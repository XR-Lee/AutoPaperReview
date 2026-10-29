"""P0 smoke tests for CVPR tier isolation. Fixtures are synthetic. No network."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from autopaperreview.cli import main
from autopaperreview.cvpr_anonymize import anonymize
from autopaperreview.cvpr_sets import (
    SetError,
    SetRegistry,
    SetSpec,
    build_lock,
    builtin_registry,
    id_list_digest,
    load_lock,
    load_source_ids,
    save_cache_registry,
    write_lock,
)
from autopaperreview.cvpr_tier import (
    CvprTierError,
    HeldoutTiers,
    SampleManifest,
    SamplePaper,
    YearSummary,
    fetch_sample,
    hidden_from_model,
    list_catalog,
    prepare_manuscripts,
    read_judgment_text,
    run_model,
    strip_cvpr_banner,
    write_report,
)
from autopaperreview.hashing import sha256_file

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "cvpr_tier"
CANARY = "CANARY-CVPR-TIER-P0-7c2e"
HIDDEN_DIRS = {"heldout", "confirmation", "custody", "probe_keys", "meta"}
HIDDEN_NAMES = {"catalog.json", "canaries.txt", "answer_key.json", "probe_answers.json"}
MANUSCRIPT = """This CVPR note is the open access version of a synthetic file.
The final published version of the proceedings is available on IEEE Xplore.
Provided by the Computer Vision Foundation.
Except for this watermark the text matches the accepted version.

GridNet: A Synthetic Lattice Counter

Ada Quill
Example Academy
ada.quill@example.test
Equal contribution.

Abstract

GridNet counts eight cells in a lattice.
GridNet repeats the count.
GridNet stays on the lattice.
GridNet is local.
GridNet does not leave the lattice.
The code is available at https://example.test/gridnet
A project page is available at https://example.test/page

Acknowledgements

Thanks to Example Academy for the fixture.

References

