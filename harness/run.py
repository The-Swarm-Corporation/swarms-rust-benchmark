"""Run a benchmark suite and write raw results to results/<suite>/.

  python harness/run.py --suite mock   # framework overhead against a local mock API (free)
  python harness/run.py --suite live   # real Claude calls through a recording proxy (needs ANTHROPIC_API_KEY)

Every framework process is wrapped in /usr/bin/time -l for peak RSS, and all
LLM traffic goes through harness/proxy.py so HTTP calls, tokens and request
sizes are attributed to the run that produced them.
"""

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENVS = ROOT / ".venvs"
RUST_BIN = ROOT / "rust" / "target" / "release" / "swarms-rs-bench"

FRAMEWORKS = {
    "swarms-rs": [str(RUST_BIN)],
    "swarms-python": [str(VENVS / "swarms/bin/python"), str(ROOT / "python/bench_swarms.py")],
    "langgraph": [str(VENVS / "langgraph/bin/python"), str(ROOT / "python/bench_langgraph.py")],
    "crewai": [str(VENVS / "crewai/bin/python"), str(ROOT / "python/bench_crewai.py")],
}


@dataclass
class Step:
    scenario: str
    n_agents: int
    iterations: int  # iterations inside one process
    processes: int  # separate process launches, interleaved across frameworks
    delay_ms: int | None = None  # mock response delay


PLANS = {
    "mock": [
        Step("coldstart", 1, 1, 10),
        Step("single", 1, 30, 1, delay_ms=0),
        Step("sequential", 3, 10, 1, delay_ms=0),
        Step("concurrent", 10, 5, 1, delay_ms=500),
        Step("concurrent", 50, 5, 1, delay_ms=500),
        Step("concurrent", 100, 5, 1, delay_ms=500),
    ],
    "live": [
        Step("single", 1, 1, 5),
        Step("sequential", 3, 1, 3),
        Step("concurrent", 4, 1, 3),
    ],
}

RSS_RE = re.compile(r"(\d+)\s+maximum resident set size")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def load_api_key() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    env_file = ROOT / ".env"
    for line in env_file.read_text().splitlines() if env_file.exists() else []:
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit("ANTHROPIC_API_KEY not found in the environment or in .env")


