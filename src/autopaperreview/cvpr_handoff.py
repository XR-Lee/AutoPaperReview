"""Model-visible handoff isolation for the CVPR tier loop.

Filenames, packet JSON, and argv carry an opaque id. The command backend
feeds the prompt on stdin, uses an empty temporary directory as cwd, and
drops credential-bearing environment variables. Replay packets live outside
the cache and outside the checkout.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .cvpr_json import JsonOutputError, reject_truncated_output
from .hashing import sha256_bytes

HANDOFF_ID_LENGTH = 12
_SALT_ENV = "APR_CVPR_HANDOFF_SALT"
_ID_LEAK = re.compile(r"(?i)cvpr\s*20\d{2}|cvpr20\d{2}[-_]|openaccess\.thecvf\.com")
_VENUE_WORD = re.compile(r"(?i)(?<![A-Za-z0-9])(?:cvpr|iccv|eccv)(?![A-Za-z0-9])")
_YEAR_TOKEN = re.compile(r"20\d{2}")
_HANDOFF_NAME = re.compile(r"^[0-9a-f]{12}$")
_ENV_KEEP = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "HOME",
    "SYSTEMROOT",
    "COMSPEC",
)
_ENV_BLOCK = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|credential|cookie|session|"
    r"authorization|bearer|private[_-]?key)"
)
_ENV_PREFIX_BLOCK = (
    "APR_",
    "OPENAI_",
    "ANTHROPIC_",
    "XAI_",
    "GROK_",
    "AWS_",
    "AZURE_",
    "GCP_",
    "GOOGLE_",
    "GITHUB_",
    "GH_",
    "NPM_",
    "HUGGING",
)
_VERSION_CACHE: dict[str, str] = {}


class HandoffError(ValueError):
    """A model-visible handoff would leak, or an imported answer failed validation."""


def handoff_id_for(salt: bytes, paper_id: str) -> str:
    """12 hex characters from HMAC-SHA256. The salt is not written into the handoff."""
    if not salt:
        raise HandoffError("handoff salt is empty")
    digest = hmac.new(salt, str(paper_id).encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:HANDOFF_ID_LENGTH]


def load_or_create_salt(cache: Path) -> bytes:
    """Cache-local salt, or ``APR_CVPR_HANDOFF_SALT`` when the operator set one."""
    override = os.environ.get(_SALT_ENV, "").strip()
    if override:
        try:
            raw = bytes.fromhex(override)
        except ValueError as exc:
            raise HandoffError("APR_CVPR_HANDOFF_SALT must be hex") from exc
        if len(raw) < 16:
            raise HandoffError("APR_CVPR_HANDOFF_SALT must be at least 16 bytes")
        return raw
    path = cache / "handoff_salt"
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise HandoffError("handoff_salt is not hex") from exc
    raw = secrets.token_bytes(32)
    path.write_text(raw.hex() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return raw


def assert_model_visible(parts: list[str], *, what: str) -> None:
    """Refuse a year, a venue word, or a catalog id in filenames, argv, or packet JSON."""
    for part in parts:
        if _ID_LEAK.search(part) or _VENUE_WORD.search(part):
            raise HandoffError(f"{what} contains a venue or a catalog id")
        for token in re.split(r"[^\w]+", part):
            if _YEAR_TOKEN.fullmatch(token):
                raise HandoffError(f"{what} contains a year")


def scrubbed_environ(work: Path | None = None) -> dict[str, str]:
    """Minimal environment. Credential variables and APR_* do not pass through."""
    env: dict[str, str] = {}
    for key in _ENV_KEEP:
        value = os.environ.get(key)
        if value is None or _ENV_BLOCK.search(key):
            continue
        if key.startswith(_ENV_PREFIX_BLOCK):
            continue
        env[key] = value
    if work is not None:
        temporary = str(work)
        env["TMPDIR"] = temporary
        env["TEMP"] = temporary
        env["TMP"] = temporary
    return env


def cli_version(argv0: str, env: dict[str, str]) -> str:
    """First line of ``argv0 --version``. Empty when the probe does not answer."""
    cached = _VERSION_CACHE.get(argv0)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            [argv0, "--version"],
            check=False,
            capture_output=True,
            timeout=5,
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _VERSION_CACHE[argv0] = ""
        return ""
    text = (completed.stdout or completed.stderr).decode("utf-8", "replace").strip()
    line = text.splitlines()[0].strip() if text else ""
    if _ENV_BLOCK.search(line):
        line = ""
    line = line[:200]
    _VERSION_CACHE[argv0] = line
    return line


def cli_flags(argv: list[str]) -> list[str]:
    return [arg for arg in argv[1:] if arg.startswith("-")]


def estimated_tokens(prompt_chars: int) -> int:
    if prompt_chars < 0:
        raise HandoffError("prompt_chars must be non-negative")
    return prompt_chars // 4


def grok_json_prompt(prompt: str, model_id: str) -> str:
    """Add transport instructions only for Grok; leave other model prompts unchanged."""
    if "grok" not in model_id.lower():
        return prompt
    return (
        "Return only one complete JSON object matching the requested schema. "
        "Do not include Markdown fences, analysis, explanations, or text before or after the JSON. "
        "Do not call tools. Do not search or read the workspace, filesystem, or web. "
        "Use only the supplied input and your existing knowledge.\n\n" + prompt
    )


def split_usage(text: str) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Pop harness fields from a JSON object. Non-JSON text is returned unchanged."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text, None, None
    if not isinstance(payload, dict):
        return text, None, None
    usage = payload.pop("usage", None)
    sentinel = payload.pop("sentinel", None)
    payload.pop("packet_sha256", None)
    rebuilt = json.dumps(payload, ensure_ascii=False)
    usage_out = usage if isinstance(usage, dict) else None
    sentinel_out = sentinel if isinstance(sentinel, dict) else None
    return rebuilt, usage_out, sentinel_out


def assert_outside(path: Path, *roots: Path, what: str) -> None:
    resolved = path.expanduser().resolve()
    for root in roots:
        if root is None:
            continue
        try:
            resolved.relative_to(root.expanduser().resolve())
        except ValueError:
            continue
        raise HandoffError(f"{what} must be outside {root}")


def export_replay_packet(
    handoff_root: Path,
    *,
    handoff_id: str,
    prompt: str,
    packet_sha256: str,
    holistic_mode: str,
    holistic_samples: int,
) -> Path:
    """Write one paper's packet into its own directory. The name is the handoff id."""
    if not _HANDOFF_NAME.fullmatch(handoff_id):
        raise HandoffError("handoff id must be 12 hex characters")
    directory = handoff_root / handoff_id
    directory.mkdir(parents=True, exist_ok=True)
    prompt_path = directory / "prompt.txt"
    packet = {
        "handoff_id": handoff_id,
        "packet_sha256": packet_sha256,
        "prompt_file": "prompt.txt",
        "holistic_mode": holistic_mode,
        "holistic_samples": holistic_samples,
    }
    assert_model_visible(
        [handoff_id, "prompt.txt", "packet.json", json.dumps(packet, sort_keys=True)],
        what="replay packet",
    )
    prompt_path.write_text(prompt, encoding="utf-8")
    (directory / "packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return directory


def import_replay_answer(
    directory: Path,
    *,
    handoff_id: str,
    packet_sha256: str,
    canaries: list[str],
) -> str:
    """Validate one exported answer. Unknown ids and canary echoes are refused."""
    if directory.name != handoff_id or not _HANDOFF_NAME.fullmatch(directory.name):
        raise HandoffError("replay answer directory is not the handoff id")
    prompt_path = directory / "prompt.txt"
    packet_path = directory / "packet.json"
    if not prompt_path.is_file() or not packet_path.is_file():
        raise HandoffError("replay packet is incomplete")
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HandoffError("replay packet is not JSON") from exc
    if not isinstance(packet, dict) or packet.get("handoff_id") != handoff_id:
        raise HandoffError("replay packet handoff id does not match")
    if packet.get("packet_sha256") != packet_sha256:
        raise HandoffError("replay packet hash does not match the export")
    digest = sha256_bytes(prompt_path.read_bytes())
    if digest != packet_sha256:
        raise HandoffError("replay prompt was modified after export")
    answer_path = directory / "answer.json"
    if not answer_path.is_file():
        alternate = directory / "answer.txt"
        answer_path = alternate if alternate.is_file() else answer_path
    if not answer_path.is_file() or answer_path.stat().st_size == 0:
        raise HandoffError("replay answer is missing")
    if answer_path.name not in {"answer.json", "answer.txt"}:
        raise HandoffError("replay answer filename is not answer.json")
    text = answer_path.read_text(encoding="utf-8")
    for canary in canaries:
        if canary and canary in text:
            raise HandoffError("replay answer contains a label canary")
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped[stripped.find("{") : stripped.rfind("}") + 1])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and "packet_sha256" in payload:
            if payload.get("packet_sha256") != packet_sha256:
                raise HandoffError("replay answer packet hash does not match")
    return text


