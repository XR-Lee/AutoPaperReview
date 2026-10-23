"""Command-line interface for AutoPaperReview."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .artifacts import artifact_from_path
from .config import HarnessConfig, load_config
from .migration import load_review_input
from .models import BatchCalibration, ReviewIssue, ReviewPackage, RunManifest
from .pipeline import (
    PipelineError,
    default_registry,
    load_manifest,
    run_pipeline,
    validate_config_paths,
)
from .reporting import write_package_json


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def command_validate(args: argparse.Namespace) -> int:
    loaded = load_config(args.config)
    errors = validate_config_paths(loaded)
    result = {
        "status": "pass" if not errors else "fail",
        "config": str(loaded.path),
        "config_sha256": loaded.sha256,
        "stage_count": len(loaded.config.stages),
        "route_count": len(loaded.config.routes),
        "errors": errors,
    }
    _print_json(result)
    return 0 if not errors else 1


def command_run(args: argparse.Namespace) -> int:
    manifest = run_pipeline(args.config, force=args.force)
    _print_json(
        {
            "status": "success",
            "run_id": manifest.run_id,
            "project_id": manifest.project_id,
            "source_sha256": manifest.source.sha256,
            "stages": [
                {"id": stage.id, "status": stage.status.value, "outputs": len(stage.outputs)}
                for stage in manifest.stages
            ],
        }
    )
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if path.is_dir():
        path = path / "run_manifest.json"
    manifest = load_manifest(path)
    _print_json(manifest.model_dump(mode="json"))
    return 0


def command_plugins(args: argparse.Namespace) -> int:
    registry = default_registry()
    _print_json({"stage_types": registry.names()})
    return 0


def command_schema(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas = {
        "review-issue.schema.json": ReviewIssue.model_json_schema(),
        "review-package.schema.json": ReviewPackage.model_json_schema(),
        "run-manifest.schema.json": RunManifest.model_json_schema(),
        "harness-config.schema.json": HarnessConfig.model_json_schema(),
        "batch-calibration.schema.json": BatchCalibration.model_json_schema(),
    }
    for filename, schema in schemas.items():
        (output_dir / filename).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    _print_json({"status": "success", "output_dir": str(output_dir), "schemas": sorted(schemas)})
    return 0


def command_templates(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    resources = sorted(
        [
            resource
            for resource in files("autopaperreview").joinpath("prompts").iterdir()
            if resource.name.endswith(".md")
        ],
        key=lambda resource: resource.name,
    )
    destinations = [output_dir / resource.name for resource in resources]
    conflicts = [path for path in destinations if path.exists()]
    if conflicts and not args.force:
        names = ", ".join(path.name for path in conflicts)
        raise FileExistsError(f"prompt templates already exist: {names}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for resource, destination in zip(resources, destinations, strict=True):
        destination.write_text(resource.read_text(encoding="utf-8"), encoding="utf-8")
    _print_json(
        {
            "status": "success",
            "output_dir": str(output_dir),
            "templates": [resource.name for resource in resources],
        }
    )
    return 0


def command_migrate(args: argparse.Namespace) -> int:
    source_path = Path(args.source).expanduser().resolve()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    manuscript = artifact_from_path(source_path, role="manuscript.source", base=source_path.parent, base_name="workspace")
    package = load_review_input(input_path, manuscript=manuscript, project_id=args.project_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_package_json(package, output_path)
    _print_json(
        {
            "status": "success",
            "output": str(output_path),
            "issue_count": len(package.issues),
            "source_sha256": manuscript.sha256,
        }
    )
    return 0


def command_calibrate_eval_list(args: argparse.Namespace) -> int:
    from .public_calibration import list_venue

    rows = list_venue(args.listing, limit=args.limit)
    _print_json({"listing": args.listing, "papers": rows})
    return 0


def command_calibrate_eval_fetch(args: argparse.Namespace) -> int:
    from .public_calibration import fetch_manifest

    statuses = fetch_manifest(
        Path(args.manifest),
        Path(args.cache),
        pdf_fallback=args.pdf_fallback,
        max_pages=args.max_pages,
    )
    _print_json({"status": "success", "papers": statuses})
    return 0


def command_calibrate_eval_prepare(args: argparse.Namespace) -> int:
    from .public_calibration import prepare_cache

    rows = prepare_cache(Path(args.cache))
    _print_json({"status": "success", "papers": rows})
    return 0


def _load_only_ids(path: str | None, split: str | None) -> set[str] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if not split or split not in payload:
            raise ValueError("an id file that is an object needs --split naming one of its lists")
        values = payload[split]
    elif isinstance(payload, list):
        values = payload
    else:
        raise ValueError("id file must be a list or an object of lists")
    return {str(item) for item in values}


def command_calibrate_eval_run(args: argparse.Namespace) -> int:
    from .public_calibration import run_cache

    result = run_cache(
        Path(args.cache),
        mode=args.mode,
        adapter=args.adapter,
        network_policy=args.network_policy,
        requires_network=args.requires_network,
        related_work=args.related_work,
        base_url=args.base_url,
        model=args.model_name,
        api_key_env=args.api_key_env,
        allow_remote=args.allow_remote,
        coefficient_set=args.coefficient_set,
        with_holistic=args.with_holistic,
        cheap=args.cheap,
        holistic_samples=args.holistic_samples,
        only_ids=_load_only_ids(args.only_ids, args.split),
    )
    if result["awaiting_judgment"]:
        status = "awaiting_judgment"
        code = 3
    elif result["errors"]:
        status = "error"
        code = 2
    else:
        status = "success"
        code = 0
    _print_json({"status": status, **result})
    return code


def command_calibrate_eval_report(args: argparse.Namespace) -> int:
    from .public_calibration import write_report

    metrics = write_report(Path(args.cache), Path(args.output))
    _print_json({"status": "success", "output": str(Path(args.output).expanduser().resolve()), "metrics": metrics})
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    modules = ["docling", "pydantic_ai", "opentelemetry", "openreview", "paperqa", "pypdfium2"]
    commands = ["docker", "pandoc", "pdftotext", "promptfoo"]
    optional_modules = {}
    for name in modules:
        importable = bool(importlib.util.find_spec(name))
        optional_modules[name] = {
            "importable": importable,
            "bundled_adapter": False,
            "status": "importable, not bundled" if importable else "not installed",
        }
    optional_commands = {}
    for name in commands:
        path = shutil.which(name)
        optional_commands[name] = {
            "path": path,
            "status": "available" if path else "missing",
        }
    _print_json(
        {
            "framework_version": __version__,
            "python": sys.version.split()[0],
            "core": {"pydantic": bool(importlib.util.find_spec("pydantic"))},
            "bundled_stage_types": default_registry().names(),
            "bundled_review_adapters": [
                "autopaperreview.adapters:DeterministicManuscriptAdapter",
                "autopaperreview.adapters:FixtureReviewAdapter",
                "autopaperreview.adapters:AgentJudgmentAdapter",
                "autopaperreview.model_exec:OpenAICompatibleAdapter",
            ],
            "bundled_visual_inspectors": [
                "autopaperreview.visual_agent:AgentFindingsInspector",
                "autopaperreview.visual_agent:HostedVisionInspector",
                "autopaperreview.model_exec:OpenAICompatibleVisionInspector",
            ],
            "optional_modules": optional_modules,
            "optional_commands": optional_commands,
            "notes": [
                "Optional modules are not adapters. A stage plugin must convert their output into AutoPaperReview records.",
                "execute_review calls a declared module:attribute adapter.",
                "OpenAICompatibleAdapter calls an OpenAI-compatible chat endpoint. Loopback is allowed by default, including a hostname that resolves only to loopback. A remote endpoint needs allow_remote, network_policy = allow, and an env-var key. The key is not part of the stage signature. bilingual = true adds a zh-Hans pass. Fixture JSON and the deterministic adapter remain equal options.",
            ],
        }
    )
    return 0


def command_init(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"target directory is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    (target / "manuscript.txt").write_text(
        "Synthetic manuscript placeholder. Replace this file with the document to review.\n",
        encoding="utf-8",
    )
    artifacts_dir = target / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "results.json").write_text(
        '{\n  "accuracy": null,\n  "note": "Replace with experimental outputs bound to the manuscript."\n}\n',
        encoding="utf-8",
    )
    templates_dir = target / "prompts"
    template_args = argparse.Namespace(output_dir=str(templates_dir), force=True)
    command_templates(template_args)
    (target / "review.toml").write_text(
        f'''schema_version = 1

[project]
id = "{args.project_id}"
title = "{args.title}"
source = "manuscript.txt"
run_root = ".runs"
default_language = "en"
network_policy = "deny"

[[routes]]
id = "M0"
name = "Structured closed-book review"
kind = "prompt"
prompt = "prompts/closed_book_v1.md"
prompt_version = "1.1"

[[routes]]
id = "M2"
name = "Metric and protocol review"
kind = "prompt"
prompt = "prompts/metric_protocol_v1.md"
prompt_version = "1.1"

[[routes]]
id = "M3"
name = "SAR-shaped grounded review"
kind = "literature"
prompt = "prompts/sar_grounded_review_v1.md"
prompt_version = "1.1"

[[routes]]
id = "M4"
name = "Literature audit"
kind = "literature"
prompt = "prompts/literature_audit_v1.md"
prompt_version = "1.1"

[[routes]]
id = "M5"
name = "Reproducibility audit"
kind = "prompt"
prompt = "prompts/reproducibility_v1.md"
prompt_version = "1.1"

[[routes]]
id = "M6"
name = "Figure-grounded visual review"
kind = "visual"
prompt = "prompts/figure_grounded_v1.md"
prompt_version = "1.0"

[[routes]]
id = "M7"
name = "Per-contribution evidence and retrieval audit"
kind = "prompt"
prompt = "prompts/contribution_audit_v1.md"
prompt_version = "1.0"

[[routes]]
id = "M8"
name = "First-read clarity review"
kind = "prompt"
prompt = "prompts/first_read_v1.md"
prompt_version = "1.0"

[[routes]]
id = "M9"
name = "Layout and format visual QA"
kind = "visual"
prompt = "prompts/visual_qa_v1.md"
prompt_version = "1.1"

[[stages]]
id = "ingest"
type = "ingest"

[[stages]]
id = "ledger"
type = "ledger"
depends_on = ["ingest"]

[[stages]]
id = "agenda"
type = "agenda"
depends_on = ["ledger"]

[[stages]]
id = "workspace"
type = "workspace_inspect"
depends_on = ["ingest"]
[stages.params]
paths = ["manuscript.txt", "artifacts"]

[[stages]]
id = "prompt_packets"
type = "prompt_packet"
depends_on = ["ingest"]
[stages.params]
include_source_text = true

[[stages]]
id = "review"
type = "execute_review"
depends_on = ["prompt_packets", "ledger"]
[stages.params]
adapter = "autopaperreview.adapters:DeterministicManuscriptAdapter"

[[stages]]
id = "integrity"
type = "integrity"
depends_on = ["workspace", "ledger"]

[[stages]]
id = "consensus"
type = "consensus"
depends_on = ["review", "integrity", "agenda"]

[[stages]]
id = "report"
type = "report"
depends_on = ["consensus"]
[stages.params]
min_issues = 1
require_ledger = true
require_integrity = true
require_first_read_on_major = true
require_quote_on_major = true
''',
        encoding="utf-8",
    )
    _print_json(
        {
            "status": "success",
            "project_dir": str(target),
            "config": str(target / "review.toml"),
            "adapter": "autopaperreview.adapters:DeterministicManuscriptAdapter",
        }
    )
    return 0


def command_calibrate(args: argparse.Namespace) -> int:
    import tomllib

    from .rating import TieNote, calibrate_packages

    batch_path = Path(args.batch).expanduser().resolve()
    payload = tomllib.loads(batch_path.read_text(encoding="utf-8"))
    root = batch_path.parent
    packages = []
    for item in payload.get("packages", []):
        path = (root / str(item["path"])).resolve()
        packages.append(ReviewPackage.model_validate_json(path.read_text(encoding="utf-8")))
    ties = [
        TieNote(
            str(item["left_project_id"]),
            str(item["right_project_id"]),
            str(item.get("axis") or "unspecified"),
            str(item["same_bin_justified"]),
        )
        for item in payload.get("ties", [])
    ]
    batch = calibrate_packages(packages, ties)
    output = Path(args.output).expanduser().resolve() if args.output else root / "batch_calibration.json"
    output.write_text(
        json.dumps(batch.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _print_json(
        {
            "status": "success",
            "output": str(output),
            "order": batch.order,
            "pairs": [
                {
                    "left": pair.left_project_id,
                    "right": pair.right_project_id,
                    "left_value": pair.left_value,
                    "right_value": pair.right_value,
                    "decision_gap": pair.decision_gap,
                    "axis": pair.axis,
                }
                for pair in batch.pairs
            ],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autopaperreview")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a review TOML file and local paths")
    validate.add_argument("config")
    validate.set_defaults(func=command_validate)

    run = subparsers.add_parser("run", help="run or resume a review pipeline")
    run.add_argument("config")
    run.add_argument("--force", action="store_true", help="rerun every enabled stage")
    run.set_defaults(func=command_run)

    inspect = subparsers.add_parser("inspect", help="print a run manifest")
    inspect.add_argument("path")
    inspect.set_defaults(func=command_inspect)

    plugins = subparsers.add_parser("plugins", help="list registered stage types")
    plugins.set_defaults(func=command_plugins)

    schema = subparsers.add_parser("schema", help="export canonical JSON Schemas")
    schema.add_argument("output_dir")
    schema.set_defaults(func=command_schema)

    templates = subparsers.add_parser("templates", help="export packaged review prompt templates")
    templates.add_argument("output_dir")
    templates.add_argument("--force", action="store_true", help="replace existing template files")
    templates.set_defaults(func=command_templates)

    migrate = subparsers.add_parser("migrate", help="convert legacy review JSON to the canonical package")
    migrate.add_argument("input")
    migrate.add_argument("output")
    migrate.add_argument("--source", required=True)
    migrate.add_argument("--project-id", required=True)
    migrate.set_defaults(func=command_migrate)

    calibrate = subparsers.add_parser("calibrate", help="order rated packages and require same-bin reasons")
    calibrate.add_argument("batch", help="TOML file listing package paths and tie notes")
    calibrate.add_argument("--output", help="where to write batch_calibration.json")
    calibrate.set_defaults(func=command_calibrate)

    doctor = subparsers.add_parser("doctor", help="inspect optional adapter availability")
    doctor.set_defaults(func=command_doctor)

    calibrate_eval = subparsers.add_parser(
        "calibrate-eval",
        help="compare the declared ICLR formula to public human scores without fitting",
    )
    eval_sub = calibrate_eval.add_subparsers(dest="eval_command", required=True)

    eval_list = eval_sub.add_parser("list", help="list public submissions for one ICLR venue listing")
    eval_list.add_argument("--listing", required=True)
    eval_list.add_argument("--limit", type=int, default=20)
    eval_list.set_defaults(func=command_calibrate_eval_list)

    eval_fetch = eval_sub.add_parser("fetch", help="fetch public PDFs and held-out official ratings")
    eval_fetch.add_argument("--manifest", required=True)
    eval_fetch.add_argument("--cache", required=True)
    eval_fetch.add_argument("--pdf-fallback", choices=("none", "arxiv"), default="arxiv")
    eval_fetch.add_argument("--max-pages", type=int, default=40)
    eval_fetch.set_defaults(func=command_calibrate_eval_fetch)

    eval_prepare = eval_sub.add_parser("prepare", help="write prompt packets for fetched papers")
    eval_prepare.add_argument("--cache", required=True)
    eval_prepare.set_defaults(func=command_calibrate_eval_prepare)

    eval_run = eval_sub.add_parser("run", help="run the judgment adapter and the declared rating")
    eval_run.add_argument("--cache", required=True)
    eval_run.add_argument("--mode", choices=("agent", "adapter"), default="agent")
    eval_run.add_argument("--adapter", help="module:attribute used when --mode adapter")
    eval_run.add_argument("--base-url", help="OpenAI-compatible chat base URL")
    eval_run.add_argument("--model", dest="model_name", help="model name sent to that endpoint")
    eval_run.add_argument("--api-key-env", default="APR_MODEL_API_KEY")
    eval_run.add_argument("--allow-remote", action="store_true")
    eval_run.add_argument("--related-work", choices=("off", "agent", "live"), default="off")
    eval_run.add_argument("--network-policy", choices=("deny", "metadata_only", "allow"), default="deny")
    eval_run.add_argument("--requires-network", action="store_true")
    eval_run.add_argument("--coefficient-set", default="apr-iclr-cap-v2")
    eval_run.add_argument("--with-holistic", action="store_true")
    eval_run.add_argument(
        "--cheap",
        action="store_true",
        help="holistic samples only; skip contributions, ledger, dimensions, and fatal",
    )
    from .model_exec import DEFAULT_HOLISTIC_SAMPLES

    eval_run.add_argument(
        "--holistic-samples",
        type=int,
        default=DEFAULT_HOLISTIC_SAMPLES,
        help="holistic calls per paper; the default is the locked evaluation K",
    )
    eval_run.add_argument("--only-ids", help="gitignored JSON list, or an object of lists")
    eval_run.add_argument("--split", help="key inside --only-ids when that file is an object")
    eval_run.set_defaults(func=command_calibrate_eval_run)

    eval_report = eval_sub.add_parser("report", help="write Spearman, MAE, bin agreement, AUC, and spread")
    eval_report.add_argument("--cache", required=True)
    eval_report.add_argument("--output", required=True)
    eval_report.set_defaults(func=command_calibrate_eval_report)

    init = subparsers.add_parser("init", help="create a minimal local-first review project")
    init.add_argument("path")
    init.add_argument("--project-id", default="new-review")
    init.add_argument("--title", default="New manuscript review")
    init.set_defaults(func=command_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (PipelineError, ValidationError, ValueError, FileNotFoundError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