def machine_info() -> dict:
    def sh(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        except OSError:
            return ""

    return {
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": sh(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor(),
        "cpu_cores": os.cpu_count(),
        "memory_gb": round(int(sh(["sysctl", "-n", "hw.memsize"]) or 0) / 2**30, 1),
        "python": sh([str(VENVS / "swarms/bin/python"), "--version"]),
        "rustc": sh(["rustc", "--version"]),
    }


def summarize_http(records: list[dict]) -> dict:
    def total(key: str) -> int:
        return sum(r.get(key) or 0 for r in records)

    return {
        "calls": len(records),
        "errors": sum(1 for r in records if r.get("status", 200) >= 400),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "thinking_tokens": total("thinking_tokens"),
        "cache_read_input_tokens": total("cache_read_input_tokens"),
        "req_bytes": total("req_bytes"),
        "temperature_sent": any(r.get("temperature_sent") for r in records),
    }


def run_process(suite: str, fw: str, step: Step, rep: int, proxy: str, env: dict, out_dir: Path) -> dict:
    run_id = f"{fw}__{step.scenario}__n{step.n_agents}__rep{rep}"
    http(f"{proxy}/__control/run", {"run_id": run_id, "delay_ms": step.delay_ms})
    bench_out = out_dir / "tmp" / f"{run_id}.json"
    log_path = out_dir / "logs" / f"{run_id}.log"
    cmd = ["/usr/bin/time", "-l", *FRAMEWORKS[fw], "--scenario", step.scenario,
           "--iterations", str(step.iterations), "--n-agents", str(step.n_agents), "--out", str(bench_out)]

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, env=env, cwd=out_dir / "tmp", capture_output=True, text=True)
    wall = time.perf_counter() - t0
    log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)

    rss = RSS_RE.search(proc.stderr)
    traffic = http(f"{proxy}/__control/records?run_id={run_id}")
    bench = json.loads(bench_out.read_text()) if bench_out.exists() else None
    result = {
        "run_id": run_id,
        "suite": suite,
        "framework": fw,
        "rep": rep,
        **asdict(step),
        "exit_code": proc.returncode,
        "process_wall_s": wall,
        "max_rss_mb": int(rss.group(1)) / 2**20 if rss else None,
        "bench": bench,
        "http": summarize_http(traffic["records"]),
        "http_calls": traffic["records"],
    }
    (out_dir / "runs" / f"{run_id}.json").write_text(json.dumps(result, indent=2))
    if rep == 0 and traffic["bodies"]:
        (out_dir / "requests" / f"{fw}__{step.scenario}__n{step.n_agents}.json").write_text(
            json.dumps(traffic["bodies"], indent=2))

    ok = bench is not None and all(r["ok"] for r in bench["runs"]) and proc.returncode == 0
    runs = [r["run_s"] for r in (bench or {}).get("runs", []) if r.get("run_s") is not None]
    detail = f"run median {sorted(runs)[len(runs) // 2]:.3f}s" if runs else f"wall {wall:.2f}s"
    print(f"  {'ok ' if ok else 'ERR'} {run_id:<48} {detail}  calls={result['http']['calls']}", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=PLANS, required=True)
    parser.add_argument("--frameworks", nargs="*", default=list(FRAMEWORKS))
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()

    out_dir = ROOT / "results" / args.suite
    for sub in ("runs", "logs", "tmp", "requests"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
        for stale in (out_dir / sub).glob("*"):
            stale.unlink()

    print("building swarms-rs benchmark (release)...", flush=True)
    subprocess.run(["cargo", "build", "--release", "--quiet"], cwd=ROOT / "rust", check=True)

    port = free_port()
    proxy_url = f"http://127.0.0.1:{port}"
    proxy = subprocess.Popen(
        [str(VENVS / "harness/bin/python"), str(ROOT / "harness/proxy.py"), "--mode", args.suite, "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=open(out_dir / "logs" / "proxy.log", "w"))
    for _ in range(50):
        try:
            http(f"{proxy_url}/__control/health")
            break
        except OSError:
            time.sleep(0.1)

    env = {
        **os.environ,
        "BENCH_BASE_URL": proxy_url,
        "BENCH_MODEL": args.model,
        "BENCH_MAX_TOKENS": str(args.max_tokens),
        # Mock runs never need a real key; this keeps it out of those processes entirely.
        "ANTHROPIC_API_KEY": load_api_key() if args.suite == "live" else "sk-ant-mock",
        # Telemetry off everywhere so no framework makes calls the others don't.
        "SWARMS_TELEMETRY_ON": "false",
        "CREWAI_DISABLE_TELEMETRY": "true",
        "CREWAI_TRACING_ENABLED": "false",
        "OTEL_SDK_DISABLED": "true",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
        "PYTHONUNBUFFERED": "1",
    }

    manifest = {"suite": args.suite, "model": args.model, "max_tokens": args.max_tokens,
                "frameworks": args.frameworks, "machine": machine_info(),
                "plan": [asdict(s) for s in PLANS[args.suite]]}
    try:
        # Warm-up launch per framework so bytecode caches exist before anything is timed.
        warm = Step("coldstart", 1, 1, 1)
        print("warm-up", flush=True)
        for fw in args.frameworks:
            run_process(args.suite, fw, warm, -1, proxy_url, env, out_dir)
        for stale in (out_dir / "runs").glob("*rep-1.json"):
            stale.unlink()

        started = time.perf_counter()
        for step in PLANS[args.suite]:
            print(f"{step.scenario} n={step.n_agents} x{step.processes}", flush=True)
            for rep in range(step.processes):
                # Rotate the order each rep so no framework always runs first after a pause.
                order = args.frameworks[rep % len(args.frameworks):] + args.frameworks[:rep % len(args.frameworks)]
                for fw in order:
                    run_process(args.suite, fw, step, rep, proxy_url, env, out_dir)
        manifest["duration_s"] = round(time.perf_counter() - started, 1)
    finally:
        proxy.terminate()
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"done in {manifest.get('duration_s', '?')}s -> {out_dir}")


if __name__ == "__main__":
    main()
