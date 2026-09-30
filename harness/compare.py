"""Aggregate results/<suite>/runs into comparison tables and charts.

  python harness/compare.py

Writes:
  results/<suite>/runs.csv        one row per framework process
  comparisons/*.csv               aggregated metrics per framework
  comparisons/charts/*.png        charts
  comparisons/prompts.md          the first request each framework sends to Claude
  comparisons/SUMMARY.md          every table plus the charts
"""

import csv
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = ROOT / "comparisons"
CHARTS = OUT / "charts"

ORDER = ["swarms-rs", "swarms-python", "langgraph", "crewai"]
# Categorical slots 1-4 of the validated reference palette, fixed per framework.
COLORS = {"swarms-rs": "#2a78d6", "swarms-python": "#eb6834", "langgraph": "#1baf7a", "crewai": "#eda100"}
LABELS = {"swarms-rs": "swarms-rs", "swarms-python": "Swarms (Python)", "langgraph": "LangGraph", "crewai": "CrewAI"}
SURFACE, INK, INK_2, MUTED, GRID, BASELINE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": INK_2,
    "axes.titlecolor": INK,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "xtick.color": MUTED,
    "ytick.color": INK_2,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
})


# ---------------------------------------------------------------- loading

def load_runs(suite: str) -> list[dict]:
    runs_dir = RESULTS / suite / "runs"
    return [json.loads(p.read_text()) for p in sorted(runs_dir.glob("*.json"))] if runs_dir.exists() else []


def select(runs: list[dict], fw: str, scenario: str, n: int | None = None) -> list[dict]:
    return [r for r in runs if r["framework"] == fw and r["scenario"] == scenario
            and (n is None or r["n_agents"] == n)]


def iter_times(runs: list[dict]) -> list[float]:
    return [i["run_s"] for r in runs for i in (r["bench"] or {}).get("runs", []) if i.get("run_s") is not None]


def ok_rate(runs: list[dict]) -> str:
    iters = [i for r in runs for i in (r["bench"] or {}).get("runs", [])]
    failed = sum(1 for r in runs if r["bench"] is None or r["exit_code"] != 0)
    good = sum(1 for i in iters if i["ok"])
    return f"{good}/{len(iters) + failed}"


def api_busy_s(calls: list[dict]) -> float:
    """Wall time with at least one request in flight at the proxy (union of intervals)."""
    spans = sorted((c["t_start"], c["t_end"]) for c in calls if c.get("t_end") is not None)
    busy, cur_start, cur_end = 0.0, None, None
    for start, end in spans:
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                busy += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        busy += cur_end - cur_start
    return busy


