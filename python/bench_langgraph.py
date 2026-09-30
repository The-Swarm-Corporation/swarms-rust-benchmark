"""LangGraph benchmark: StateGraph nodes backed by langchain-anthropic's ChatAnthropic.

single      START -> agent -> END
sequential  START -> Researcher -> Analyst -> Writer -> END
concurrent  START fans out to N agent nodes -> END (one superstep, run concurrently)
"""

import asyncio
import operator
import time
from typing import Annotated, TypedDict

from common import BASE_URL, MAX_TOKENS, MODEL, Recorder, agent_specs, parse_args, task_for

t0 = time.perf_counter()
from importlib.metadata import version  # noqa: E402

from langchain_anthropic import ChatAnthropic  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

import_s = time.perf_counter() - t0


class State(TypedDict):
    task: str
    last: str
    outputs: Annotated[list[str], operator.add]


def make_llm() -> ChatAnthropic:
    # temperature is left unset: Sonnet 5.5 rejects it.
    return ChatAnthropic(model=MODEL, max_tokens=MAX_TOKENS, base_url=BASE_URL)


def sequential_node(spec: dict, llm: ChatAnthropic, previous: str | None):
    async def node(state: State) -> dict:
        prompt = state["task"]
        if previous:
            prompt = f"Task: {state['task']}\n\nOutput from {previous}:\n{state['last']}"
        reply = await llm.ainvoke([SystemMessage(spec["system_prompt"]), HumanMessage(prompt)])
        return {"last": reply.text, "outputs": [f"{spec['name']}: {reply.text}"]}

    return node


def parallel_node(spec: dict, llm: ChatAnthropic):
    async def node(state: State) -> dict:
        reply = await llm.ainvoke([SystemMessage(spec["system_prompt"]), HumanMessage(state["task"])])
        return {"outputs": [f"{spec['name']}: {reply.text}"]}

    return node


def build_graph(scenario: str, specs: list[dict]):
    graph = StateGraph(State)
    names = [s["name"] for s in specs]
    if scenario == "concurrent":
        for spec in specs:
            graph.add_node(spec["name"], parallel_node(spec, make_llm()))
            graph.add_edge(START, spec["name"])
            graph.add_edge(spec["name"], END)
    else:
        for i, spec in enumerate(specs):
            graph.add_node(spec["name"], sequential_node(spec, make_llm(), names[i - 1] if i else None))
        for a, b in zip([START, *names], [*names, END]):
            graph.add_edge(a, b)
    return graph.compile()


async def main() -> None:
    args = parse_args()
    rec = Recorder("langgraph", version("langgraph"), args, import_s)
    specs = agent_specs(args.scenario, args.n_agents)
    task = task_for(args.scenario)
    scenario = "single" if args.scenario == "coldstart" else args.scenario

    async def run(app):
        state = await app.ainvoke({"task": task, "last": "", "outputs": []})
        return state["last"] if scenario != "concurrent" else "\n\n".join(state["outputs"])

    await rec.ameasure(lambda: build_graph(scenario, specs), run)
    rec.write()


if __name__ == "__main__":
    asyncio.run(main())
