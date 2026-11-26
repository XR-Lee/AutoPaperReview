import argparse
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from autopaperreview.proposal import pair_compare
from autopaperreview.proposal.data import read, write


def clean_judge(_):
    return {"verdict": "clean"}


class TestProposalPairCompare(unittest.TestCase):
    def test_orders_and_invalid_candidates(self):
        items = [{"id": "one"}, {"id": "two"}]
        self.assertEqual(pair_compare.select(items, "two", "one"), (items[1], items[0]))
        for document, left, right in [(items, "one", "one"), (items, "one", "missing"),
                                     (items + items, "one", "two"), ({"id": "one"}, "one", "two")]:
            with self.subTest(left=left, right=right), self.assertRaises(ValueError):
                pair_compare.select(document, left, right)

    def test_public_command_preserves_input_and_forwards_order(self):
        parser = argparse.ArgumentParser()
        pair_compare.register(parser.add_subparsers(required=True))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / "in.json", root / "out.json"
            data = [{"id": "one", "overall": 2}, {"id": "two", "overall": 3}]
            write(source, data)
            cmd = ["compare", str(source), "--left", "two", "--right", "one", "--model-dir", str(root)]
            args = parser.parse_args([*cmd, "--output", str(output)])
            with (patch.object(pair_compare, "evaluate", return_value={"direction": "unresolved"}) as call,
                  patch.object(pair_compare, "model_leak_judge", return_value=clean_judge)):
                self.assertEqual(args.func(args), 0)
            call.assert_called_once_with(root.resolve(), data[1], data[0], leak_judge=clean_judge)
            self.assertEqual(read(output), {"direction": "unresolved"})
            self.assertEqual(read(source), data)
            for path in [source, root / "model.json", root / "stability-models.npy"]:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    pair_compare.dispatch(parser.parse_args([*cmd, "--output", str(path)]))

    def test_zip_reads_only_named_assets_and_requires_complete_stability_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle.zip"
            with zipfile.ZipFile(bundle, "w") as target:
                for n in pair_compare.MODEL_FILES:
                    target.writestr(n, "synthetic")
                target.writestr("../ignored", "extra")
            def fake(directory, left, right, *, leak_judge):
                self.assertEqual({p.name for p in directory.iterdir()}, pair_compare.MODEL_FILES)
                return left, right
            with patch.object(pair_compare, "evaluate", side_effect=fake):
                self.assertEqual(pair_compare.evaluate_bundle(bundle, "a", "b", leak_judge=clean_judge), ("a", "b"))
            with zipfile.ZipFile(bundle, "a") as target:
                target.writestr("stability-model.json", "{}")
            with self.assertRaises(ValueError):
                pair_compare.evaluate_bundle(bundle, "a", "b", leak_judge=clean_judge)
