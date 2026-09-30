# What each framework sends to Claude

First request body captured by the proxy (live suite) for the same single-agent task and the final stage of the 3-agent pipeline. Same system prompt and task in every framework; everything else is added by the framework.

## Single agent

### swarms-rs

```json
{
  "model": "claude-sonnet-5-5",
  "max_tokens": 4096,
  "system": "You are a senior systems engineer. Answer precisely and concisely in at most 150 words.",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "User: Timestamp(millis): 1790784946689 \nExplain the difference between a mutex and a semaphore, and give one situation where each is the right choice."
        }
      ]
    }
  ]
}
```

### swarms-python

```json
{
  "model": "claude-sonnet-5-5",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Explain the difference between a mutex and a semaphore, and give one situation where each is the right choice."
        }
      ]
    }
  ],
  "max_tokens": 4096,
  "system": [
    {
      "type": "text",
      "text": "You are a senior systems engineer. Answer precisely and concisely in at most 150 words."
    }
  ]
}
```

### langgraph

```json
{
  "max_tokens": 4096,
  "messages": [
    {
      "role": "user",
      "content": "Explain the difference between a mutex and a semaphore, and give one situation where each is the right choice."
    }
  ],
  "model": "claude-sonnet-5-5",
  "system": "You are a senior systems engineer. Answer precisely and concisely in at most 150 words."
}
```

### crewai

```json
{
  "max_tokens": 4096,
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "\nCurrent Task: Explain the difference between a mutex and a semaphore, and give one situation where each is the right choice.\n\nThis is the expected criteria for your final answer: A concise, well-reasoned response of at most 150 words.\nyou MUST return the actual complete content as the final answer, not a summary.\n\nProvide your complete response:",
          "cache_control": {
            "type": "ephemeral"
          }
        }
      ]
    }
  ],
  "model": "claude-sonnet-5-5",
  "stop_sequences": [
    "\nObservation:"
  ],
  "stream": false,
  "system": [
    {
      "type": "text",
      "text": "You are Systems-Engineer. You are a senior systems engineer. Answer precisely and concisely in at most 150 words.\nYour personal goal is: Deliver the Systems-Engineer contribution to the task.",
      "cache_control": {
        "type": "ephemeral"
      }
    }
  ]
}
```

## Sequential pipeline, stage 3 (Writer)

### swarms-rs

```json
{
  "model": "claude-sonnet-5-5",
  "max_tokens": 4096,
  "system": "You are a technical writer. Turn the analysis you are given into a crisp executive summary for a CTO. Use at most 150 words.",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "User: Timestamp(millis): 1790785076855 \n[From Agent] Analyst:\nUser(User): Timestamp(millis): 1790785073199 \n[From Agent] Researcher:\nUser(User): Timestamp(millis): 1790785068136 \nAssess whether a team should rewrite its latency-sensitive order-routing gateway from Python to Rust.\nResearcher(Assistant): Timestamp(millis): 1790785073199 \n**Key facts**\n- Python (CPython) has GIL limits, GC/refcount overhead, and latency jitter. Rust gives no GC, predictable tail latency (p99/p999), and typically 10–100x CPU-bound speedups.\n- Many gateways are I/O- or network-bound (exchange RTT, venue links), so language gains may be marginal.\n\n**Constraints**\n- Hiring/skills: Rust talent is scarcer; learning curve is steep.\n- Rewrite risk: behavioral drift, lost edge-case logic, regulatory/compliance revalidation, and a long parallel-run period.\n- Ecosystem: FIX, exchange SDKs, and risk libraries may be Python/C++-centric.\n\n**Trade-offs**\n- Cheaper alternatives first: profile; use asyncio/uvloop, PyPy, Cython, or PyO3 to move only hot paths to Rust; fix GC pauses, serialization, and kernel/network tuning.\n- Full rewrite pays off if: measured p99 misses SLAs, CPU-bound on the hot path, or scaling costs are high.\n- Incremental strangler-pattern migration lowers risk versus a big-bang rewrite.\n\n**Recommendation:** Profile and set explicit latency targets first; default to a targeted hot-path Rust extraction unless the data justify a full rewrite.\n\nAnalyst(Assistant): Timestamp(millis): 1790785076855 \n**Recommendation: Don't do a full rewrite now. Profile first, then extract only the hot paths to Rust.**\n\n**Why:**\n- Order-routing gateways are often bound by network and exchange round-trip time, so Rust's CPU speedups may barely move end-to-end latency.\n- A full rewrite risks behavioral drift, lost edge-case logic, compliance revalidation, and a long parallel-run period. Rust hiring adds further strain.\n- Cheaper fixes often close the gap: uvloop, GC tuning, faster serialization, kernel/network tuning, and Cython or PyO3 for hot loops.\n\n**Decision path:**\n1. Set explicit p99/p999 targets.\n2. Profile under production-like load.\n3. If the gaps are in a few CPU-bound functions, extract them to Rust via PyO3.\n4. If p99 still misses SLA after that, or scaling costs are high, migrate incrementally with the strangler pattern and shadow traffic.\n\n**Full rewrite is justified only if** measurements show Python runtime jitter (GIL, GC) is the dominant tail-latency source and extraction can't resolve it.\n"
        }
      ]
    }
  ]
}
```

