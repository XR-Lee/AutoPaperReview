"""Command for comparing two source-audited candidates on a shared research question."""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from ..public_calibration import assert_local_only
from .data import read, require_distinct_output, write
from .leak_audit import model_leak_judge, require_leak_judge
from .stages import claude_argv


def register(parent):
    parser = parent.add_parser("compare", help="estimate an experimental preference between two audited proposals")
    parser.add_argument("input", type=Path, help="JSON array from prioritize --include-source-audit")
    parser.add_argument("--left", required=True, help="first candidate ID")
    parser.add_argument("--right", required=True, help="second candidate ID")
    parser.add_argument("--model", "--model-dir", dest="model_dir", type=Path, required=True,
                        help="local pair-model ZIP or its extracted directory")
    parser.add_argument("--output", type=Path, help="write JSON locally (default stdout)")
    parser.add_argument("--work", type=Path, help="local directory for leak audit receipts")
    parser.add_argument("--cached-only", action="store_true", help="use previous leak audit receipts offline")
    parser.set_defaults(func=dispatch)


def select(document, left, right):
    if left == right:
        raise ValueError("choose two distinct candidate IDs")
    if not isinstance(document, list) or any(
        not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in document
    ):
        raise ValueError("expected an array of audited candidates with string IDs")
    by_id = {r["id"]: r for r in document}
    if len(by_id) != len(document):
        raise ValueError("candidate IDs must be unique")
    if left not in by_id or right not in by_id:
        raise ValueError("both candidate IDs must exist in the input")
    return by_id[left], by_id[right]


def evaluate(directory, left, right, *, leak_judge=None):
    require_leak_judge(leak_judge)
    try:
        from .pair_model import PairEvaluator
    except ModuleNotFoundError as exc:
        if exc.name not in {"numpy", "onnxruntime", "tokenizers"}:
            raise
        raise ValueError("install autopaperreview[proposal-pair] for local pair inference") from exc

    model = PairEvaluator(directory / "model.json", directory / "specter.onnx", directory / "tokenizer.json")
    return model.compare(left, right, leak_judge=leak_judge)


MODEL_FILES = {"model.json", "specter.onnx", "tokenizer.json"}
STABILITY_FILES = {"stability-model.json", "stability-models.npy"}


def evaluate_bundle(path, left, right, *, leak_judge=None):
    require_leak_judge(leak_judge)
    if path.is_dir():
        return evaluate(path, left, right, leak_judge=leak_judge)
    if not zipfile.is_zipfile(path):
        raise ValueError("expected a local pair-model ZIP or directory")
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
        names = archive.namelist()
        present = set(names)
        selected = MODEL_FILES | (STABILITY_FILES if STABILITY_FILES & present else set())
        if any(names.count(n) != 1 for n in selected):
            raise ValueError("pair-model ZIP contains missing or duplicate model assets")
        directory = Path(tmp)
        for name in selected:
            with archive.open(name) as source, (directory / name).open("wb") as output:
                shutil.copyfileobj(source, output)
        return evaluate(directory, left, right, leak_judge=leak_judge)


def dispatch(args):
    source = assert_local_only(args.input)
    directory = assert_local_only(args.model_dir)
    output = assert_local_only(args.output) if args.output else None
    if output is not None:
        require_distinct_output(
            output, source, directory, *(directory / name for name in MODEL_FILES | STABILITY_FILES)
        )
    left, right = select(read(source), args.left, args.right)
    def compare(cache):
        judge = model_leak_judge(cache, "claude-opus-5-5", claude_argv(), 240, cached_only=args.cached_only)
        return evaluate_bundle(directory, left, right, leak_judge=judge)

    if args.work:
        result = compare(assert_local_only(args.work) / "cache")
    elif args.cached_only:
        raise ValueError("--cached-only requires --work for leak audit receipts")
    else:
        with tempfile.TemporaryDirectory(prefix="apr-proposal-pair-audit-") as cache:
            result = compare(Path(cache))
    if output:
        write(output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