Quill, A. A lattice note.
"""
LEAKY = MANUSCRIPT.replace(
    "GridNet counts eight cells in a lattice.",
    "GridNet counts eight cells in an oral presentation of a lattice.",
)


def _source() -> YearSummary:
    return YearSummary(
        year=2024,
        virtual_json="fixture",
        openaccess="fixture",
        complete=True,
        label_field="fixture",
        tiers={"oral": 1, "highlight": 1, "poster": 1},
        unlabeled=0,
        with_pdf=1,
        notes="synthetic",
    )


def _write_manifest(root: Path, papers: list[tuple[str, str]], *, seed: int = 11) -> None:
    manifest = SampleManifest(
        seed=seed,
        n_per_tier={"oral": 1, "highlight": 1, "poster": 1},
        years=[2024],
        draws={
            "oral": {"available": 1, "requested": 1, "drawn": 1},
            "highlight": {"available": 1, "requested": 1, "drawn": 1},
            "poster": {"available": 1, "requested": 1, "drawn": 1},
        },
        sources=[_source()],
        papers=[SamplePaper(paper_id=paper_id, year=2024, pdf_url="fixture://synthetic") for paper_id, _tier in papers],
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample_manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    labels = HeldoutTiers(papers={paper_id: tier for paper_id, tier in papers})
    held = root / "heldout"
    held.mkdir(parents=True, exist_ok=True)
    (held / "tiers.json").write_text(labels.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _plant_canaries(cache: Path, custody: Path) -> None:
    (cache / "catalog.json").write_text(json.dumps({"note": CANARY}), encoding="utf-8")
    (cache / "canaries.txt").write_text(CANARY + "\n", encoding="utf-8")
    for relative in (
        "heldout/tiers.json",
        "confirmation/tiers.json",
        "probe_keys/answer_key.json",
        "probe_keys/probe_answers.json",
    ):
        path = cache / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            payload = path.read_text(encoding="utf-8")
            path.write_text(payload + "\n" + CANARY + "\n", encoding="utf-8")
        else:
            path.write_text(CANARY + "\n", encoding="utf-8")
    custody.mkdir(parents=True, exist_ok=True)
    (custody / "tiers.json").write_text(CANARY + "\n", encoding="utf-8")
    (cache / "custody_path.txt").write_text(str(custody.resolve()) + "\n", encoding="utf-8")


def _forbidden(cache: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(cache.resolve()).as_posix()
    except ValueError:
        return False
    parts = relative.split("/")
    if relative in HIDDEN_NAMES or parts[-1] in HIDDEN_NAMES:
        return True
    return any(part in HIDDEN_DIRS for part in parts)


def _guard(test: unittest.TestCase, cache: Path, fn):
    opened: list[Path] = []
    original_text = Path.read_text
    original_bytes = Path.read_bytes

    def wrap_text(path: Path, *args, **kwargs):
        opened.append(Path(path))
        return original_text(path, *args, **kwargs)

    def wrap_bytes(path: Path, *args, **kwargs):
        opened.append(Path(path))
        return original_bytes(path, *args, **kwargs)

    Path.read_text = wrap_text  # type: ignore[method-assign]
    Path.read_bytes = wrap_bytes  # type: ignore[method-assign]
    try:
        value = fn()
    finally:
        Path.read_text = original_text  # type: ignore[method-assign]
        Path.read_bytes = original_bytes  # type: ignore[method-assign]
    for path in opened:
        test.assertFalse(_forbidden(cache, path), path)
    return value


def _mean_json(**extra: object) -> str:
    payload: dict[str, object] = {
        "rationale": "synthetic lattice reading",
        "rating": 4,
        "predicted_mean": 6.5,
        "tier": "highlight",
    }
    payload.update(extra)
    return json.dumps(payload)


class AnonymizeTests(unittest.TestCase):
    def test_a0_matches_the_historical_banner_strip(self) -> None:
        raw = "Accepted to CVPR\n\n\n\nThe method is not a poster child for the claim.\n"
        text, _meta, _counts = anonymize(raw, "A0")
        self.assertEqual(text, strip_cvpr_banner(raw))
        self.assertIn("poster child", text)

    def test_a1_strips_identity_and_a2_masks_the_method(self) -> None:
        a1, meta, counts = anonymize(MANUSCRIPT, "A1")
        self.assertEqual(meta["title"], "GridNet: A Synthetic Lattice Counter")
        self.assertIn("Ada Quill", meta["authors"])
        self.assertEqual(counts, {key: 0 for key in counts})
        self.assertIn("GridNet", a1)
        self.assertIn("[TITLE]", a1)
        self.assertIn("[URL]", a1)
        self.assertNotIn("Quill", a1.split("References")[0])
        self.assertNotIn("example.test", a1)
        self.assertNotIn("https://", a1)
        self.assertNotIn("Equal contribution", a1)
        self.assertNotIn("Acknowledgements", a1)
        self.assertNotIn("Computer Vision Foundation", a1)
        self.assertNotIn("IEEE Xplore", a1)
        self.assertNotIn("open access version", a1.lower())
        blob = json.dumps(counts)
        self.assertNotIn("Quill", blob)
        self.assertNotIn("GridNet", blob)
        self.assertNotIn("http", blob)

        a2, _meta, a2_counts = anonymize(MANUSCRIPT, "A2")
        self.assertEqual(a2_counts, {key: 0 for key in a2_counts})
        self.assertNotIn("GridNet", a2)
        self.assertIn("[METHOD]", a2)
        self.assertIn("Quill", a2)

    def test_prepare_variants_keep_meta_out_of_the_manuscript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            _write_manifest(cache, [("syn-grid", "poster")])
            (cache / "raw").mkdir(parents=True)
            (cache / "raw" / "syn-grid.txt").write_text(MANUSCRIPT, encoding="utf-8")
            rows = prepare_manuscripts(cache, variant="A1")
            self.assertEqual(rows[0]["leak"]["residual_url"], 0)
            text = (cache / "manuscripts" / "A1" / "syn-grid.txt").read_text(encoding="utf-8")
            self.assertIn("GridNet", text)
            self.assertNotIn("ada.quill@example.test", text)
            leak = (cache / "manuscripts" / "A1" / "leak_counts.json").read_text(encoding="utf-8")
            self.assertNotIn("Quill", leak)
            self.assertNotIn("example.test", leak)
            self.assertNotIn("GridNet", leak)
            with self.assertRaises(CvprTierError):
                read_judgment_text(cache, cache / "meta" / "syn-grid.json")
            meta = json.loads((cache / "meta" / "syn-grid.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["title"], "GridNet: A Synthetic Lattice Counter")
            prepare_manuscripts(cache, variant="A2")
            masked = (cache / "manuscripts" / "A2" / "syn-grid.txt").read_text(encoding="utf-8")
            self.assertNotIn("GridNet", masked)
            self.assertIn("[METHOD]", masked)

    def test_a1_fails_closed_with_counts_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            _write_manifest(cache, [("syn-grid", "poster")])
            (cache / "raw").mkdir(parents=True)
            (cache / "raw" / "syn-grid.txt").write_text(LEAKY, encoding="utf-8")
            with self.assertRaises(CvprTierError) as caught:
                prepare_manuscripts(cache, variant="A1")
        message = str(caught.exception)
        self.assertIn("tier_phrase=", message)
        self.assertNotIn("Quill", message)
        self.assertNotIn("oral presentation of a lattice", message)


class LabelGuardTests(unittest.TestCase):
    def test_hidden_paths_cover_labels_custody_and_probe_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            custody = Path(temporary) / "custody-box"
            cache.mkdir()
            _plant_canaries(cache, custody)
            blocked = [
                cache / "heldout" / "tiers.json",
                cache / "confirmation" / "tiers.json",
                cache / "probe_keys" / "answer_key.json",
                cache / "probe_keys" / "probe_answers.json",
                cache / "catalog.json",
                cache / "canaries.txt",
                cache / "meta" / "syn-grid.json",
                custody / "tiers.json",
            ]
            (cache / "meta").mkdir()
            (cache / "meta" / "syn-grid.json").write_text("{}", encoding="utf-8")
            for path in blocked:
                self.assertIsNotNone(hidden_from_model(cache, path))
                with self.assertRaises(CvprTierError):
                    read_judgment_text(cache, path)
            manuscript = cache / "manuscripts" / "syn-grid.txt"
            manuscript.parent.mkdir(parents=True)
            manuscript.write_text("A synthetic lattice crosses eight cells.\n", encoding="utf-8")
            self.assertIsNone(hidden_from_model(cache, manuscript))
            self.assertIn("lattice", read_judgment_text(cache, manuscript))


class SentinelTests(unittest.TestCase):
    def test_command_cwd_and_env_cannot_see_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            custody = root / "custody-box"
            _write_manifest(cache, [("syn-grid", "poster")])
            (cache / "raw").mkdir(parents=True)
            (cache / "raw" / "syn-grid.txt").write_text(
                "A synthetic lattice crosses eight cells.\n", encoding="utf-8"
            )
            _plant_canaries(cache, custody)
            _guard(self, cache, lambda: prepare_manuscripts(cache))
            script = cache / "sentinel_cli.py"
            script.write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "prompt = sys.stdin.read()\n"
                "joined = ' '.join(sys.argv)\n"
                "if 'cvpr20' in joined.lower() or 'heldout' in joined.lower():\n"
                "    raise SystemExit('label path in argv')\n"
                "names = sorted(path.name for path in Path('.').iterdir())\n"
                "probes = [\n"
                "    'heldout/tiers.json', 'catalog.json', 'confirmation/tiers.json',\n"
                "    'custody/tiers.json', 'probe_keys/answer_key.json', 'meta/x.json',\n"
                "    'canaries.txt',\n"
                "]\n"
                "present = [item for item in probes if Path(item).exists()]\n"
                "hits = []\n"
                "for key, value in os.environ.items():\n"
                "    upper = key.upper()\n"
                "    if any(part in upper for part in ('KEY', 'SECRET', 'TOKEN', 'PASSWORD')):\n"
                "        hits.append(key)\n"
                f"    if {CANARY!r} in value or 'cvpr2024-' in value:\n"
                "        hits.append('value:' + key)\n"
                "answer = {\n"
                "    'rationale': 'synthetic lattice reading',\n"
                "    'rating': 4,\n"
                "    'predicted_mean': 6.5,\n"
                "    'tier': 'highlight',\n"
                "    'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18},\n"
                "    'sentinel': {\n"
                "        'cwd_entries': names,\n"
                "        'present': present,\n"
                "        'env_hits': hits,\n"
                "        'has_path': 'PATH' in os.environ,\n"
                "        'prompt_has_canary': " + repr(CANARY) + " in prompt,\n"
                "    },\n"
                "}\n"
                "Path(sys.argv[1]).write_text(json.dumps(answer), encoding='utf-8')\n",
                encoding="utf-8",
            )
            template = f"{sys.executable} -I {script} {{answer_file}} {{handoff_id}} {{model}} {{mode}}"
            previous = os.environ.get("XAI_API_KEY")
            os.environ["XAI_API_KEY"] = "fixture-not-a-real-key"
            try:
                result = _guard(
                    self,
                    cache,
                    lambda: run_model(
                        cache,
                        model_id="opus-local",
                        backend="command",
                        holistic_mode="mean",
                        command_template=template,
                    ),
                )
            finally:
                if previous is None:
                    os.environ.pop("XAI_API_KEY", None)
                else:
                    os.environ["XAI_API_KEY"] = previous
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["prompt_transport"], "stdin")
            self.assertTrue(result["parsed"])
            run_dir = cache / "runs" / "opus-local"
            usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
            row = usage["papers"][0]
            self.assertGreater(row["prompt_chars"], 0)
            self.assertEqual(row["estimated_tokens"], row["prompt_chars"] // 4)
            self.assertGreaterEqual(row["latency_seconds"], 0)
            self.assertEqual(row["retries"], 0)
            self.assertTrue(row["parsed"])
            self.assertEqual(row["usage"]["total_tokens"], 18)
            self.assertEqual(usage["prompt_transport"], "stdin")
            self.assertEqual(usage["retry_max"], 1)
            self.assertIn("Python", usage["cli_version"])
            self.assertEqual(usage["model_version"], usage["cli_version"])
            self.assertIn("-I", usage["cli_flags"])
            sentinel = json.loads((run_dir / "sentinel" / f"{row['handoff_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(sentinel["cwd_entries"], [])
            self.assertEqual(sentinel["present"], [])
            self.assertEqual(sentinel["env_hits"], [])
            self.assertTrue(sentinel["has_path"])
            self.assertFalse(sentinel["prompt_has_canary"])
            blob = b""
            for path in run_dir.rglob("*"):
                if path.is_file():
                    blob += path.read_bytes()
            self.assertNotIn(b"fixture-not-a-real-key", blob)
            self.assertNotIn(CANARY.encode("utf-8"), blob)
            for path in run_dir.rglob("*"):
                self.assertNotIn("cvpr2024", path.name.lower())

    def test_replay_import_rejects_a_canary_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            _write_manifest(cache, [("syn-grid", "poster")])
            (cache / "raw").mkdir(parents=True)
            (cache / "raw" / "syn-grid.txt").write_text(
                "A synthetic lattice crosses eight cells.\n", encoding="utf-8"
            )
            (cache / "canaries.txt").write_text(CANARY + "\n", encoding="utf-8")
            prepare_manuscripts(cache)
            waiting = run_model(cache, model_id="grok-4.7-cursor", backend="replay", holistic_mode="mean")
            hid = waiting["awaiting_judgment"][0]
            answer = Path(waiting["handoff_dir"]) / hid / "answer.json"
            answer.write_text(_mean_json(rationale=CANARY), encoding="utf-8")
            finished = run_model(cache, model_id="grok-4.7-cursor", backend="replay", holistic_mode="mean")
            self.assertEqual(finished["parsed"], [])
            self.assertTrue(finished["errors"])
            self.assertIn("canary", finished["errors"][0]["error"])
            self.assertNotIn(CANARY, finished["errors"][0]["error"])


class UsageRetryTests(unittest.TestCase):
    def test_parse_failure_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            _write_manifest(cache, [("syn-grid", "poster")])
            (cache / "raw").mkdir(parents=True)
            (cache / "raw" / "syn-grid.txt").write_text(
                "A synthetic lattice crosses eight cells.\n", encoding="utf-8"
            )
            prepare_manuscripts(cache)
            script = cache / "retry_cli.py"
            script.write_text(
                "import json, sys\n"
                "from pathlib import Path\n"
                "sys.stdin.read()\n"
                "counter = Path(__file__).with_suffix('.count')\n"
                "count = int(counter.read_text()) if counter.is_file() else 0\n"
                "count += 1\n"
                "counter.write_text(str(count))\n"
                "if count == 1:\n"
                "    print('not-json')\n"
                "else:\n"
                "    print(json.dumps({\n"
                "        'rationale': 'synthetic lattice reading',\n"
                "        'rating': 4,\n"
                "        'predicted_mean': 6.5,\n"
                "        'tier': 'highlight',\n"
                "    }))\n",
                encoding="utf-8",
            )
            template = f"{sys.executable} {script} {{handoff_id}} {{mode}}"
            result = run_model(
                cache,
                model_id="astra-local",
                backend="command",
                holistic_mode="mean",
                command_template=template,
            )
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["parsed"], ["syn-grid"])
            usage = json.loads((cache / "runs" / "astra-local" / "usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["papers"][0]["retries"], 1)
            self.assertTrue(usage["papers"][0]["parsed"])
            failure = (cache / "runs" / "astra-local" / "failures.jsonl").read_text(encoding="utf-8")
            self.assertIn('"kind": "parse"', failure)
            self.assertNotIn("synthetic lattice crosses", failure)
            self.assertEqual((cache / "retry_cli.count").read_text(encoding="utf-8"), "2")

    def test_remote_grok_adapter_is_refused(self) -> None:
        with self.assertRaises(CvprTierError) as caught:
            run_model(
                Path("unused-cache"),
                model_id="grok-4.7-cursor",
                backend="adapter",
                base_url="https://api.x.ai/v1",
                endpoint_model="grok-remote",
            )
        self.assertIn("local CLI", str(caught.exception))


class SetRegistryTests(unittest.TestCase):
    def test_committed_registry_hashes_and_roles(self) -> None:
        registry = builtin_registry()
        by_id = {spec.id: spec for spec in registry.sets}
        self.assertEqual(set(by_id), {"S24-P", "S24-C1", "S24-D2", "S24-C2"})
        pilot = by_id["S24-P"]
        first = by_id["S24-C1"]
        second_dev = by_id["S24-D2"]
        second = by_id["S24-C2"]
        self.assertEqual(pilot.role, "dev")
        self.assertEqual(pilot.status, "dev")
        self.assertEqual(pilot.seed, 20261029)
        self.assertEqual(pilot.quotas, {"oral": 13, "highlight": 17, "poster": 20})
        self.assertEqual(first.status, "sealed")
        self.assertEqual(first.role, "confirm")
        self.assertEqual(first.seed, 20261030)
        self.assertEqual(first.quotas, {"oral": 30, "highlight": 40, "poster": 50})
        self.assertEqual(first.exclude, ["S24-P"])
        self.assertEqual(second_dev.status, "dev")
        self.assertEqual(second_dev.seed, 20261031)
        self.assertEqual(second_dev.quotas, {"oral": 15, "highlight": 15, "poster": 20})
        self.assertEqual(second_dev.exclude, ["S24-P", "S24-C1"])
        self.assertEqual(second_dev.expect_available, {"oral": 47, "highlight": 267, "poster": 2224})
        self.assertIsNone(second_dev.id_list_sha256)
        self.assertEqual(second.status, "sealed")
        self.assertEqual(second.seed, 20261032)
        self.assertEqual(second.exclude, ["S24-P", "S24-C1", "S24-D2"])
        self.assertIsNone(second.id_list_sha256)
        for spec, count in ((pilot, 50), (first, 120)):
            ids = load_source_ids(spec.id_list_source or "")
            self.assertEqual(len(ids), count)
            self.assertEqual(len(set(ids)), count)
            self.assertEqual(id_list_digest(ids), spec.id_list_sha256)
        overlap = set(load_source_ids(pilot.id_list_source or "")) & set(
            load_source_ids(first.id_list_source or "")
        )
        self.assertEqual(len(overlap), 0)

    def test_fetch_refuses_sealed_sets_and_a_short_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            list_catalog(cache, years=(2024, 2025), fixture_dir=FIXTURE)
            for set_id in ("S24-C1", "S24-C2"):
                with self.assertRaises(CvprTierError) as caught:
                    fetch_sample(cache, set_id=set_id, fixture_dir=FIXTURE)
                self.assertIn("sealed", str(caught.exception))
                self.assertFalse((cache / "sets" / set_id / "sample_manifest.json").exists())
            with self.assertRaises(CvprTierError) as caught:
                fetch_sample(cache, set_id="S24-D2", fixture_dir=FIXTURE)
            self.assertIn("pool", str(caught.exception))
            self.assertFalse((cache / "sets" / "S24-D2" / "sample_manifest.json").exists())
            stored = json.loads((cache / "sets.json").read_text(encoding="utf-8"))
            dev = next(item for item in stored["sets"] if item["id"] == "S24-D2")
            self.assertIsNone(dev["id_list_sha256"])
            with self.assertRaises(CvprTierError) as caught:
                fetch_sample(cache, set_id="S24-P", fixture_dir=FIXTURE)
            self.assertIn("hash", str(caught.exception))
            self.assertFalse((cache / "sets" / "S24-P" / "sample_manifest.json").exists())

    def test_dev_set_fetch_excludes_registered_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            catalog = list_catalog(cache, years=(2024,), fixture_dir=FIXTURE)
            held = next(paper.paper_id for paper in catalog.papers if paper.tier == "oral")
            registry = SetRegistry(
                sets=[
                    SetSpec(
                        id="SYN-HOLD",
                        role="dev",
                        year=2024,
                        quotas={"oral": 1, "highlight": 0, "poster": 0},
                        seed=1,
                        status="dev",
                        id_list_sha256=id_list_digest([held]),
                    ),
                    SetSpec(
                        id="SYN-DEV",
                        role="dev",
                        year=2024,
                        quotas={"oral": 2, "highlight": 2, "poster": 2},
                        seed=7,
                        exclude=["SYN-HOLD"],
                        status="fresh",
                    ),
                ]
            )
            save_cache_registry(cache, registry)
            hold_root = cache / "sets" / "SYN-HOLD"
            _write_manifest(hold_root, [(held, "oral")], seed=1)
            manifest = fetch_sample(cache, set_id="SYN-DEV", fixture_dir=FIXTURE)
            drawn = [paper.paper_id for paper in manifest.papers]
            self.assertNotIn(held, drawn)
            self.assertEqual(len(drawn), 5)
            dumped = json.loads((cache / "sets" / "SYN-DEV" / "sample_manifest.json").read_text(encoding="utf-8"))
            for paper in dumped["papers"]:
                self.assertNotIn("tier", paper)
                self.assertEqual(paper["year"], 2024)
            with self.assertRaises(CvprTierError):
                read_judgment_text(cache, cache / "sets" / "SYN-DEV" / "heldout" / "tiers.json")
            updated = json.loads((cache / "sets.json").read_text(encoding="utf-8"))
            spec = next(item for item in updated["sets"] if item["id"] == "SYN-DEV")
            self.assertEqual(spec["status"], "dev")
            self.assertEqual(spec["id_list_sha256"], id_list_digest(drawn))
            with self.assertRaises(CvprTierError) as caught:
                fetch_sample(cache, set_id="SYN-DEV", exclude_sets=["SYN-MISSING"], fixture_dir=FIXTURE)
            self.assertIn("unknown set", str(caught.exception))
            committed = builtin_registry()
            committed_dev = next(item for item in committed.sets if item.id == "S24-D2")
            self.assertIsNone(committed_dev.id_list_sha256)

    def test_lock_roundtrip_rejects_a_tampered_body(self) -> None:
        lock = build_lock(holistic_mode="mean", holistic_samples=1)
        self.assertEqual(lock["protocol"], "cvpr-tier-lock-v1")
        self.assertFalse(lock["models"]["grok"]["xai_api"])
        self.assertEqual(lock["task"]["retry_max"], 1)
        self.assertNotIn("cvpr20", json.dumps(lock).lower())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock.json"
            write_lock(path, lock)
            loaded = load_lock(path)
            self.assertEqual(loaded["sha256"], lock["sha256"])
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["task"]["k"] = 9
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaises(SetError):
                load_lock(path)
            stdout_path = Path(temporary) / "from-cli.json"
            with redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "calibrate-eval",
                        "cvpr-tier",
                        "lock",
                        "--output",
                        str(stdout_path),
                        "--holistic-mode",
                        "mean",
                        "--holistic-samples",
                        "1",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(stdout_path.is_file())
            self.assertNotIn("cvpr20", stdout_path.read_text(encoding="utf-8").lower())


class SealTests(unittest.TestCase):
    def test_sealed_run_and_report_open_once(self) -> None:
        papers = [("syn-seal-oral", "oral"), ("syn-seal-high", "highlight"), ("syn-seal-post", "poster")]
        ids = [paper_id for paper_id, _tier in papers]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            custody = root / "custody-box"
            material = cache / "sets" / "SYN-SEAL"
            _write_manifest(material, papers, seed=11)
            for paper_id, _tier in papers:
                path = material / "manuscripts" / f"{paper_id}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("A synthetic lattice crosses eight cells.\n", encoding="utf-8")
            label_dir = custody / "SYN-SEAL"
            label_dir.mkdir(parents=True)
            (label_dir / "tiers.json").write_text(
                HeldoutTiers(papers={paper_id: tier for paper_id, tier in papers}).model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            registry = SetRegistry(
                sets=[
                    SetSpec(
                        id="SYN-SEAL",
                        role="confirm",
                        year=2024,
                        quotas={"oral": 1, "highlight": 1, "poster": 1},
                        seed=11,
                        status="sealed",
                        id_list_sha256=id_list_digest(ids),
                    )
                ]
            )
            save_cache_registry(cache, registry)
            lock = build_lock(
                registry,
                holistic_mode="mean",
                holistic_samples=1,
                label_hashes={"SYN-SEAL": sha256_file(label_dir / "tiers.json")},
            )
            lock_path = root / "lock.json"
            write_lock(lock_path, lock)
            self.assertNotIn("syn-seal-oral", lock_path.read_text(encoding="utf-8"))
            script = cache / "seal_cli.py"
            script.write_text(
                "import json, sys\n"
                "from pathlib import Path\n"
                "sys.stdin.read()\n"
                "if 'cvpr20' in ' '.join(sys.argv).lower():\n"
                "    raise SystemExit('catalog id in argv')\n"
                "Path(sys.argv[1]).write_text(json.dumps({\n"
                "    'rationale': 'synthetic lattice reading',\n"
                "    'rating': 4,\n"
                "    'predicted_mean': 6.5,\n"
                "    'tier': 'highlight',\n"
                "}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            template = f"{sys.executable} {script} {{answer_file}} {{handoff_id}} {{mode}}"
            with self.assertRaises(CvprTierError) as caught:
                run_model(
                    cache,
                    model_id="opus-local",
                    backend="command",
                    holistic_mode="mean",
                    command_template=template,
                    set_id="SYN-SEAL",
                )
            self.assertIn("--confirm --lock", str(caught.exception))
            bad = json.loads(lock_path.read_text(encoding="utf-8"))
            bad["sets"]["SYN-SEAL"]["id_list_sha256"] = "0" * 64
            from autopaperreview.hashing import hash_json

            bad["sha256"] = hash_json({key: value for key, value in bad.items() if key != "sha256"})
            bad_path = root / "bad-lock.json"
            bad_path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(CvprTierError) as caught:
                run_model(
                    cache,
                    model_id="opus-local",
                    backend="command",
                    holistic_mode="mean",
                    command_template=template,
                    set_id="SYN-SEAL",
                    confirm=True,
                    lock_path=bad_path,
                )
            self.assertIn("hash", str(caught.exception))
            result = run_model(
                cache,
                model_id="opus-local",
                backend="command",
                holistic_mode="mean",
                command_template=template,
                set_id="SYN-SEAL",
                confirm=True,
                lock_path=lock_path,
                custody=custody,
            )
            self.assertEqual(result["errors"], [])
            self.assertEqual(sorted(result["parsed"]), sorted(ids))
            report = root / "report.md"
            with self.assertRaises(CvprTierError):
                write_report(cache, report, set_id="SYN-SEAL", resamples=8, seed=1)
            payload = write_report(
                cache,
                report,
                set_id="SYN-SEAL",
                confirm=True,
                lock_path=lock_path,
                custody=custody,
                resamples=8,
                seed=1,
            )
            self.assertEqual(payload["set_id"], "SYN-SEAL")
            self.assertEqual(payload["models"]["opus-local"]["metrics"]["n"], 3)
            self.assertTrue((custody / "SYN-SEAL" / "opened.json").is_file())
            log = (custody / "SYN-SEAL" / "opened.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(log), 1)
            self.assertIn("opened", log[0])
            stored = json.loads((cache / "sets.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["sets"][0]["status"], "opened")
            with self.assertRaises(CvprTierError) as caught:
                write_report(
                    cache,
                    root / "again.md",
                    set_id="SYN-SEAL",
                    confirm=True,
                    lock_path=lock_path,
                    custody=custody,
                    resamples=8,
                    seed=1,
                )
            self.assertIn("already opened", str(caught.exception))
            self.assertEqual(len((custody / "SYN-SEAL" / "opened.log").read_text(encoding="utf-8").splitlines()), 1)
