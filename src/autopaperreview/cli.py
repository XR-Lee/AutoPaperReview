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
from .models import ReviewIssue, ReviewPackage, RunManifest
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


def command_doctor(args: argparse.Namespace) -> int:
    modules = ["docling", "pydantic_ai", "opentelemetry", "openreview", "paperqa"]
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
            ],
            "optional_modules": optional_modules,
            "optional_commands": optional_commands,
            "notes": [
                "Optional modules are not adapters. A stage plugin must convert their output into AutoPaperReview records.",
                "execute_review calls a declared module:attribute adapter. The core does not call a model API.",
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
prompt_version = "1.0"

[[routes]]
id = "M2"
name = "Metric and protocol review"
kind = "prompt"
prompt = "prompts/metric_protocol_v1.md"
prompt_version = "1.0"

[[routes]]
id = "M3"
name = "SAR-shaped grounded review"
kind = "literature"
prompt = "prompts/sar_grounded_review_v1.md"
prompt_version = "1.0"

[[routes]]
id = "M4"
name = "Literature audit"
kind = "literature"
prompt = "prompts/literature_audit_v1.md"
prompt_version = "1.0"

[[routes]]
id = "M5"
name = "Reproducibility audit"
kind = "prompt"
prompt = "prompts/reproducibility_v1.md"
prompt_version = "1.0"

[[routes]]
id = "M6"
name = "Figure-grounded visual review"
kind = "visual"
prompt = "prompts/figure_grounded_v1.md"
prompt_version = "1.0"

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

    doctor = subparsers.add_parser("doctor", help="inspect optional adapter availability")
    doctor.set_defaults(func=command_doctor)

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
