"""Swarms (Python) benchmark: Agent, SequentialWorkflow and ConcurrentWorkflow."""

import time

from common import BASE_URL, MAX_TOKENS, MODEL, Recorder, agent_specs, parse_args, task_for

t0 = time.perf_counter()
from importlib.metadata import version  # noqa: E402

from swarms import Agent, ConcurrentWorkflow, SequentialWorkflow  # noqa: E402

import_s = time.perf_counter() - t0


def make_agent(spec: dict) -> Agent:
    return Agent(
        agent_name=spec["name"],
        system_prompt=spec["system_prompt"],
        # The provider prefix routes through LiteLLM's Anthropic client.
        model_name=f"anthropic/{MODEL}",
        llm_base_url=BASE_URL,
        max_loops=1,
        max_tokens=MAX_TOKENS,
        # Sonnet 5.5 rejects temperature; None keeps it out of the request.
        temperature=None,
        output_type="final",
        print_on=False,
        verbose=False,
        autosave=False,
    )


def main() -> None:
    args = parse_args()
    rec = Recorder("swarms-python", version("swarms"), args, import_s)
    specs = agent_specs(args.scenario, args.n_agents)
    task = task_for(args.scenario)

    if args.scenario in ("coldstart", "single"):
        rec.measure(lambda: make_agent(specs[0]), lambda agent: agent.run(task))
    elif args.scenario == "sequential":
        rec.measure(
            lambda: SequentialWorkflow(
                agents=[make_agent(s) for s in specs], output_type="final", autosave=False
            ),
            lambda wf: wf.run(task),
        )
    else:
        rec.measure(
            lambda: ConcurrentWorkflow(
                agents=[make_agent(s) for s in specs], output_type="dict-all-except-first", autosave=False
            ),
            lambda wf: wf.run(task),
        )
    rec.write()


if __name__ == "__main__":
    main()