### swarms-python

```json
{
  "model": "claude-sonnet-5-5",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Assess whether a team should rewrite its latency-sensitive order-routing gateway from Python to Rust."
        },
        {
          "type": "text",
          "text": "Researcher: **Key facts**\n- Python (CPython) has GIL limits, GC/refcount overhead, and typically tens-to-hundreds of µs jitter. Rust offers no GC, predictable tail latency, and often 10–100x CPU-bound speedups.\n- Order routing usually spends most time in network I/O, exchange round trips, and risk checks. Language may not be the dominant bottleneck.\n- Python's speed-ups are possible: asyncio/uvloop, PyPy, Cython, C extensions, multiprocessing, and profiling-led hotspot fixes.\n\n**Constraints**\n- Team Rust skills, hiring pool, and ramp time (months).\n- Must maintain protocol parity (FIX, binary protocols), compliance, and audit trails.\n- Live-system migration risk; need shadow running and rollback.\n- Latency SLO: is it p50 or p99/p99.9? What's the current measured gap?\n\n**Trade-offs**\n- Rust: lower tail latency, memory safety, fewer runtime incidents vs. slower development, smaller ecosystem, higher upfront cost.\n- Incremental option (Rust core via PyO3, or rewriting only the hot path) reduces risk.\n\n**Recommendation basis:** profile first; rewrite only if measured tail latency misses the SLO after Python optimization."
        },
        {
          "type": "text",
          "text": "Analyst: **Recommendation: Don't do a full rewrite now. Profile first, then migrate incrementally if the data justifies it.**\n\n**Reasoning:** Order routing is usually dominated by network I/O, exchange round trips, and risk checks, so language overhead may be a small share of end-to-end latency. A full rewrite carries high costs: months of ramp-up, protocol parity (FIX and binary), audit and compliance requirements, and live-migration risk.\n\n**Suggested path:**\n1. Measure p50/p99/p99.9 per stage against the actual SLO.\n2. Apply cheap Python fixes: uvloop, GC tuning, hotspot removal, and Cython or C extensions.\n3. If tail latency still misses the SLO, rewrite only the hot path in Rust (PyO3 or a separate service), with shadow running and rollback.\n4. Consider a full rewrite only if most of the latency budget sits in gateway code and the team has Rust capacity.\n\n**Caveat:** If the SLO is in the low tens of microseconds, Python likely can't meet it, and Rust becomes the right choice sooner."
        }
      ]
    }
  ],
  "max_tokens": 4096,
  "system": [
    {
      "type": "text",
      "text": "You are a technical writer. Turn the analysis you are given into a crisp executive summary for a CTO. Use at most 150 words."
    },
    {
      "type": "text",
      "text": "Sequential awareness: Agent ahead: Analyst"
    }
  ]
}
```

### langgraph

