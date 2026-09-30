"""Shared CLI, task definitions and result recording for the Python benchmarks.

Kept free of framework imports so each benchmark can time its own framework
import separately.
"""

import argparse
import json
import os
import time
import traceback
from pathlib import Path

TASKS = json.loads((Path(__file__).resolve().parent.parent / "tasks.json").read_text())
BASE_URL = os.environ.get("BENCH_BASE_URL", "http://127.0.0.1:8787")
MODEL = os.environ.get("BENCH_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = int(os.environ.get("BENCH_MAX_TOKENS", "4096"))
SCENARIOS = ("coldstart", "single", "sequential", "concurrent")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--n-agents", type=int, default=4)
    parser.add_argument("--out", required=True)
    return parser.parse_args()


def agent_specs(scenario: str, n_agents: int) -> list[dict]:
    """Agents for a scenario; concurrent templates repeat when n_agents exceeds them."""
    if scenario in ("coldstart", "single"):
        return [TASKS["single"]["agent"]]
    if scenario == "sequential":
        return TASKS["sequential"]["agents"]
    templates = TASKS["concurrent"]["agents"]
    if n_agents <= len(templates):
        return templates[:n_agents]
    return [
        {"name": f"{templates[i % len(templates)]['name']}-{i}",
         "system_prompt": templates[i % len(templates)]["system_prompt"]}
        for i in range(n_agents)
    ]


def task_for(scenario: str) -> str:
    return TASKS["single" if scenario == "coldstart" else scenario]["task"]


class Recorder:
    """Times build and run for each iteration and writes one JSON result."""

    def __init__(self, framework: str, version: str, args: argparse.Namespace, import_s: float):
        self.args = args
        self.result = {
            "framework": framework,
            "version": version,
            "scenario": args.scenario,
            "n_agents": len(agent_specs(args.scenario, args.n_agents)),
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "import_s": import_s,
            "runs": [],
        }

    def _record(self, build_s: float, run_s: float | None, text, error: str | None) -> None:
        text = "" if text is None else str(text)
        self.result["runs"].append({
            "build_s": build_s,
            "run_s": run_s,
            "ok": error is None,
            "error": error,
            "output_chars": len(text),
            "output_preview": text[:400],
        })

    def measure(self, build, run) -> None:
        iterations = 1 if self.args.scenario == "coldstart" else self.args.iterations
        for _ in range(iterations):
            t0 = time.perf_counter()
            target = build()
            build_s = time.perf_counter() - t0
            if self.args.scenario == "coldstart":
                self._record(build_s, None, "", None)
                continue
            t1 = time.perf_counter()
            try:
                text, error = run(target), None
            except Exception:
                text, error = None, traceback.format_exc(limit=3)[-1500:]
            self._record(build_s, time.perf_counter() - t1, text, error)

    async def ameasure(self, build, arun) -> None:
        iterations = 1 if self.args.scenario == "coldstart" else self.args.iterations
        for _ in range(iterations):
            t0 = time.perf_counter()
            target = build()
            build_s = time.perf_counter() - t0
            if self.args.scenario == "coldstart":
                self._record(build_s, None, "", None)
                continue
            t1 = time.perf_counter()
            try:
                text, error = await arun(target), None
            except Exception:
                text, error = None, traceback.format_exc(limit=3)[-1500:]
            self._record(build_s, time.perf_counter() - t1, text, error)

    def write(self) -> None:
        Path(self.args.out).write_text(json.dumps(self.result, indent=2))
