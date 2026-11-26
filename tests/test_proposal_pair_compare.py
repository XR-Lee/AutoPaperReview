import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import patch

from autopaperreview.proposal import pair_compare
from autopaperreview.proposal.data import read, write
from autopaperreview.proposal.sanitized import require_model_input


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

    def pair_backend(self):
        try:
            importlib.import_module("numpy")
        except ModuleNotFoundError:
            self.skipTest("NumPy is required for the pair encoder test")
        local_dependencies = {"onnxruntime": SimpleNamespace(), "tokenizers": SimpleNamespace(Tokenizer=object)}
        with patch.dict(sys.modules, local_dependencies):
            return importlib.import_module("autopaperreview.proposal.pair_model")

    def test_pair_audits_both_truncated_orders_before_onnx(self):
        pair_model = self.pair_backend()
        model = object.__new__(pair_model.PairEvaluator)
        tokens = {"[CLS]": 101, "[SEP]": 102, "violet": 12, "basket": 13,
                  "copper": 14, "catalogue": 15, "omitted": 16}
        words = {token: word for word, token in tokens.items()}

        def encode(text, *, add_special_tokens):
            self.assertFalse(add_special_tokens)
            return SimpleNamespace(ids=[tokens[word] for word in text.split()])

        def decode(ids, *, skip_special_tokens):
            self.assertFalse(skip_special_tokens)
            return " ".join(words[token] for token in ids)

        left = [" ".join(["violet"] * 58), " ".join(["omitted"] * 23), " ".join(["basket"] * 38)]
        right = [" ".join(["copper"] * 58), " ".join(["omitted"] * 31), " ".join(["catalogue"] * 38)]
        model.tokenizer = SimpleNamespace(encode=encode, decode=decode,
                                          id_to_token=words.get, token_to_id=tokens.get)
        model.projection = pair_model.np.identity(2)
        model.scale = pair_model.np.ones(4)
        model.coefficient = pair_model.np.zeros(4)
        model.replicates = None
        model.model_id = "synthetic-model"
        events = []

        def judge(packet, verdict="clean"):
            events.append(("judge", require_model_input(packet)))
            return {"verdict": verdict}

        def infer(_, tensors):
            events.append(("onnx", {name: array.tolist() for name, array in tensors.items()}))
            return [pair_model.np.zeros((2, 195, 2))]

        model.net = SimpleNamespace(run=infer)
        with self.assertRaisesRegex(ValueError, "leak audit blocked"):
            model.encode_pair(left, right, leak_judge=lambda packet: judge(packet, "leak"))
        self.assertEqual([name for name, _ in events], ["judge"])
        events.clear()
        with patch.object(pair_model, "material", side_effect=[left, right]):
            compared = model.compare({"id": "one"}, {"id": "two"}, leak_judge=judge)
        self.assertEqual(compared["wordpieces_per_side"], 96)
        self.assertEqual([name for name, _ in events], ["judge", "onnx"])
        checked, tensors = events[0][1], events[1][1]
        encoded = tensors["input_ids"]
        for side, ids in zip(("left", "right"), encoded):
            self.assertEqual(checked[f"{side}_first"], decode(ids, skip_special_tokens=False))
            self.assertEqual(checked[f"{side}_wordpieces"], " ".join(words[token] for token in ids))
        self.assertEqual(set(checked["tensor_sha256"]), {"input_ids", "attention_mask", "token_type_ids"})
        self.assertEqual(compared["tensor_sha256"], checked["tensor_sha256"])
        for name, array in tensors.items():
            encoded_array = json.dumps(array, separators=(",", ":")).encode("ascii")
            self.assertEqual(checked["tensor_sha256"][name], "sha256:" + hashlib.sha256(encoded_array).hexdigest())
        self.assertNotIn(tokens["omitted"], encoded[0] + encoded[1])

    def test_pair_audit_distinguishes_tokens_with_the_same_decoded_text(self):
        pair_model = self.pair_backend()
        model = object.__new__(pair_model.PairEvaluator)
        tokens = {"[CLS]": 101, "[SEP]": 102, "copper": 12, "amber": 13, "basket": 14}
        words = {token: word for word, token in tokens.items()}
        model.tokenizer = SimpleNamespace(
            encode=lambda text, **_: SimpleNamespace(ids=[tokens[text]]),
            decode=lambda ids, **_: " ".join(words[token].replace("amber", "copper") for token in ids),
            id_to_token=words.get,
            token_to_id=tokens.get,
        )
        model.projection = pair_model.np.identity(2)
        events = []

        def judge(packet):
            events.append(("judge", require_model_input(packet)))
            return {"verdict": "clean"}

        def infer(_, tensors):
            events.append(("onnx", tensors["input_ids"].tolist()))
            return [pair_model.np.zeros((2, 5, 2))]

        model.net = SimpleNamespace(run=infer)
        model.encode_pair(["copper"], ["basket"], leak_judge=judge)
        model.encode_pair(["amber"], ["basket"], leak_judge=judge)
        self.assertEqual([event for event, _ in events], ["judge", "onnx", "judge", "onnx"])
        first, second = events[0][1], events[2][1]
        for side in ("left", "right"):
            self.assertEqual(first[f"{side}_first"], second[f"{side}_first"])
            self.assertNotEqual(first[f"{side}_wordpieces"], second[f"{side}_wordpieces"])
        self.assertNotEqual(first["tensor_sha256"]["input_ids"], second["tensor_sha256"]["input_ids"])
        self.assertNotEqual(events[1][1], events[3][1])

        events.clear()
        words[tokens["amber"]] = "copper"
        with self.assertRaisesRegex(ValueError, "cannot identify an encoder token"):
            model.encode_pair(["amber"], ["basket"], leak_judge=judge)
        self.assertEqual(events, [])