def peak_in_flight(calls: list[dict]) -> int:
    """Most requests the proxy was serving at once, i.e. the concurrency actually achieved."""
    events = sorted([(c["t_start"], 1) for c in calls] + [(c["t_end"], -1) for c in calls if c.get("t_end")])
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def med(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def fmt(x, digits=0, suffix=""):
    if x is None:
        return "—"
    return f"{x:,.{digits}f}{suffix}"


def write_csv(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(OUT / name, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def md_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    head = "| " + " | ".join(label for label, _ in columns) + " |"
    sep = "|" + "|".join("---" if i == 0 else "---:" for i in range(len(columns))) + "|"
    body = ["| " + " | ".join(str(r[key]) for _, key in columns) + " |" for r in rows]
    return "\n".join([head, sep, *body])


# ---------------------------------------------------------------- charts

def hbar(ax, values: dict[str, float | None], title: str, label, whiskers: dict | None = None) -> None:
    fws = [fw for fw in ORDER if values.get(fw) is not None]
    ys = list(range(len(fws)))[::-1]
    vals = [values[fw] for fw in fws]
    ax.barh(ys, vals, height=0.52, color=[COLORS[fw] for fw in fws], zorder=2)
    top = max(vals + [hi for _, hi in (whiskers or {}).values()]) if vals else 1
    if whiskers:
        for y, fw in zip(ys, fws):
            lo, hi = whiskers[fw]
            ax.plot([lo, hi], [y, y], color=INK_2, lw=1, zorder=3)
            ax.plot([lo, lo], [y - 0.12, y + 0.12], color=INK_2, lw=1, zorder=3)
            ax.plot([hi, hi], [y - 0.12, y + 0.12], color=INK_2, lw=1, zorder=3)
    for y, fw, v in zip(ys, fws, vals):
        anchor = max(v, whiskers[fw][1]) if whiskers else v
        ax.text(anchor + top * 0.015, y, label(v), va="center", ha="left", color=INK, fontsize=9)
    ax.set_yticks(ys, [LABELS[fw] for fw in fws])
    ax.set_xlim(0, top * 1.38)
    ax.set_title(title, pad=10)
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)


def save(fig, name: str, note: str | None = None, note_y: float = -0.02) -> str:
    if note:
        fig.text(0.01, note_y, note, color=MUTED, fontsize=8, ha="left", va="top")
    path = CHARTS / name
    fig.savefig(path)
    plt.close(fig)
    return f"charts/{name}"


# ---------------------------------------------------------------- mock suite

def mock_section(runs: list[dict], md: list[str]) -> None:
    if not runs:
        return
    manifest = json.loads((RESULTS / "mock" / "manifest.json").read_text())
    md.append("## Framework overhead (mock Anthropic API)\n")
    md.append("Every framework talks to a local mock of the Messages API that answers instantly "
              "(or after a fixed 500 ms for the concurrency test), so these numbers are the frameworks' own cost: "
              "startup, memory, per-call orchestration and concurrency. No model or network variance.\n")

    # Cold start
    rows = []
    for fw in ORDER:
        rs = select(runs, fw, "coldstart")
        if not rs:
            continue
        rows.append({
            "framework": fw,
            "version": rs[0]["bench"]["version"] if rs[0]["bench"] else "",
            "startup_ms_median": round(med([r["process_wall_s"] for r in rs]) * 1000, 1),
            "startup_ms_min": round(min(r["process_wall_s"] for r in rs) * 1000, 1),
            "startup_ms_max": round(max(r["process_wall_s"] for r in rs) * 1000, 1),
            "import_ms_median": round(med([r["bench"]["import_s"] for r in rs if r["bench"]]) * 1000, 1),
            "agent_build_ms_median": round(med([r["bench"]["runs"][0]["build_s"] for r in rs if r["bench"]]) * 1000, 3),
            "peak_rss_mb_median": round(med([r["max_rss_mb"] for r in rs if r["max_rss_mb"]]), 1),
            "processes": len(rs),
        })
    write_csv("mock_coldstart.csv", rows)
    by = {r["framework"]: r for r in rows}
    fig, axes = plt.subplots(1, 2, figsize=(11, 2.8), gridspec_kw={"wspace": 0.55})
    hbar(axes[0], {fw: by[fw]["startup_ms_median"] for fw in by}, "Cold start: launch → 1 agent built (ms)",
         lambda v: f"{v:,.0f} ms", {fw: (by[fw]["startup_ms_min"], by[fw]["startup_ms_max"]) for fw in by})
    hbar(axes[1], {fw: by[fw]["peak_rss_mb_median"] for fw in by}, "Peak memory at cold start (MB)",
         lambda v: f"{v:,.1f} MB" if v < 10 else f"{v:,.0f} MB")
    chart = save(fig, "mock_coldstart_memory.png",
                 f"Median of {rows[0]['processes']} process launches each; whiskers show min–max. Lower is better.")
    md.append("### Cold start and memory\n")
    md.append(f"![cold start]({chart})\n")
    md.append(md_table([{**r, "startup": f"{fmt(r['startup_ms_median'], 0)} ms",
                         "imp": f"{fmt(r['import_ms_median'], 0)} ms",
                         "build": f"{fmt(r['agent_build_ms_median'], 2)} ms",
                         "rss": f"{fmt(r['peak_rss_mb_median'], 1)} MB"} for r in rows],
                       [("Framework", "framework"), ("Version", "version"), ("Process start → agent ready", "startup"),
                        ("Framework import", "imp"), ("Agent construction", "build"), ("Peak RSS", "rss")]))
    md.append("")

    # Per-call overhead
    rows = []
    for fw in ORDER:
        single, seq = select(runs, fw, "single"), select(runs, fw, "sequential")
        if not single:
            continue
        s_times, q_times = iter_times(single), iter_times(seq)
        s_iters = single[0]["bench"]["runs"] if single[0]["bench"] else []
        q_iters = seq[0]["bench"]["runs"] if seq and seq[0]["bench"] else []
        seq_calls = seq[0]["http_calls"] if seq else []
        rows.append({
            "framework": fw,
            "single_first_call_ms": round(s_times[0] * 1000, 2) if s_times else None,
            "single_warm_median_ms": round(med(s_times[1:]) * 1000, 3) if len(s_times) > 1 else None,
            "single_warm_p95_ms": round(sorted(s_times[1:])[int(0.95 * (len(s_times) - 2))] * 1000, 3) if len(s_times) > 2 else None,
            "single_http_calls_per_run": round(single[0]["http"]["calls"] / max(1, len(s_iters)), 2),
            "single_request_bytes": single[0]["http_calls"][0]["req_bytes"] if single[0]["http_calls"] else None,
            "sequential_warm_median_ms": round(med(q_times[1:]) * 1000, 3) if len(q_times) > 1 else None,
            "sequential_http_calls_per_run": round(seq[0]["http"]["calls"] / max(1, len(q_iters)), 2) if seq else None,
            "sequential_stage_request_bytes": "/".join(str(c["req_bytes"]) for c in seq_calls[:3]),
            "iterations": len(s_times),
        })
    write_csv("mock_call_overhead.csv", rows)
    by = {r["framework"]: r for r in rows}
    fig, axes = plt.subplots(1, 2, figsize=(11, 2.8), gridspec_kw={"wspace": 0.55})
    hbar(axes[0], {fw: by[fw]["single_warm_median_ms"] for fw in by}, "Single agent: framework time per call (ms)",
         lambda v: f"{v:,.2f} ms")
    hbar(axes[1], {fw: by[fw]["sequential_warm_median_ms"] for fw in by}, "3-agent sequential pipeline (ms)",
         lambda v: f"{v:,.2f} ms")
    chart = save(fig, "mock_call_overhead.png",
                 "Mock API answers instantly, so this is time spent inside the framework plus one localhost round trip "
                 "per call. Warm median of the iterations after the first. Lower is better.")
    md.append("### Orchestration overhead per call\n")
    md.append(f"![call overhead]({chart})\n")
    md.append(md_table([{**r, "first": f"{fmt(r['single_first_call_ms'], 2)} ms",
                         "warm": f"{fmt(r['single_warm_median_ms'], 2)} ms",
                         "p95": f"{fmt(r['single_warm_p95_ms'], 2)} ms",
                         "seq": f"{fmt(r['sequential_warm_median_ms'], 2)} ms"} for r in rows],
                       [("Framework", "framework"), ("First call", "first"), ("Warm median", "warm"), ("Warm p95", "p95"),
                        ("HTTP calls / agent run", "single_http_calls_per_run"), ("Request bytes (single)", "single_request_bytes"),
                        ("3-agent pipeline", "seq"), ("Request bytes per stage", "sequential_stage_request_bytes")]))
    md.append("")

    # Concurrency scaling
    delay = next((s["delay_ms"] for s in manifest["plan"] if s["scenario"] == "concurrent"), 500)
    ns = sorted({r["n_agents"] for r in runs if r["scenario"] == "concurrent"})
    rows = []
    for fw in ORDER:
        for n in ns:
            rs = select(runs, fw, "concurrent", n)
            if not rs:
                continue
            t = med(iter_times(rs))
            rows.append({
                "framework": fw, "agents": n,
                "wall_s_median": round(t, 3) if t else None,
                "ideal_s": delay / 1000,
                "parallel_efficiency_pct": round(100 * (delay / 1000) / t, 1) if t else None,
                "peak_in_flight_requests": peak_in_flight(rs[0]["http_calls"]),
                "peak_rss_mb": round(rs[0]["max_rss_mb"], 1) if rs[0]["max_rss_mb"] else None,
                "http_calls": rs[0]["http"]["calls"],
                "ok": ok_rate(rs),
            })
    write_csv("mock_concurrency.csv", rows)
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.4), gridspec_kw={"wspace": 0.4})
    ax = axes[0]
    for fw in ORDER:
        pts = [(r["agents"], r["wall_s_median"]) for r in rows if r["framework"] == fw and r["wall_s_median"]]
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=COLORS[fw], lw=2, marker="o", ms=6, mec=SURFACE, mew=2, label=LABELS[fw], zorder=3,
                solid_capstyle="round")
    ax.axhline(delay / 1000, color=MUTED, lw=1, zorder=2)
    ax.text(ns[-1], delay / 1000 - 0.06, f"ideal: {delay} ms per call ", va="top", ha="right", color=MUTED, fontsize=8)
    ax.set_xticks(ns)
    ax.set_xlabel("agents running concurrently")
    ax.set_ylim(bottom=0)
    ax.set_title("Concurrent fan-out: wall time (s)", pad=10)
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK_2)
    last = {r["framework"]: r for r in rows if r["agents"] == ns[-1]}
    hbar(axes[1], {fw: last[fw]["peak_rss_mb"] for fw in last}, f"Peak memory with {ns[-1]} agents (MB)",
         lambda v: f"{v:,.1f} MB" if v < 10 else f"{v:,.0f} MB")
    chart = save(fig, "mock_concurrency.png",
                 f"Each mock call takes {delay} ms, so perfect concurrency finishes in {delay} ms at any agent count. "
                 "Median of 5 runs. Lower is better.", note_y=-0.08)
    md.append("### Concurrency scaling\n")
    md.append(f"![concurrency]({chart})\n")
    md.append(md_table([{**r, "wall": f"{fmt(r['wall_s_median'], 3)} s", "eff": f"{fmt(r['parallel_efficiency_pct'], 0)}%",
                         "rss": f"{fmt(r['peak_rss_mb'], 0)} MB"} for r in rows],
                       [("Framework", "framework"), ("Agents", "agents"), ("Wall time", "wall"),
                        ("Parallel efficiency", "eff"), ("Peak in-flight requests", "peak_in_flight_requests"),
                        ("Peak RSS", "rss"), ("HTTP calls", "http_calls"),
                        ("Successful runs", "ok")]))
    md.append("")