def load_canaries(cache: Path) -> list[str]:
    path = cache / "canaries.txt"
    if not path.is_file():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if len(item) >= 8:
            lines.append(item)
    return lines


def run_isolated_command(
    template: str,
    *,
    prompt: str,
    handoff_id: str,
    model_id: str,
    mode: str,
    timeout: float,
    render,
) -> dict[str, Any]:
    """Run one local CLI. Prompt on stdin unless the template asks for ``{prompt_file}``."""
    if not _HANDOFF_NAME.fullmatch(handoff_id):
        raise HandoffError("handoff id must be 12 hex characters")
    work = Path(tempfile.mkdtemp(prefix="apr-work-"))
    prompt_holder: Path | None = None
    try:
        answer_path = work / f"{handoff_id}.json"
        values = {
            "prompt_file": "",
            "answer_file": str(answer_path),
            "model": model_id,
            "paper_id": handoff_id,
            "handoff_id": handoff_id,
            "mode": mode,
        }
        transport = "stdin"
        if "{prompt_file}" in template:
            prompt_holder = Path(tempfile.mkdtemp(prefix="apr-prompt-"))
            prompt_path = prompt_holder / f"{handoff_id}.prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            values["prompt_file"] = str(prompt_path)
            transport = "prompt_file"
        argv = render(template, values)
        assert_model_visible([*argv, work.name, answer_path.name], what="command argv")
        env = scrubbed_environ(work)
        version = cli_version(argv[0], env)
        flags = cli_flags(argv)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                timeout=timeout,
                cwd=work,
                env=env,
                input=None if transport == "prompt_file" else prompt.encode("utf-8"),
            )
        except subprocess.TimeoutExpired as exc:
            raise HandoffError(f"command timed out for {handoff_id}") from exc
        except OSError as exc:
            raise HandoffError(f"command failed to start: {exc}") from exc
        latency = round(time.perf_counter() - started, 4)
        try:
            reject_truncated_output(completed.stdout.decode("utf-8", "replace"))
            reject_truncated_output(completed.stderr.decode("utf-8", "replace"))
        except JsonOutputError as exc:
            raise HandoffError(str(exc)) from exc
        if answer_path.is_file() and answer_path.stat().st_size > 0:
            text = answer_path.read_text(encoding="utf-8")
        elif completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace")[:500]
            raise HandoffError(f"command exited {completed.returncode} for {handoff_id}: {detail}")
        else:
            text = completed.stdout.decode("utf-8", "replace")
            if not text.strip():
                raise HandoffError(f"command wrote no answer for {handoff_id}")
        try:
            reject_truncated_output(text)
        except JsonOutputError as exc:
            raise HandoffError(str(exc)) from exc
        body, usage, sentinel = split_usage(text)
        return {
            "text": body,
            "raw": text,
            "usage": usage,
            "sentinel": sentinel,
            "latency_seconds": latency,
            "cli_version": version,
            "cli_flags": flags,
            "argv0": argv[0],
            "prompt_chars": len(prompt),
            "estimated_tokens": estimated_tokens(len(prompt)),
            "prompt_transport": transport,
        }
    finally:
        _remove(work)
        if prompt_holder is not None:
            _remove(prompt_holder)


def _remove(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def quote_debug(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)