```json
{
  "max_tokens": 4096,
  "messages": [
    {
      "role": "user",
      "content": "Task: Assess whether a team should rewrite its latency-sensitive order-routing gateway from Python to Rust.\n\nOutput from Analyst:\n**Recommendation: Don't do a full rewrite now. Profile first, then migrate only the proven hot path to Rust incrementally.**\n\n**Reasoning:**\n- The research can't tell us whether Python is actually the bottleneck. Many latency problems come from I/O, serialization, network hops, or architecture. A rewrite could cost months and fix none of them.\n- A full rewrite carries heavy risk: behavioral drift, re-certification with exchanges or regulators, parity testing, and lost domain knowledge. The team also lacks Rust expertise, so the 2–6 month learning curve would delay features.\n- Cheaper options (uvloop, orjson, PyO3 for hot paths, sharding, kernel tuning, colocation) may close the gap.\n\n**Exceptions that justify a full rewrite:**\n- SLOs in the microsecond range.\n- Profiling shows GC/GIL-driven p99.9 jitter that can't be fixed in place.\n- Sustained throughput growth will exceed what sharded Python can handle.\n\n**Next steps:**\n1. Profile and measure the SLO gap.\n2. Apply the cheap fixes.\n3. Build a Rust sidecar or extension for the remaining hot path using the strangler pattern.\n4. Reassess after the results."
    }
  ],
  "model": "claude-sonnet-5-5",
  "system": "You are a technical writer. Turn the analysis you are given into a crisp executive summary for a CTO. Use at most 150 words."
}
```

### crewai

```json
{
  "max_tokens": 4096,
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "\nCurrent Task: Build on the previous agent's output as the Writer for this task: Assess whether a team should rewrite its latency-sensitive order-routing gateway from Python to Rust.\n\nThis is the expected criteria for your final answer: A concise, well-reasoned response of at most 150 words.\nyou MUST return the actual complete content as the final answer, not a summary.\n\nThis is the context you're working with:\n**Key facts**\n- Python (CPython) has GIL limits, GC/allocation jitter, and interpreter overhead. Typical tail latencies are tens to hundreds of µs or worse. Rust offers predictable, no-GC performance, often 10–100× faster on CPU-bound paths.\n- In many gateways, latency is dominated by network I/O, exchange round-trips, serialization, and kernel networking, not language speed.\n\n**Constraints**\n- Team Rust expertise, hiring, and the learning curve.\n- Rewrite risk: feature parity, regression bugs, and a dual-running period.\n- Ecosystem: FIX/exchange protocol libraries, observability, and internal integrations.\n\n**Trade-offs**\n- Rust gains: p99/p999 determinism, memory safety, and lower infrastructure cost.\n- Rust costs: slower iteration, longer development time, and a harder-to-change business logic layer.\n\n**Recommendation**\n1. Profile first and set explicit p99 latency SLOs.\n2. Try cheaper fixes: asyncio/uvloop, Cython, PyO3 extensions, or pinning.\n3. Rewrite incrementally, hot path first, if the SLOs are still missed.\n4. Run shadow traffic and benchmark before cutting over.\n\nA full rewrite is justified only if profiling shows that language overhead, not I/O, is the bottleneck.\n\n----------\n\n**Recommendation: Don't commit to a full rewrite yet. Decide based on evidence.**\n\nThe prior research is sound. Rust is clearly better for tail-latency determinism, but a rewrite is expensive and often unnecessary when network I/O, exchange round-trips, and serialization dominate latency.\n\n**Decision path:**\n1. **Measure first.** Profile end-to-end and set explicit p99/p999 SLOs. Identify whether time goes to the interpreter, GC, or GIL, or to I/O.\n2. **Try cheap fixes.** Use uvloop, Cython, PyO3 extensions for the hot path, CPU pinning, and GC tuning.\n3. **Go incremental.** If the SLOs are still missed, move only the hot path (parsing, risk checks, encoding) to Rust.\n4. **De-risk cutover.** Run shadow traffic, compare outputs, and benchmark the tail before switching.\n\n**Rewrite fully only if** profiling shows language overhead is the bottleneck, the team has or can hire Rust skills, and the business logic is stable enough to port safely.\n\n**Bottom line:** An incremental, hot-path-first approach captures most of the benefit at far lower risk.\n\nProvide your complete response:",
          "cache_control": {
            "type": "ephemeral"
          }
        }
      ]
    }
  ],
  "model": "claude-sonnet-5-5",
  "stop_sequences": [
    "\nObservation:"
  ],
  "stream": false,
  "system": [
    {
      "type": "text",
      "text": "You are Writer. You are a technical writer. Turn the analysis you are given into a crisp executive summary for a CTO. Use at most 150 words.\nYour personal goal is: Deliver the Writer contribution to the task.",
      "cache_control": {
        "type": "ephemeral"
      }
    }
  ]
}
```
