"""Model Context Protocol server over stdio.

A host agent such as Claude Code or Codex CLI starts ``autopaperreview mcp``
and drives a review through these tools. The host agent is the reviewer: it
reads the prompt packets a run writes and answers with a judgment drop, which
the server checks with the stage's own validation. The server speaks JSON-RPC
2.0, one message per line, and uses the standard library only.

Every path a tool receives must resolve inside a ``--root`` directory.
``review.toml`` is trusted local configuration, as it is for the CLI: its
``command`` stages and ``module:attribute`` adapters run in this process.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
import threading
import traceback
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import __version__
from .config import LoadedConfig, load_config
from .pipeline import PipelineError, run_directory, validate_config_paths

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_MAX_CHARS = 40_000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

INSTRUCTIONS = """AutoPaperReview runs a local, hash-pinned review pipeline. You are the reviewer.

1. init_project with adapter "agent" (or pass an existing review.toml as config).
2. run. When status is awaiting_input, read every file in awaiting[].reads (read_input pages long files;
   your own file tool works too), call drop_template, fill the drop, and call submit_drop.
3. run again, then report.

submit_drop rejects a drop whose packet_sha256 does not match the packets, a drop without all seven
dimensions, and quotes that are not verbatim in the manuscript. Do not add an overall score; the rate
stage assigns the venue bin. The packets contain the manuscript, so reading them sends it to your model
provider."""


class ToolError(Exception):
    """A tool call that cannot be served. The client sees the message with ``isError``."""


class InvalidParams(Exception):
    """A request the protocol layer rejects with JSON-RPC error -32602."""


def _schema(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


CONFIG = {"type": "string", "description": "Path to review.toml, inside a server root."}
STAGE = {"type": "string", "description": "Stage id; needed only when review.toml declares several agent drops."}
KIND = {
    "type": "string",
    "enum": ["review_judgment", "visual_findings", "related_work_findings", "related_work_confirmation"],
    "description": "Drop kind; needed only when one stage declares two drops.",
}
OFFSET = {"type": "integer", "minimum": 0, "default": 0, "description": "First character to return."}
MAX_CHARS = {
    "type": "integer",
    "minimum": 1000,
    "maximum": 200_000,
    "default": DEFAULT_MAX_CHARS,
    "description": "Characters to return. Call again from next_offset for the rest.",
}


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.idempotent,
                "openWorldHint": self.open_world,
            },
        }


TOOLS = (
    Tool(
        "init_project",
        "Create a review project",
        "Create a review project directory with review.toml, packaged prompts, and a manuscript. With adapter "
        "\"agent\" (the default) the review stage waits for your judgment drop. manuscript_path is a UTF-8 text "
        "file copied in as manuscript.txt; pdf_path is copied as manuscript.pdf for figure checks and the "
        "annotated PDF. The directory must be new or empty.",
        _schema(
            {
                "path": {"type": "string", "description": "New project directory, inside a server root."},
                "project_id": {"type": "string", "description": "Run id; defaults to the directory name."},
                "title": {"type": "string", "default": "New manuscript review"},
                "adapter": {"type": "string", "enum": ["agent", "deterministic"], "default": "agent"},
                "manuscript_path": {"type": "string", "description": "UTF-8 text of the manuscript."},
                "pdf_path": {"type": "string", "description": "Optional manuscript PDF."},
            },
            ["path"],
        ),
    ),
    Tool(
        "validate",
        "Validate review.toml",
        "Check review.toml: schema, DAG, stage parameters, declared network access, and local paths.",
        _schema({"config": CONFIG}, ["config"]),
        read_only=True,
        idempotent=True,
    ),
    Tool(
        "run",
        "Run or resume the review",
        "Run the pipeline. Unchanged stages are reused from their hashes. Returns status success, "
        "awaiting_input (with awaiting requests to answer), failed (with the stage and error), or busy (another "
        "run holds the project). Sends progress notifications per stage when the call carries a progressToken. "
        "A stage that calls a declared model or network service may take minutes.",
        _schema(
            {"config": CONFIG, "force": {"type": "boolean", "default": False, "description": "Rerun every stage."}},
            ["config"],
        ),
        idempotent=True,
        open_world=True,
    ),
    Tool(
        "status",
        "Read the run state",
        "Read the run manifest: state, stages, awaiting drop requests with the files to read and the digests "
        "to echo, notices, input changes since the last run, report outputs, and the next step.",
        _schema(
            {
                "config": CONFIG,
                "detail": {"type": "boolean", "default": False, "description": "Add a template to each request."},
            },
            ["config"],
        ),
        read_only=True,
        idempotent=True,
    ),
    Tool(
        "read_input",
        "Read a run file",
        "Read a file of the project or its run directory, such as a prompt packet named in awaiting[].reads. "
        "Text comes back in pages of max_chars; a PNG or JPEG page render comes back as an image.",
        _schema(
            {
                "config": CONFIG,
                "path": {"type": "string", "description": "absolute_path or path from awaiting[].reads."},
                "offset": OFFSET,
                "max_chars": MAX_CHARS,
            },
            ["config", "path"],
        ),
        read_only=True,
        idempotent=True,
    ),
    Tool(
        "drop_template",
        "Get a drop skeleton",
        "Return a fill-in skeleton for an awaiting drop, with the digests to echo already filled, the allowed "
        "values, the sar_bands_v1 clause list for the seven dimensions, and the rules the check enforces.",
        _schema({"config": CONFIG, "stage_id": STAGE, "kind": KIND}, ["config"]),
        read_only=True,
        idempotent=True,
    ),
    Tool(
        "submit_drop",
        "Check and write an agent drop",
        "Check a drop with the code the stage runs (packet digests, schema, all seven dimensions, verbatim "
        "quotes when verify_excerpts is on) and write it to the drop path declared in review.toml. A rejected "
        "drop is not written; fix the errors and submit again. Pass the drop as an object, or drop_file for a "
        "JSON file you wrote. Call run afterwards.",
        _schema(
            {
                "config": CONFIG,
                "drop": {"type": "object", "description": "The drop as a JSON object."},
                "drop_file": {"type": "string", "description": "A JSON file holding the drop, inside a root."},
                "stage_id": STAGE,
                "kind": KIND,
            },
            ["config"],
        ),
        destructive=True,
        idempotent=True,
    ),
    Tool(
        "report",
        "Read the review report",
        "Return review_report.md in pages, with the paths of the review package, the PDF report, and the "
        "annotated manuscript when they exist. Fails until the run is complete.",
        _schema({"config": CONFIG, "offset": OFFSET, "max_chars": MAX_CHARS}, ["config"]),
        read_only=True,
        idempotent=True,
    ),
)
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def _error(message_id: Any, code: int, text: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def _log(text: str) -> None:
    print(f"autopaperreview mcp: {text}", file=sys.stderr, flush=True)


class Server:
    """Protocol state and tool handlers. ``send`` writes one message to the client."""

    def __init__(self, roots: Iterable[Path], send: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.roots = [Path(root).expanduser().resolve() for root in roots]
        if not self.roots:
            raise ValueError("the MCP server needs at least one root directory")
        missing = [str(root) for root in self.roots if not root.is_dir()]
        if missing:
            raise ValueError(f"root directories do not exist: {missing}")
        self.send = send or (lambda message: None)
        self.protocol_version = PROTOCOL_VERSIONS[0]

    # Protocol

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _error(None, -32600, "invalid request")
        message_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            # A response to a request this server never sends, or garbage.
            return None if "id" in message and ("result" in message or "error" in message) else _error(
                message_id, -32600, "invalid request"
            )
        if "id" not in message:
            return None
        params = message.get("params") or {}
        try:
            if not isinstance(params, dict):
                raise InvalidParams("params must be an object")
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [tool.definition() for tool in TOOLS]}
            elif method == "tools/call":
                result = self._call(params)
            else:
                return _error(message_id, -32601, f"method not found: {method}")
        except InvalidParams as exc:
            return _error(message_id, -32602, str(exc))
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        self.protocol_version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "autopaperreview", "title": "AutoPaperReview", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(name) if isinstance(name, str) else None
        if tool is None:
            raise InvalidParams(f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise InvalidParams("arguments must be an object")
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        token = meta.get("progressToken")
        try:
            self._check_arguments(tool, arguments)
            handler = getattr(self, f"tool_{tool.name}")
            return handler(arguments, progress_token=token)
        except ToolError as exc:
            return self._result({"error": str(exc)}, is_error=True)
        except (PipelineError, ValidationError, ValueError, OSError, RuntimeError, ImportError) as exc:
            _log(f"{tool.name} failed:\n{traceback.format_exc()}")
            return self._result({"error": f"{type(exc).__name__}: {exc}"}, is_error=True)
        except Exception as exc:  # noqa: BLE001 - the client gets an error result, not a dead server
            _log(f"{tool.name} raised an unexpected error:\n{traceback.format_exc()}")
            return self._result({"error": f"internal error: {type(exc).__name__}: {exc}"}, is_error=True)

    @staticmethod
    def _check_arguments(tool: Tool, arguments: dict[str, Any]) -> None:
        properties = tool.input_schema["properties"]
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ToolError(f"unknown arguments for {tool.name}: {unknown}")
        missing = [key for key in tool.input_schema["required"] if arguments.get(key) in (None, "")]
        if missing:
            raise ToolError(f"missing arguments for {tool.name}: {missing}")
        for key, value in arguments.items():
            schema = properties[key]
            expected = schema.get("type")
            kinds = {"string": str, "boolean": bool, "integer": int, "object": dict}
            if expected in kinds and not isinstance(value, kinds[expected]):
                raise ToolError(f"{key} must be a {expected}")
            if expected == "integer":
                if isinstance(value, bool):
                    raise ToolError(f"{key} must be an integer")
                if "minimum" in schema and value < schema["minimum"]:
                    raise ToolError(f"{key} must be at least {schema['minimum']}")
                if "maximum" in schema and value > schema["maximum"]:
                    raise ToolError(f"{key} must be at most {schema['maximum']}")
            if "enum" in schema and value not in schema["enum"]:
                raise ToolError(f"{key} must be one of {schema['enum']}")

    def _result(
        self,
        payload: dict[str, Any],
        *,
        is_error: bool = False,
        content: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "content": [*(content or []), {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": is_error,
        }
        if self.protocol_version >= "2025-06-18":
            result["structuredContent"] = payload
        return result

    def _progress(self, token: Any) -> Callable[[dict[str, Any]], None] | None:
        if token is None:
            return None
        with_message = self.protocol_version >= "2025-03-26"

        def emit(event: dict[str, Any]) -> None:
            done = event["status"] != "running"
            params: dict[str, Any] = {
                "progressToken": token,
                # Monotonic: a stage counts half when it starts and whole when it ends.
                "progress": event["index"] - (0 if done else 0.5),
                "total": event["total"],
            }
            if with_message:
                params["message"] = f"{event['stage_id']}: {event['status']}"
            self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})

        return emit

    # Paths

    def _inside_roots(self, path: Path) -> bool:
        return any(path == root or path.is_relative_to(root) for root in self.roots)

    def _path(self, value: str) -> Path:
        raw = Path(value).expanduser()
        resolved = (raw if raw.is_absolute() else self.roots[0] / raw).resolve()
        if not self._inside_roots(resolved):
            roots = ", ".join(str(root) for root in self.roots)
            raise ToolError(f"{resolved} is outside the server roots ({roots})")
        return resolved

    def _config(self, value: str) -> LoadedConfig:
        path = self._path(value)
        if not path.is_file():
            raise ToolError(f"config does not exist: {path}")
        loaded = load_config(path)
        run_dir = run_directory(loaded).resolve()
        if not self._inside_roots(run_dir):
            raise ToolError(f"the run directory {run_dir} is outside the server roots")
        return loaded

    # Tools

    def tool_init_project(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        from .cli import write_project

        target = self._path(arguments["path"])
        manuscript = self._path(arguments["manuscript_path"]) if arguments.get("manuscript_path") else None
        pdf = self._path(arguments["pdf_path"]) if arguments.get("pdf_path") else None
        if manuscript is not None:
            if not manuscript.is_file():
                raise ToolError(f"manuscript_path does not exist: {manuscript}")
            try:
                manuscript.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ToolError("manuscript_path must be UTF-8 text; extract the PDF text first") from exc
        if pdf is not None:
            if not pdf.is_file():
                raise ToolError(f"pdf_path does not exist: {pdf}")
            with pdf.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ToolError(f"pdf_path is not a PDF: {pdf}")
        project_id = arguments.get("project_id") or _project_id_from(target.name)
        result = write_project(
            target,
            project_id=project_id,
            title=arguments.get("title") or "New manuscript review",
            adapter=arguments.get("adapter") or "agent",
        )
        if manuscript is not None:
            shutil.copyfile(manuscript, target / "manuscript.txt")
        if pdf is not None:
            shutil.copyfile(pdf, target / "manuscript.pdf")
        result["manuscript"] = str(target / "manuscript.txt")
        if manuscript is None:
            result["note"] = "manuscript.txt is a placeholder; replace it with the manuscript text before run"
        result["next"] = "Call run with this config."
        return self._result(result)

    def tool_validate(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        loaded = self._config(arguments["config"])
        errors = validate_config_paths(loaded)
        return self._result(
            {
                "status": "pass" if not errors else "fail",
                "config": str(loaded.path),
                "config_sha256": loaded.sha256,
                "stage_count": len(loaded.config.stages),
                "route_count": len(loaded.config.routes),
                "errors": errors,
            }
        )

    def tool_run(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        from .cli import run_summary

        loaded = self._config(arguments["config"])
        self._drops_inside_roots(loaded)
        summary = run_summary(
            loaded.path,
            force=bool(arguments.get("force", False)),
            on_event=self._progress(progress_token),
        )
        return self._result(summary, is_error=summary["status"] in {"failed", "busy"})

    def tool_status(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        from .handoff import run_status

        loaded = self._config(arguments["config"])
        return self._result(run_status(loaded.path, detail=bool(arguments.get("detail", False))))

    def tool_read_input(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        loaded = self._config(arguments["config"])
        run_dir = run_directory(loaded).resolve()
        raw = Path(arguments["path"]).expanduser()
        path = (raw if raw.is_absolute() else run_dir / raw).resolve()
        if not (path.is_relative_to(run_dir) or path.is_relative_to(loaded.workspace)):
            raise ToolError("read_input reads files of this project and its run directory only")
        path = self._path(str(path))
        if not path.is_file():
            raise ToolError(f"file does not exist: {path}")
        meta: dict[str, Any] = {"path": str(path)}
        media_type = IMAGE_TYPES.get(path.suffix.lower())
        if media_type is not None:
            size = path.stat().st_size
            if size > MAX_IMAGE_BYTES:
                raise ToolError(f"image is larger than {MAX_IMAGE_BYTES} bytes: {path}")
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            meta.update({"media_type": media_type, "bytes": size})
            return self._result(meta, content=[{"type": "image", "data": data, "mimeType": media_type}])
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"not a UTF-8 text file or a page image: {path}") from exc
        return self._paged(text, meta, arguments)

    def _paged(self, text: str, meta: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        offset = int(arguments.get("offset") or 0)
        limit = int(arguments.get("max_chars") or DEFAULT_MAX_CHARS)
        chunk = text[offset : offset + limit]
        end = offset + len(chunk)
        meta.update(
            {"chars": len(text), "offset": offset, "returned": len(chunk), "next_offset": end if end < len(text) else None}
        )
        return self._result(meta, content=[{"type": "text", "text": chunk}])

    def tool_drop_template(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        from .handoff import run_status

        loaded = self._config(arguments["config"])
        status = run_status(loaded.path, detail=True)
        requests = [*status["awaiting"], *status["notices"]]
        if arguments.get("stage_id"):
            requests = [item for item in requests if item.get("stage_id") == arguments["stage_id"]]
        if arguments.get("kind"):
            requests = [item for item in requests if item.get("kind") == arguments["kind"]]
        if not requests:
            raise ToolError(f"no stage is waiting for a drop (state: {status['state']}). {status['next']}")
        if len(requests) > 1:
            names = ", ".join(f"{item.get('stage_id')}/{item.get('kind')}" for item in requests)
            raise ToolError(f"several drops are waiting; pass stage_id and kind ({names})")
        request = requests[0]
        return self._result(
            {
                "stage_id": request.get("stage_id"),
                "kind": request.get("kind"),
                "drop_path": request.get("drop_path_absolute"),
                "instructions": request.get("instructions"),
                **request["template"],
            }
        )

    def tool_submit_drop(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        from .handoff import submit_drop

        loaded = self._config(arguments["config"])
        self._drops_inside_roots(loaded)
        has_drop = arguments.get("drop") is not None
        has_file = bool(arguments.get("drop_file"))
        if has_drop == has_file:
            raise ToolError("pass exactly one of drop or drop_file")
        if has_file:
            path = self._path(arguments["drop_file"])
            if not path.is_file():
                raise ToolError(f"drop_file does not exist: {path}")
            drop: dict[str, Any] | str = path.read_text(encoding="utf-8")
        else:
            drop = arguments["drop"]
        result = submit_drop(loaded.path, drop, stage_id=arguments.get("stage_id"), kind=arguments.get("kind"))
        return self._result(result, is_error=result["status"] != "accepted")

    def tool_report(self, arguments: dict[str, Any], *, progress_token: Any = None) -> dict[str, Any]:
        from .handoff import run_status

        loaded = self._config(arguments["config"])
        status = run_status(loaded.path)
        if status["state"] != "complete":
            raise ToolError(f"the review is not complete (state: {status['state']}). {status['next']}")
        markdown = loaded.path.parent / "review_report.md"
        if not markdown.is_file():
            found = [item["path"] for item in status["outputs"] if item["role"] == "review.report.markdown"]
            if not found:
                raise ToolError("the report stage wrote no Markdown report")
            markdown = Path(found[0])
        meta = {"path": str(markdown), "outputs": status["outputs"]}
        return self._paged(markdown.read_text(encoding="utf-8"), meta, arguments)

    def _drops_inside_roots(self, loaded: LoadedConfig) -> None:
        from .handoff import drop_slots

        for slot in drop_slots(loaded):
            target = loaded.resolve(slot.drop_path).resolve()
            if not self._inside_roots(target):
                raise ToolError(f"stage {slot.stage.id} declares a drop outside the server roots: {target}")


def _project_id_from(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._")
    if not slug or not slug[0].isalpha():
        slug = f"review-{slug}" if slug else "new-review"
    return slug


def _answer(server: Server, message: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> None:
    try:
        response = server.handle(message)
    except Exception:  # noqa: BLE001 - keep serving other requests
        _log(f"request failed:\n{traceback.format_exc()}")
        response = _error(message.get("id"), -32603, "internal error")
    if response is not None:
        send(response)


def serve(server: Server, stream: Iterable[bytes], send: Callable[[Any], None], *, workers: int = 4) -> int:
    """Read newline-delimited JSON-RPC from ``stream`` until it closes.

    Tool calls run on worker threads so ``ping`` and other calls are answered
    while a long run is in progress. A second run of the same project gets
    ``busy`` from the run lock instead of corrupting the manifest.
    """
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apr-mcp") as pool:
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                send(_error(None, -32700, "parse error"))
                continue
            if isinstance(message, list):
                responses = [response for item in message if (response := server.handle(item)) is not None]
                if responses:
                    send(responses)
            elif isinstance(message, dict) and message.get("method") == "tools/call" and "id" in message:
                pool.submit(_answer, server, message, send)
            else:
                _answer(server, message, send)
    return 0


def serve_stdio(roots: Sequence[Path]) -> int:
    """Serve on this process's stdin and stdout.

    The protocol keeps a private copy of stdout. File descriptor 1 and
    ``sys.stdout`` then point at stderr, so a stray print or a child process
    cannot corrupt the message stream.
    """
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    lock = threading.Lock()

    def send(message: Any) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with lock:
            protocol.write(data)
            protocol.flush()

    server = Server(roots, send)
    _log(f"serving {len(TOOLS)} tools; roots: {', '.join(str(root) for root in server.roots)}")
    return serve(server, sys.stdin.buffer, send)
