"""LLM client: OpenAI-compatible API providers only."""
from __future__ import annotations
import json
import queue
import re
import threading
import time
from typing import Any, Callable

from openai import OpenAI

from brain.config import get_active_provider, load_config, get_effort_settings


_openai_clients: dict[tuple[str, str], OpenAI] = {}


def _get_client(base_url: str, api_key: str) -> OpenAI:
    key = (base_url, api_key)
    if key not in _openai_clients:
        _openai_clients[key] = OpenAI(base_url=base_url, api_key=api_key, timeout=60.0)
    return _openai_clients[key]


def _effort_route(fallback_model: str) -> tuple[str, dict]:
    """Resolve the active effort tier to (model, extra_body).

    Effort owns the model now: snappy/balanced -> deepseek-flash,
    max/ultra -> deepseek-v4-pro. ``reasoning_effort: none`` genuinely turns
    DeepSeek's thinking off (verified against the API: reasoning_content comes
    back empty), which is what makes snappy fast.
    """
    try:
        eff = get_effort_settings()
    except Exception:
        return fallback_model, {}
    model = eff.get("model") or fallback_model
    effort = eff.get("reasoning_effort") or ""
    extra: dict = {}
    if effort:
        extra["reasoning_effort"] = effort
    return model, extra


def _strip_think(text: str) -> str:
    """Remove <think>...</think> reasoning blocks from model output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _abortable_create(client, *, model, messages, tools, temperature, abort_check,
                      extra_body=None, poll=0.1):
    """Run a blocking chat completion, bailing out instantly on an abort.

    The OpenAI call blocks in a network request we can't cancel from the
    caller's thread, so we run it in a daemon sub-thread and poll the abort
    flag. If a Stop fires mid-request we stop waiting and raise Aborted right
    away — Stop feels instant instead of sitting on the API timeout.
    """
    result: dict = {}
    done = threading.Event()

    def _run():
        try:
            kwargs = dict(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
            )
            if extra_body:
                kwargs["extra_body"] = extra_body
            result["resp"] = client.chat.completions.create(**kwargs)
        except Exception as e:
            result["err"] = e
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    while not done.is_set():
        if abort_check is not None:
            abort_check()  # raises Aborted the instant a Stop fires
        if done.wait(timeout=poll):
            break
    t.join(timeout=0.5)
    if "err" in result:
        raise result["err"]
    return result.get("resp")


def _abortable_stream_create(
    client,
    *,
    model: str,
    messages: list[dict],
    tools: list[dict],
    temperature: float,
    abort_check: Callable[[], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
    extra_body: dict | None = None,
    poll: float = 0.1,
) -> tuple[str, list[dict]]:
    """Run a streaming chat completion, bailing out instantly on an abort.

    The stream generator blocks on network reads we can't cancel from the
    caller's thread, so we consume it in a daemon sub-thread and pump chunks
    through a bounded queue. The caller's thread polls the abort flag between
    chunks, so a Stop fires the moment it's set instead of sitting on the API.

    Returns (final_content, tool_calls) where tool_calls is a list of dicts in
    OpenAI format: {"id": ..., "type": "function", "function": {"name": ...,
    "arguments": ...}} — accumulated across stream chunks.
    """
    q: queue.Queue = queue.Queue(maxsize=64)
    done = threading.Event()
    err: dict = {}

    def _run() -> None:
        try:
            kwargs = dict(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                stream=True,
            )
            if extra_body:
                kwargs["extra_body"] = extra_body
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                q.put(chunk)
            q.put(None)  # end-of-stream sentinel
        except Exception as e:
            err["exc"] = e
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_acc: dict[int, dict] = {}

    while True:
        if abort_check is not None:
            abort_check()  # raises Aborted the instant a Stop fires
        try:
            chunk = q.get(timeout=poll)
        except queue.Empty:
            if done.is_set() and q.empty():
                break
            continue
        if chunk is None:
            break
        if not getattr(chunk, "choices", None):
            continue  # usage-only / empty chunk
        delta = chunk.choices[0].delta
        if delta is None:
            continue

        if delta.content:
            content_parts.append(delta.content)
            if on_delta:
                on_delta(delta.content)

        # DeepSeek-style models stream their reasoning in a separate field.
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            reasoning_parts.append(reasoning)
            if on_reasoning:
                on_reasoning(reasoning)

        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index if tc.index is not None else 0
                acc = tool_calls_acc.setdefault(idx, {
                    "id": "", "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["function"]["name"] += tc.function.name
                    if tc.function.arguments:
                        acc["function"]["arguments"] += tc.function.arguments

    t.join(timeout=0.5)
    if "exc" in err:
        raise err["exc"]

    content = "".join(content_parts)
    tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
    return content, tool_calls


def simple_chat(messages: list[dict], provider_key: str | None = None) -> str:
    """Send a simple chat request (no tools). Used for summarization and subagents."""
    cfg = load_config()
    if provider_key:
        provider = cfg.get("providers", {}).get(provider_key, {})
    else:
        provider = get_active_provider()

    if provider.get("type") != "openai":
        return "[LLM error: no OpenAI-compatible provider available]"

    try:
        client = _get_client(provider["base_url"], provider.get("api_key", ""))
        resp = client.chat.completions.create(
            model=provider["model"],
            messages=messages,
            temperature=cfg.get("brain", {}).get("temperature", 0.7),
        )
        return _strip_think(resp.choices[0].message.content or "")
    except Exception as e:
        return f"[LLM error: {e}]"


def chat_with_tools(
    messages: list[dict],
    tool_schemas: list[dict],
    execute_tool: Callable[[str, dict], str],
    max_loops: int = 15,
    abort_check: Callable[[], None] | None = None,
    on_tool_call: Callable[[str, dict], None] | None = None,
    on_tool_result: Callable[[str, str], None] | None = None,
    on_progress: Callable[[], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> str:
    """Run the LLM tool loop.

    Args:
        messages: Full conversation including system prompt.
        tool_schemas: JSON schemas for function calling.
        execute_tool: Callable(name, args) -> result_string.
        max_loops: Maximum tool-call iterations.
        abort_check: Callable that raises an exception if generation should stop.
        on_tool_call: Callback(name, args) when a tool is invoked.
        on_tool_result: Callback(name, result) when a tool returns.
        on_progress: Callable() when work has been going for a while.
        on_delta: Callback(text) receiving final-answer text as it streams.
                  When set, responses stream from the provider (SSE) instead
                  of arriving in one blocking chunk.
        on_reasoning: Callback(text) receiving model reasoning tokens as they
                      stream (DeepSeek-style `reasoning_content` field).
    """
    cfg = load_config()
    provider = get_active_provider()
    if provider.get("type") != "openai":
        return "No OpenAI-compatible provider is configured."

    client = _get_client(provider["base_url"], provider.get("api_key", ""))
    model, extra_body = _effort_route(provider["model"])
    temperature = cfg.get("brain", {}).get("temperature", 1.0)

    tool_count = 0
    for _ in range(max_loops):
        if abort_check:
            abort_check()

        if on_delta or on_reasoning:
            # Streaming path: same tool loop, but deltas go out live.
            content, tool_calls = _abortable_stream_create(
                client,
                model=model,
                messages=messages,
                tools=tool_schemas,
                temperature=temperature,
                abort_check=abort_check,
                on_delta=on_delta,
                on_reasoning=on_reasoning,
                extra_body=extra_body,
            )
            if not tool_calls:
                return _strip_think(content or "Done.")
            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            })
        else:
            # Blocking path (kept for callers that don't want streaming).
            resp = _abortable_create(
                client,
                model=model,
                messages=messages,
                tools=tool_schemas,
                temperature=temperature,
                abort_check=abort_check,
                extra_body=extra_body,
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return _strip_think(msg.content or "Done.")
            messages.append(msg.model_dump())
            tool_calls = [tc.model_dump() for tc in msg.tool_calls]

        for tc in tool_calls:
            if abort_check:
                abort_check()

            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
            except json.JSONDecodeError:
                args = {}

            tool_count += 1
            if tool_count == 4 and on_progress:
                on_progress()
            if on_tool_call:
                on_tool_call(name, args)

            result = execute_tool(name, args)
            if on_tool_result:
                on_tool_result(name, result)

            messages.append({
                "role": "tool",
                "content": result,
                "tool_call_id": tc.get("id", ""),
            })

    # Hit the tool-call cap. Don't reply with a hollow "Done." that loses
    # the whole task — ask the model to synthesize one final summary of
    # what it did and the outcome, so the result survives into context.
    messages = messages + [{"role": "user", "content": (
        "You've used your full tool-call budget. Without calling any more "
        "tools, give the user a concise final summary of what you did and "
        "the concrete result. Do not say 'end' or 'done'."
    )}]
    if on_delta or on_reasoning:
        content, _ = _abortable_stream_create(
            client, model=model, messages=messages, tools=[],
            temperature=temperature, abort_check=abort_check,
            on_delta=on_delta, on_reasoning=on_reasoning,
            extra_body=extra_body,
        )
    else:
        resp = _abortable_create(
            client, model=model, messages=messages, tools=[],
            temperature=temperature, abort_check=abort_check,
            extra_body=extra_body,
        )
        content = resp.choices[0].message.content or "Done."
    return _strip_think(content or "Done.")
