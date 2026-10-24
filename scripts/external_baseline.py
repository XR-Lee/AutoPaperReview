#!/usr/bin/env python3
"""Call an OpenAI-compatible reviewer and score it against a local cache.

Manuscripts, prompts, and per-paper outputs stay under the cache or under
paths passed on the command line. This script does not embed either.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.external_baseline import (  # noqa: E402
    manuscript_excerpt,
    parse_json_field,
    parse_last_rating,
    parse_rating_heading,
    summarize_scores,
)
from autopaperreview.public_calibration import assert_local_only  # noqa: E402

PARSERS = {
    "json_field": parse_json_field,
    "rating_heading": parse_rating_heading,
    "last_rating": parse_last_rating,
}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def chat(base_url: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("chat response was not an object")
    return payload


def message_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    message = choices[0].get("message") if choices else None
    content = message.get("content") if isinstance(message, dict) else ""
    if not isinstance(content, str):
        raise ValueError("chat content was not text")
    return content


def selected_ids(cache: Path, split_name: str) -> list[str]:
    split_path = cache / "split.json"
    if split_name == "all":
        if split_path.is_file():
            split = _read_json(split_path)
            return list(split.get("tune") or []) + list(split.get("holdout") or [])
        manifest = _read_json(cache / "manifest.json")
        return [item["forum_id"] for item in manifest["papers"]]
    split = _read_json(split_path)
    if split_name not in split:
        raise SystemExit(f"split {split_name} is not in split.json")
    return list(split[split_name])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score one external reviewer on a local calibration cache")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--system", required=True, help="short name used as the output directory")
    parser.add_argument("--split", choices=("holdout", "tune", "all"), default="holdout")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--system-prompt-file", required=True)
    parser.add_argument("--user-prefix-file", default="")
    parser.add_argument("--user-suffix-file", default="")
    parser.add_argument("--parser", choices=tuple(PARSERS), required=True)
    parser.add_argument("--json-field", default="Overall")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--char-limit", type=int, default=14000)
    parser.add_argument("--start-at-abstract", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    cache = assert_local_only(Path(args.cache))
    system_prompt = Path(args.system_prompt_file).read_text(encoding="utf-8")
    user_prefix = ""
    user_suffix = ""
    if args.user_prefix_file:
        user_prefix = Path(args.user_prefix_file).read_text(encoding="utf-8")
    if args.user_suffix_file:
        user_suffix = Path(args.user_suffix_file).read_text(encoding="utf-8")
    ids = selected_ids(cache, args.split)
    if args.limit:
        ids = ids[: args.limit]
    out_dir = cache / "external" / args.system
    rows_path = out_dir / "rows.json"
    rows = _read_json(rows_path) if rows_path.is_file() else {}
    parse = PARSERS[args.parser]
    failures = 0
    for forum_id in ids:
        if forum_id in rows and rows[forum_id].get("score") is not None:
            continue
        paper = cache / "papers" / forum_id
        manuscript_path = paper / "manuscript.full.txt"
        if not manuscript_path.is_file():
            manuscript_path = paper / "manuscript.txt"
        human_path = paper / "heldout" / "human.json"
        if not manuscript_path.is_file() or not human_path.is_file():
            rows[forum_id] = {"score": None, "error": "missing local files"}
            failures += 1
            _write_json(rows_path, rows)
            continue
        human = _read_json(human_path)
        excerpt = manuscript_excerpt(
            manuscript_path.read_text(encoding="utf-8"),
            limit=args.char_limit,
            start_at_abstract=args.start_at_abstract,
        )
        user = user_prefix + excerpt + user_suffix
        body = {
            "model": args.model,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "max_tokens": args.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user},
            ],
        }
        started = time.perf_counter()
        try:
            payload = chat(args.base_url, body, args.timeout)
            text = message_text(payload)
            if args.parser == "json_field":
                score = parse(text, args.json_field)
            else:
                score = parse(text)
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            error = None if score is not None else "no score"
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
            text = ""
            score = None
            usage = {}
            error = exc.__class__.__name__
        elapsed = time.perf_counter() - started
        if score is None:
            failures += 1
        rows[forum_id] = {
            "score": score,
            "error": error,
            "seconds": round(elapsed, 3),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "decision": human.get("decision"),
            "ratings": human.get("ratings"),
            "chars": len(excerpt),
        }
        # Raw model text stays beside the cache and is not printed.
        (out_dir / "raw").mkdir(parents=True, exist_ok=True)
        (out_dir / "raw" / f"{forum_id}.txt").write_text(text, encoding="utf-8")
        _write_json(rows_path, rows)
        print(
            f"done n={sum(1 for item in rows.values() if item.get('score') is not None)} "
            f"fail={failures} seconds={elapsed:.0f}",
            file=sys.stderr,
        )
    scored = [item for item in rows.values() if item.get("score") is not None and item.get("ratings")]
    summary = {"system": args.system, "model": args.model, "attempted": len(rows), "scored": len(scored)}
    if len(scored) >= 2:
        summary["metrics"] = summarize_scores(
            [float(item["score"]) for item in scored],
            [sum(item["ratings"]) / len(item["ratings"]) for item in scored],
            [str(item["decision"]) for item in scored],
            [list(item["ratings"]) for item in scored],
        )
        seconds = [float(item["seconds"]) for item in scored if item.get("seconds") is not None]
        summary["seconds_per_scored_paper"] = sum(seconds) / len(seconds) if seconds else None
    summary["failure_rate"] = (len(rows) - len(scored)) / len(rows) if rows else None
    _write_json(out_dir / "summary.json", summary)
    printable = json.dumps(summary, indent=2, sort_keys=True)
    sys.stdout.write(printable + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
