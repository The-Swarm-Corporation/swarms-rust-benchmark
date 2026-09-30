"""Anthropic Messages API proxy that every benchmarked framework talks to.

mock mode  answers /v1/messages locally after a configurable delay, so a run
           measures pure framework overhead with no network or model variance.
live mode  forwards requests to api.anthropic.com unchanged and records usage.

Each request is tagged with the run id last set via POST /__control/run, which
lets the harness attribute HTTP calls, tokens and request sizes to one run.
"""

import argparse
import asyncio
import json
import time
from collections import defaultdict

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

UPSTREAM = "https://api.anthropic.com"
HOP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
BODIES_PER_RUN = 3

# Uses the ReAct "Final Answer:" shape so CrewAI's parser accepts it; the other
# frameworks treat it as plain text. Every framework receives the same bytes.
MOCK_TEXT = (
    "Thought: I now can give a great answer\n"
    "Final Answer: "
    + " ".join(
        [
            "A mutex grants exclusive ownership of a resource to one thread at a time,",
            "and only the owner may release it. A semaphore keeps a counter of available",
            "permits, lets up to that many threads proceed, and may be signalled by any",
            "thread. Use a mutex to protect a critical section and a semaphore to bound",
            "concurrent access to a pool of identical resources.",
        ]
        * 6
    )
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class State:
    def __init__(self, mode: str, delay_ms: int):
        self.mode = mode
        self.delay_ms = delay_ms
        self.run_id = "unassigned"
        self.records = defaultdict(list)
        self.bodies = defaultdict(list)
        self.t0 = time.perf_counter()
        self.session: ClientSession | None = None


def summarize_request(body: dict) -> dict:
    system = body.get("system")
    system_text = json.dumps(system) if not isinstance(system, str) else system
    messages = body.get("messages", [])
    return {
        "model": body.get("model"),
        "stream": bool(body.get("stream")),
        "max_tokens": body.get("max_tokens"),
        "temperature_sent": "temperature" in body,
        "top_p_sent": "top_p" in body,
        "stop_sequences": body.get("stop_sequences"),
        "tools_n": len(body.get("tools") or []),
        "n_messages": len(messages),
        "system_chars": len(system_text or ""),
        "messages_chars": len(json.dumps(messages)),
    }


def apply_usage(rec: dict, usage: dict | None) -> None:
    if not usage:
        return
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        if usage.get(key) is not None:
            rec[key] = usage[key]
    details = usage.get("output_tokens_details") or {}
    if details.get("thinking_tokens") is not None:
        rec["thinking_tokens"] = details["thinking_tokens"]


def mock_message(body: dict) -> dict:
    prompt_chars = len(json.dumps(body.get("system", ""))) + len(json.dumps(body.get("messages", [])))
    return {
        "id": "msg_mock",
        "type": "message",
        "role": "assistant",
        "model": body.get("model"),
        "content": [{"type": "text", "text": MOCK_TEXT}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": max(1, prompt_chars // 4), "output_tokens": estimate_tokens(MOCK_TEXT)},
    }


async def mock_stream(request: web.Request, message: dict) -> web.StreamResponse:
    resp = web.StreamResponse(headers={"content-type": "text/event-stream", "cache-control": "no-cache"})
    await resp.prepare(request)

    async def send(event: str, data: dict) -> None:
        await resp.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())

    start = {**message, "content": [], "stop_reason": None,
             "usage": {"input_tokens": message["usage"]["input_tokens"], "output_tokens": 1}}
    await send("message_start", {"type": "message_start", "message": start})
    await send("content_block_start", {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "text", "text": ""}})
    text = MOCK_TEXT
    for i in range(0, len(text), 200):
        await send("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": text[i:i + 200]}})
    await send("content_block_stop", {"type": "content_block_stop", "index": 0})
    await send("message_delta", {"type": "message_delta",
                                 "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                 "usage": {"output_tokens": message["usage"]["output_tokens"]}})
    await send("message_stop", {"type": "message_stop"})
    await resp.write_eof()
    return resp


async def forward(request: web.Request, state: State, raw: bytes, rec: dict) -> web.StreamResponse:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["accept-encoding"] = "identity"
    async with state.session.post(UPSTREAM + request.path_qs, data=raw, headers=headers) as up:
        rec["status"] = up.status
        ctype = up.headers.get("content-type", "application/json")
        passthrough = {"content-type": ctype}
        if "request-id" in up.headers:
            passthrough["request-id"] = up.headers["request-id"]

        if "text/event-stream" in ctype:
            resp = web.StreamResponse(status=up.status, headers={**passthrough, "cache-control": "no-cache"})
            await resp.prepare(request)
            buf = b""
            async for chunk in up.content.iter_any():
                await resp.write(chunk)
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        event = json.loads(line[5:])
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "message_start":
                        apply_usage(rec, event["message"].get("usage"))
                    elif event.get("type") == "message_delta":
                        apply_usage(rec, event.get("usage"))
                    elif event.get("type") == "error":
                        rec["error"] = json.dumps(event)[:500]
            await resp.write_eof()
            return resp

        data = await up.read()
        try:
            payload = json.loads(data)
            apply_usage(rec, payload.get("usage"))
            if up.status >= 400:
                rec["error"] = json.dumps(payload)[:500]
        except json.JSONDecodeError:
            rec["error"] = data[:500].decode(errors="replace")
        return web.Response(status=up.status, body=data, headers=passthrough)


async def handle_messages(request: web.Request) -> web.StreamResponse:
    state: State = request.app["state"]
    raw = await request.read()
    body = json.loads(raw or b"{}")
    run_id = state.run_id
    t_start = time.perf_counter()
    rec = {"run_id": run_id, "path": request.path, "t_start": t_start - state.t0,
           "req_bytes": len(raw), "status": 200, **summarize_request(body)}
    if len(state.bodies[run_id]) < BODIES_PER_RUN:
        state.bodies[run_id].append(body)

    try:
        if request.path.endswith("/count_tokens"):
            if state.mode == "mock":
                return web.json_response({"input_tokens": estimate_tokens(json.dumps(body))})
            return await forward(request, state, raw, rec)

        if state.mode == "live":
            return await forward(request, state, raw, rec)

        if state.delay_ms:
            await asyncio.sleep(state.delay_ms / 1000)
        message = mock_message(body)
        apply_usage(rec, message["usage"])
        if body.get("stream"):
            return await mock_stream(request, message)
        return web.json_response(message)
    except Exception as exc:  # recorded so a proxy fault is visible in results
        rec["status"] = 599
        rec["error"] = repr(exc)[:500]
        raise
    finally:
        rec["t_end"] = time.perf_counter() - state.t0
        rec["duration_ms"] = (time.perf_counter() - t_start) * 1000
        state.records[run_id].append(rec)


async def control_run(request: web.Request) -> web.Response:
    state: State = request.app["state"]
    data = await request.json()
    state.run_id = data["run_id"]
    if data.get("delay_ms") is not None:
        state.delay_ms = int(data["delay_ms"])
    return web.json_response({"run_id": state.run_id, "delay_ms": state.delay_ms, "mode": state.mode})


async def control_records(request: web.Request) -> web.Response:
    state: State = request.app["state"]
    run_id = request.query["run_id"]
    return web.json_response({"records": state.records.get(run_id, []), "bodies": state.bodies.get(run_id, [])})


async def health(request: web.Request) -> web.Response:
    state: State = request.app["state"]
    return web.json_response({"ok": True, "mode": state.mode})


async def on_startup(app: web.Application) -> None:
    state: State = app["state"]
    if state.mode == "live":
        state.session = ClientSession(timeout=ClientTimeout(total=900), connector=TCPConnector(limit=0))


async def on_cleanup(app: web.Application) -> None:
    state: State = app["state"]
    if state.session:
        await state.session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "live"], required=True)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--delay-ms", type=int, default=0)
    args = parser.parse_args()

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app["state"] = State(args.mode, args.delay_ms)
    app.router.add_post("/__control/run", control_run)
    app.router.add_get("/__control/records", control_records)
    app.router.add_get("/__control/health", health)
    app.router.add_post("/{tail:.*messages.*}", handle_messages)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None, access_log=None, backlog=1024)


if __name__ == "__main__":
    main()
