"""Judgment calls through a local CLI instead of an HTTP chat endpoint.

With ``command`` set on an ``execute_review`` stage, ``OpenAICompatibleAdapter``
builds the same chat requests, and this transport hands each one to a local
CLI such as Claude Code or Codex CLI:

- The system prompt goes to a temporary file when the command names
  ``{system_file}``. Otherwise it is placed ahead of the conversation.
- The conversation goes on stdin. A schema retry arrives as one prompt that
  quotes the earlier reply and the validation error.
- ``{model}`` in the command is replaced by the stage's ``model``.
- The process runs in an empty temporary directory with the scrubbed
  environment of the CVPR tier command backend: credential variables and
  ``APR_*`` do not pass through.
- stdout is the reply. Claude Code's ``--output-format json`` envelope is
  unwrapped, and its usage is kept. One JSON object is then recovered with
  the CVPR tier rules: the whole reply, a ``json`` fence, or the last
  balanced object.

Sampling settings (``temperature``, ``response_format``) are not passed on.
APR cannot see where the CLI sends the manuscript, so the session treats a
command as remote egress.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .cvpr_handoff import cli_flags, scrubbed_environ
from .cvpr_json import JsonOutputError, parse_json_output

# The chat request is built as usual. This URL only labels it; nothing opens it.
COMMAND_REQUEST_URL = "http://command.invalid/chat/completions"
_PLACEHOLDERS = ("{system_file}", "{model}")


class CommandTransportError(RuntimeError):
    """The command failed in a way that another transport try would not fix."""


def command_argv(value: object) -> list[str]:
    """An argv list from a TOML list of strings or a shell-quoted string."""
    if isinstance(value, str):
        argv = shlex.split(value)
    elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        argv = list(value)
    else:
        raise ValueError("command must be a list of strings or one shell-quoted string")
    if not argv or not argv[0].strip():
        raise ValueError("command must name an executable")
    return argv


def command_label(argv: list[str]) -> dict[str, Any]:
    """The executable name and flags for run metadata. Flag values are not recorded."""
    return {"argv0": Path(argv[0]).name, "flags": cli_flags(argv)}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                raise CommandTransportError("a command transport sends text only; page images need an HTTP endpoint")
            parts.append(str(part.get("text", "")))
        return "".join(parts)
    raise CommandTransportError("chat message content must be text")


def flatten_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """The system prompt and one stdin prompt for a chat conversation."""
    system = "\n\n".join(_content_text(item.get("content")) for item in messages if item.get("role") == "system")
    turns = [item for item in messages if item.get("role") != "system"]
    if not turns:
        raise CommandTransportError("chat request has no user message")
    if len(turns) == 1:
        return system, _content_text(turns[0].get("content"))
    blocks = []
    for item in turns:
        label = "Your earlier reply" if item.get("role") == "assistant" else "Request"
        blocks.append(f"=== {label} ===\n{_content_text(item.get('content'))}")
    blocks.append("=== Answer the last request. ===")
    return system, "\n\n".join(blocks)


def reply_text(stdout: str) -> tuple[str, dict[str, Any]]:
    """The model reply in CLI stdout, and usage notes from a Claude Code JSON envelope."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, {}
    envelope = (
        isinstance(payload, dict)
        and payload.get("type") == "result"
        and isinstance(payload.get("result"), str)
    )
    if not envelope:
        return stdout, {}
    if payload.get("is_error"):
        raise CommandTransportError("command reported an error: " + str(payload.get("result"))[:300])
    if payload.get("stop_reason") == "max_tokens":
        raise CommandTransportError("command reply stopped at max_tokens")
    notes: dict[str, Any] = {}
    usage = payload.get("usage")
    if isinstance(usage, dict):
        prompt = sum(
            int(usage.get(key) or 0)
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        completion = int(usage.get("output_tokens") or 0)
        notes["usage"] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
    if isinstance(payload.get("total_cost_usd"), (int, float)):
        notes["cost_usd"] = float(payload["total_cost_usd"])
    return str(payload["result"]), notes


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value


def recover_json(text: str) -> str:
    """One JSON object from the reply, compact. The text unchanged when none can be recovered."""
    try:
        value = parse_json_output(text, _object)
    except JsonOutputError:
        return text
    return json.dumps(value, ensure_ascii=False)


def run_command(argv: list[str], *, system: str, prompt: str, model: str, timeout: float) -> tuple[str, float]:
    """Run one call. Returns stdout and the latency in seconds."""
    work = Path(tempfile.mkdtemp(prefix="apr-command-"))
    holder = Path(tempfile.mkdtemp(prefix="apr-command-system-"))
    try:
        stdin_text = prompt
        values = {"{model}": model, "{system_file}": ""}
        if any("{system_file}" in arg for arg in argv):
            system_path = holder / "system.txt"
            system_path.write_text(system, encoding="utf-8")
            values["{system_file}"] = str(system_path)
        elif system:
            stdin_text = f"{system}\n\n{prompt}"
        rendered = []
        for arg in argv:
            for key in _PLACEHOLDERS:
                arg = arg.replace(key, values[key])
            rendered.append(arg)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                rendered,
                input=stdin_text.encode("utf-8"),
                capture_output=True,
                cwd=work,
                env=scrubbed_environ(work),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTransportError(f"command timed out after {timeout:g} seconds") from exc
        except OSError as exc:
            raise CommandTransportError(f"command failed to start: {exc}") from exc
        latency = round(time.perf_counter() - started, 3)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        if completed.returncode != 0:
            detail = (completed.stderr.decode("utf-8", errors="replace") or stdout).strip()[:500]
            raise CommandTransportError(f"command exited {completed.returncode}: {detail}")
        if not stdout.strip():
            raise CommandTransportError("command wrote nothing to stdout")
        return stdout, latency
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(holder, ignore_errors=True)


def make_command_transport(argv: list[str], *, model: str) -> Callable[[urllib.request.Request, float], bytes]:
    """A ``Transport`` that answers each chat request by running ``argv``."""
    argv = command_argv(argv)

    def transport(request: urllib.request.Request, timeout: float) -> bytes:
        body = json.loads(bytes(request.data or b"").decode("utf-8"))
        system, prompt = flatten_messages(list(body.get("messages") or []))
        stdout, latency = run_command(argv, system=system, prompt=prompt, model=model, timeout=timeout)
        text, notes = reply_text(stdout)
        envelope: dict[str, Any] = {
            "object": "chat.completion",
            "model": body.get("model") or model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": recover_json(text)}, "finish_reason": "stop"}
            ],
            "apr_command": {**command_label(argv), "latency_seconds": latency},
        }
        if "usage" in notes:
            envelope["usage"] = notes["usage"]
        if "cost_usd" in notes:
            envelope["apr_command"]["cost_usd"] = notes["cost_usd"]
        return json.dumps(envelope, ensure_ascii=False).encode("utf-8")

    return transport
