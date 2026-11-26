"""Join the authors' official ID mapping to anonymous proposal inputs before scoring."""

import argparse
import csv
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha1
import json
from pathlib import Path
import re
import sys
import unicodedata
from zipfile import ZipFile

from autopaperreview.command_transport import recover_json, reply_text, run_command
from autopaperreview.hashing import sha256_file
from autopaperreview.proposal.data import digest, read, reported_result, write
from autopaperreview.proposal.merit import compact
from autopaperreview.proposal.priority import route
from autopaperreview.proposal.stages import invoke

P = Path("work/prospective-transfer")
N = Path("work/next")
sys.path.insert(0, str(N))
from interest import AX, PROMPT

sys.path.insert(0, "work/source-check")
from source_experiment import SENSITIVE

UNSAFE = re.compile(
    r"\b(?:vulnerabilit(?:y|ies)|jailbreak\w*|exploit\w*|hacking|attack(?:er|ing|s)?|penetration testing|bomb\w*|weapon\w*|terroris\w*|poisoning|biosecurity)\b",
    re.I,
)
PERSONAL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def normalize(s):
    return "".join(
        c for c in unicodedata.normalize("NFKD", s).casefold() if c.isalnum()
    )


def source_units(text):
    # The public proposal template starts with a title that occasionally includes identity.
    no_title = "\n".join(text.splitlines()[1:])
    no_title = re.sub(r"https?://\S+", "", no_title)
    units = []
    for line in no_title.splitlines():
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", line):
            part = part.strip()
            if (
                len(part) >= 18
                and len(part) <= 1600
                and not PERSONAL.search(part)
                and not reported_result(part)
            ):
                units.append(part)
    return {f"u{i + 1}": s for i, s in enumerate(units)}


def blob_hash(path):
    b = path.read_bytes()
    return sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest()


def prepare():
    raw_path = N / "true-ideation-reviews.json"
    manifest = {r["name"]: r["sha"] for r in read(N / "ideation-tree.json")}
    assert blob_hash(P / "mapping.csv") == manifest["id_title_mapping.csv"]
    assert blob_hash(raw_path) == manifest["data_points_all_anonymized.json"]
    raw = read(raw_path)
    reviews = defaultdict(list)
    for i, old in enumerate(raw["idea_id"]):
        if raw["consent"][i].strip().casefold() != "yes":
            continue
        scores = {k: raw[k + "_score"][i] for k in [*AX, "overall"]}
        if not all(type(v) in (int, float) and 1 <= v <= 10 for v in scores.values()):
            continue
        reviews[re.sub(r"\s+", "", old)].append(
            {
                "scores": scores,
                "topic": raw["topic"][i],
                "reason": raw["overall_rationale"][i],
            }
        )
    files = {}
    for kind, archive in (("AI", "ai"), ("Human", "human")):
        with ZipFile(P / (archive + ".zip")) as z:
            assert z.testzip() is None
            records = {}
            for name in z.namelist():
                if not name.endswith(".txt"):
                    continue
                text = z.read(name).decode("utf-8-sig").strip()
                first = text.splitlines()[0]
                title = re.sub(
                    r"^\s*(?:#+\s*)?title\s*:\s*", "", first, flags=re.I
                ).strip(" *")
                key = normalize(title if kind == "Human" else Path(name).stem)
                assert key and key not in records
                records[key] = (name, text)
            files[kind] = records
    counts = Counter()
    used = set()
    cases = []
    gold = {}
    with (P / "mapping.csv").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ident = row["ID"].strip()
            kind = ident.split("_")[-1]
            title = row["Title / Filename"].strip()
            key = normalize(title if kind == "Human" else Path(title).stem)
            counts["mapped_rows"] += 1
            if key not in files[kind]:
                counts["unmatched"] += 1
                continue
            member, text = files[kind][key]
            assert (kind, member) not in used
            used.add((kind, member))
            counts["matched_title_or_filename"] += 1
            rev = reviews[ident]
            if not rev:
                counts["no_consented_review"] += 1
                continue
            topics = {r["topic"] for r in rev}
            assert len(topics) == 1
            topic = topics.pop()
            if topic == "Safety" or SENSITIVE.search(text) or UNSAFE.search(text):
                counts["excluded_topic_or_content"] += 1
                continue
            packet = source_units(text)
            assert len(packet) >= 3
            anon = digest(["matched-real-proposal-v1", ident])
            write(P / "inputs" / (anon + ".json"), packet)
            cases.append(
                {
                    "key": anon,
                    "topic": topic,
                    "origin": kind,
                    "file_sha256": digest(text),
                    "input_sha256": digest(packet),
                    "n_reviews": len(rev),
                }
            )
            gold[anon] = rev
    counts["unmapped_rerank_archive"] = 49
    counts["included"] = len(cases)
    counts["included_reviews"] = sum(len(v) for v in gold.values())
    state = {
        "cases": cases,
        "counts": dict(counts),
        "github_blobs": manifest,
        "source_archives": {
            name: sha256_file(P / name)
            for name in ("human.zip", "ai.zip", "rerank.zip")
        },
        "protocol": sha256_file(P / "protocol.txt"),
        "extraction_script": sha256_file(Path(__file__)),
        "interest_prompt": digest(PROMPT),
        "transfer_script": sha256_file(P / "transfer.py"),
    }
    if (P / "lock.json").exists():
        assert read(P / "lock.json")["frozen"] == state
    else:
        write(
            P / "lock.json",
            {"utc": datetime.now(timezone.utc).isoformat(), "frozen": state},
        )
    write(P / "external-gold.json", gold)
    print(dict(counts), flush=True)


def run(cases, workers):
    def one(row):
        key = row["key"]
        packet = read(P / "inputs" / (key + ".json"))
        assert digest(packet) == row["input_sha256"]
        model, cmd = route("design")
        d = invoke("design", packet, P / "cache", model, cmd, 240)
        plan = compact(read(d)["output"])
        assert all(not reported_result(v) for v in plan.values() if v)
        model, cmd = route("delta")
        req = {
            "model": model,
            "cmd": digest(cmd),
            "source": digest(plan),
            "prompt": digest(PROMPT),
        }
        dest = P / "ratings" / (key + ".json")
        if dest.exists() and read(dest)["request"] == req:
            return
        for i in range(3):
            raw, _ = run_command(
                cmd,
                system=PROMPT,
                prompt=json.dumps(plan)
                + (
                    " Return all four grade objects using numeric scores 1..10."
                    if i
                    else ""
                ),
                model=model,
                timeout=210,
            )
            try:
                out = json.loads(recover_json(reply_text(raw)[0]))
                assert all(
                    type(out[n]["score"]) in (int, float)
                    and 1 <= out[n]["score"] <= 10
                    and isinstance(out[n]["rationale"], str)
                    and out[n]["rationale"].strip()
                    for n in AX
                )
            except (ValueError, TypeError, KeyError, AssertionError):
                continue
            write(
                dest,
                {
                    "request": req,
                    "output": {n: out[n] for n in AX},
                    "design_request": read(d)["request"],
                },
            )
            return
        raise ValueError("invalid four-axis judgment")

    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, c): c["key"] for c in cases}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                failures.append(futures[f])
                print("failed", futures[f], str(e)[:180], flush=True)
    print("external complete", len(cases), len(failures), flush=True)
    if failures:
        raise RuntimeError("incomplete external ratings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "rate"))
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        lock = read(P / "lock.json")["frozen"]
        assert sha256_file(Path(__file__)) == lock["extraction_script"]
        run(lock["cases"], args.workers)