# ---------------------------------------------------------------- live suite

def live_section(runs: list[dict], md: list[str]) -> None:
    if not runs:
        return
    manifest = json.loads((RESULTS / "live" / "manifest.json").read_text())
    md.append(f"## Live runs on `{manifest['model']}`\n")
    md.append("Real Anthropic API calls through a recording pass-through proxy. Latency here is dominated by the "
              "model, so the useful comparisons are framework time outside the API, tokens sent, and HTTP calls. "
              "Framework processes were interleaved round-robin to spread API latency drift evenly.\n")

    scenarios = [("single", "Single agent"), ("sequential", "3-agent sequential"), ("concurrent", "4-agent concurrent")]
    rows = []
    for scenario, _ in scenarios:
        for fw in ORDER:
            rs = select(runs, fw, scenario)
            if not rs:
                continue
            times = iter_times(rs)
            overhead = [i["run_s"] - api_busy_s(r["http_calls"])
                        for r in rs for i in (r["bench"] or {}).get("runs", []) if i.get("run_s") is not None]
            calls = [c for r in rs for c in r["http_calls"]]
            n = len(rs)
            rows.append({
                "scenario": scenario, "framework": fw,
                "latency_s_median": round(med(times), 2) if times else None,
                "latency_s_min": round(min(times), 2) if times else None,
                "latency_s_max": round(max(times), 2) if times else None,
                "framework_overhead_ms_median": round(med(overhead) * 1000, 1) if overhead else None,
                "http_calls_per_run": round(len(calls) / n, 2),
                # Anthropic's input_tokens excludes cached tokens, so the full prompt is the sum of all three.
                "prompt_tokens_per_run": round(sum((c.get("input_tokens") or 0) + (c.get("cache_creation_input_tokens") or 0)
                                                   + (c.get("cache_read_input_tokens") or 0) for c in calls) / n),
                "cache_write_tokens_per_run": round(sum(c.get("cache_creation_input_tokens") or 0 for c in calls) / n),
                "cache_read_tokens_per_run": round(sum(c.get("cache_read_input_tokens") or 0 for c in calls) / n),
                "output_tokens_per_run": round(sum(c.get("output_tokens") or 0 for c in calls) / n),
                "thinking_tokens_per_run": round(sum(c.get("thinking_tokens") or 0 for c in calls) / n),
                "peak_rss_mb_median": round(med([r["max_rss_mb"] for r in rs if r["max_rss_mb"]]), 1),
                "errors": sum(r["http"]["errors"] for r in rs),
                "ok": ok_rate(rs),
            })
    write_csv("live_results.csv", rows)

    def by(scenario: str, key: str) -> dict:
        return {r["framework"]: r[key] for r in rows if r["scenario"] == scenario}

    fig, axes = plt.subplots(1, 3, figsize=(15, 2.9), gridspec_kw={"wspace": 0.6})
    for ax, (scenario, title) in zip(axes, scenarios):
        whisk = {r["framework"]: (r["latency_s_min"], r["latency_s_max"]) for r in rows if r["scenario"] == scenario}
        hbar(ax, by(scenario, "latency_s_median"), f"{title} (s)", lambda v: f"{v:.1f} s", whisk)
    lat_chart = save(fig, "live_latency.png", "End-to-end wall time per workflow run; median with min–max whiskers. "
                                               "Dominated by model generation time.")

    fig, axes = plt.subplots(1, 3, figsize=(15, 2.9), gridspec_kw={"wspace": 0.6})
    for ax, (scenario, title) in zip(axes, scenarios):
        hbar(ax, by(scenario, "framework_overhead_ms_median"), f"{title}: framework time (ms)", lambda v: f"{v:,.0f} ms")
    oh_chart = save(fig, "live_framework_overhead.png",
                    "Run wall time minus the time a request to Claude was in flight at the proxy. Lower is better.")

    fig, axes = plt.subplots(1, 3, figsize=(15, 2.9), gridspec_kw={"wspace": 0.6})
    for ax, (scenario, title) in zip(axes, scenarios):
        hbar(ax, by(scenario, "prompt_tokens_per_run"), f"{title}: prompt tokens", lambda v: f"{v:,.0f}")
    tok_chart = save(fig, "live_input_tokens.png",
                     "Prompt tokens per workflow run from API usage (input + cache write + cache read). Differences come from each "
                     "framework's prompt scaffolding and how it hands context between agents.")

    md.append(f"![live latency]({lat_chart})\n")
    md.append(f"![framework overhead]({oh_chart})\n")
    md.append(f"![input tokens]({tok_chart})\n")
    md.append(md_table([{**r, "lat": f"{fmt(r['latency_s_median'], 2)} s ({fmt(r['latency_s_min'], 1)}–{fmt(r['latency_s_max'], 1)})",
                         "oh": f"{fmt(r['framework_overhead_ms_median'], 0)} ms",
                         "rss": f"{fmt(r['peak_rss_mb_median'], 0)} MB"} for r in rows],
                       [("Scenario", "scenario"), ("Framework", "framework"), ("Latency median (min–max)", "lat"),
                        ("Framework time", "oh"), ("HTTP calls", "http_calls_per_run"), ("Prompt tok", "prompt_tokens_per_run"), ("of which cache write", "cache_write_tokens_per_run"),
                        ("Output tok", "output_tokens_per_run"), ("Thinking tok", "thinking_tokens_per_run"),
                        ("Peak RSS", "rss"), ("OK", "ok")]))
    md.append("")


