"""CrewAI benchmark: Agents + Tasks run by a Crew with CrewAI's native Anthropic provider.

single      one agent, one task
sequential  Process.sequential over three agents; CrewAI passes prior task output as context
concurrent  one single-task crew per agent, launched together with Crew.akickoff()
"""

import asyncio
import time

from common import BASE_URL, MAX_TOKENS, MODEL, Recorder, agent_specs, parse_args, task_for

t0 = time.perf_counter()
from importlib.metadata import version  # noqa: E402

from crewai import LLM, Agent, Crew, Process, Task  # noqa: E402

import_s = time.perf_counter() - t0

EXPECTED_OUTPUT = "A concise, well-reasoned response of at most 150 words."


def make_agent(spec: dict) -> Agent:
    # temperature is left unset: Sonnet 5.5 rejects it.
    llm = LLM(model=f"anthropic/{MODEL}", max_tokens=MAX_TOKENS, base_url=BASE_URL)
    return Agent(
        role=spec["name"],
        goal=f"Deliver the {spec['name']} contribution to the task.",
        backstory=spec["system_prompt"],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )


def single_crew(spec: dict, task: str) -> Crew:
    agent = make_agent(spec)
    return Crew(
        agents=[agent],
        tasks=[Task(description=task, expected_output=EXPECTED_OUTPUT, agent=agent)],
        process=Process.sequential,
        verbose=False,
    )


def sequential_crew(specs: list[dict], task: str) -> Crew:
    agents = [make_agent(s) for s in specs]
    tasks = [Task(description=task, expected_output=EXPECTED_OUTPUT, agent=agents[0])]
    for agent in agents[1:]:
        tasks.append(Task(
            description=f"Build on the previous agent's output as the {agent.role} for this task: {task}",
            expected_output=EXPECTED_OUTPUT,
            agent=agent,
        ))
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=False)


async def main() -> None:
    args = parse_args()
    rec = Recorder("crewai", version("crewai"), args, import_s)
    specs = agent_specs(args.scenario, args.n_agents)
    task = task_for(args.scenario)

    if args.scenario in ("coldstart", "single"):
        await rec.ameasure(lambda: single_crew(specs[0], task), lambda crew: _raw(crew.akickoff()))
    elif args.scenario == "sequential":
        await rec.ameasure(lambda: sequential_crew(specs, task), lambda crew: _raw(crew.akickoff()))
    else:
        async def run_all(crews: list[Crew]) -> str:
            outputs = await asyncio.gather(*(c.akickoff() for c in crews))
            return "\n\n".join(o.raw for o in outputs)

        await rec.ameasure(lambda: [single_crew(s, task) for s in specs], run_all)
    rec.write()


async def _raw(kickoff) -> str:
    return (await kickoff).raw


if __name__ == "__main__":
    asyncio.run(main())