# ---------------------------------------------------------------- prompts

def prompts_doc() -> None:
    suite = "live" if (RESULTS / "live" / "requests").exists() else "mock"
    req_dir = RESULTS / suite / "requests"
    lines = ["# What each framework sends to Claude\n",
             f"First request body captured by the proxy ({suite} suite) for the same single-agent task and the "
             "final stage of the 3-agent pipeline. Same system prompt and task in every framework; "
             "everything else is added by the framework.\n"]
    for scenario, stage in (("single__n1", 0), ("sequential__n3", 2)):
        lines.append(f"## {'Single agent' if stage == 0 else 'Sequential pipeline, stage 3 (Writer)'}\n")
        for fw in ORDER:
            path = req_dir / f"{fw}__{scenario}.json"
            if not path.exists():
                continue
            bodies = json.loads(path.read_text())
            if len(bodies) <= stage:
                continue
            body = bodies[stage]
            lines.append(f"### {fw}\n")
            lines.append("```json\n" + json.dumps(body, indent=2, ensure_ascii=False)[:6000] + "\n```\n")
    (OUT / "prompts.md").write_text("\n".join(lines))


# ---------------------------------------------------------------- main

def runs_csv(suite: str, runs: list[dict]) -> None:
    if not runs:
        return
    with open(RESULTS / suite / "runs.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["run_id", "framework", "scenario", "n_agents", "rep", "iteration", "ok", "build_s", "run_s",
                         "process_wall_s", "max_rss_mb", "http_calls", "input_tokens", "output_tokens", "error"])
        for r in runs:
            for i, it in enumerate((r["bench"] or {}).get("runs", []) or [{}]):
                writer.writerow([r["run_id"], r["framework"], r["scenario"], r["n_agents"], r["rep"], i, it.get("ok"),
                                 it.get("build_s"), it.get("run_s"), r["process_wall_s"], r["max_rss_mb"],
                                 r["http"]["calls"], r["http"]["input_tokens"], r["http"]["output_tokens"],
                                 (it.get("error") or "")[-200:]])


def main() -> None:
    # A suite without a manifest is still running (run.py writes it last), so leave it out.
    mock, live = (load_runs(s) if (RESULTS / s / "manifest.json").exists() else [] for s in ("mock", "live"))
    if not mock and not live:
        # results/ isn't committed; without it, rebuilding would replace the published report with an empty one.
        raise SystemExit("No results found. Run `harness/run.py --suite mock` and/or `--suite live` first.")
    CHARTS.mkdir(parents=True, exist_ok=True)
    runs_csv("mock", mock)
    runs_csv("live", live)

    md = ["# Benchmark comparison\n", "Generated by `harness/compare.py` from `results/`. "
          "Frameworks: swarms-rs, Swarms (Python), LangGraph, CrewAI.\n"]
    for suite in ("mock", "live"):
        manifest_path = RESULTS / suite / "manifest.json"
        if manifest_path.exists():
            m = json.loads(manifest_path.read_text())
            mach = m["machine"]
            md.append(f"- **{suite}**: {mach['date_utc']}, {mach['cpu']} ({mach['cpu_cores']} cores, "
                      f"{mach['memory_gb']} GB), {mach['os']}, {mach['python']}, {mach['rustc']}; "
                      f"model `{m['model']}`, max_tokens {m['max_tokens']}")
    md.append("")
    live_section(live, md)
    mock_section(mock, md)
    (OUT / "SUMMARY.md").write_text("\n".join(md))
    prompts_doc()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
