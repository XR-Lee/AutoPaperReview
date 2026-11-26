"""Numerical backend for equal-token, two-order comparisons of pre-experiment proposals."""

import hashlib
import json

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .material import material
from .data import digest, read
from .file_io import input_sha256, open_input
from .leak_audit import apply_leak_audit, require_leak_judge

def expit(value):
    return np.exp(-np.logaddexp(0.0, -value))


def truncate(tokens, n):
    if len(tokens) <= n:
        return tokens
    left = round(.6 * n)
    right = n - left
    return tokens[:left] + (tokens[-right:] if right else [])


def _tensor_digest(tensor):
    # All encoder inputs are int64. The nested array includes row boundaries.
    encoded = json.dumps(tensor.tolist(), separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class PairEvaluator:
    def __init__(self, assets, checkpoint, tokenizer):
        spec = read(assets)
        if input_sha256(checkpoint) != spec['checkpoint'] or input_sha256(tokenizer) != spec['tokenizer']:
            raise ValueError('encoder or tokenizer fingerprint mismatch')
        ort.disable_telemetry_events()
        session = ort.SessionOptions()
        session.intra_op_num_threads = 4
        session.inter_op_num_threads = 1
        self.net = ort.InferenceSession(str(checkpoint), session, providers=['CPUExecutionProvider'])
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        self.tokenizer.no_truncation()
        self.projection = (np.random.default_rng(9127).standard_normal((768, 128)) / np.sqrt(128)).astype('f')
        if hashlib.sha256(self.projection.tobytes()).hexdigest() != spec['projection']:
            raise ValueError('projection fingerprint mismatch')
        self.scale = np.repeat(spec['fit']['scale'], 128)
        self.coefficient = np.asarray(spec['fit']['coef'])
        if (self.scale.shape != (256,) or self.coefficient.shape != (256,)
            or not np.isfinite(self.coefficient).all() or not np.isfinite(self.scale).all() or (self.scale <= 0).any()):
            raise ValueError('invalid pair model coefficients')
        self.model_id = digest(spec)
        stability_path = assets.with_name('stability-model.json')
        self.replicates = None
        if stability_path.exists():
            meta = read(stability_path)
            weights = assets.with_name('stability-models.npy')
            if input_sha256(weights) != meta['weights'] or input_sha256(assets) != meta['central_model']:
                raise ValueError('stability model fingerprint mismatch')
            with open_input(weights, "rb") as handle:
                self.replicates = np.load(handle, allow_pickle=False)
            if self.replicates.shape != (meta['bootstrap'], len(self.coefficient)) or not np.isfinite(self.replicates).all():
                raise ValueError('invalid stability weight dimensions')

    def _audit_input(self, tensors, judge):
        fingerprints = {name: _tensor_digest(array) for name, array in tensors.items()}
        packet = {"tensor_sha256": fingerprints}
        for side, ids in zip(("left", "right"), tensors["input_ids"].tolist()):
            wordpieces = []
            for token_id in ids:
                token = self.tokenizer.id_to_token(token_id)
                if not token or self.tokenizer.token_to_id(token) != token_id:
                    raise ValueError("pair tokenizer cannot identify an encoder token")
                wordpieces.append(token)
            packet[f"{side}_first"] = self.tokenizer.decode(ids, skip_special_tokens=False)
            packet[f"{side}_wordpieces"] = " ".join(wordpieces)
        apply_leak_audit(packet, judge)
        return fingerprints

    def encode_pair(self, left, right, *, leak_judge=None):
        feature, n, _ = self._encode_pair(left, right, leak_judge)
        return feature, n

    def _encode_pair(self, left, right, leak_judge):
        require_leak_judge(leak_judge)
        a, b = [self.tokenizer.encode('\n'.join(side), add_special_tokens=False).ids for side in (left, right)]
        n = min(96, len(a), len(b))
        if n == 0:
            return None, 0, None
        a, b = truncate(a, n), truncate(b, n)
        ids = np.array([[101, *a, 102, *b, 102], [101, *b, 102, *a, 102]], dtype='int64')
        segments = np.zeros_like(ids)
        segments[:, n+2:] = 1
        tensors = dict(input_ids=ids, attention_mask=(ids != 0).astype('int64'), token_type_ids=segments)
        fingerprints = self._audit_input(tensors, leak_judge)
        h = self.net.run(None, tensors)[0]
        cls = h[:, 0, :] @ self.projection
        contrast = (h[:, 1:n+1, :].mean(1) - h[:, n+2:2*n+2, :].mean(1)) @ self.projection
        feature = np.concatenate(((cls[0] - cls[1]) * .5, (contrast[0] - contrast[1]) * .5))
        return feature.astype(float), n, fingerprints

    def preference(self, left, right, *, leak_judge=None):
        feature, n = self.encode_pair(left, right, leak_judge=leak_judge)
        return (float(expit((feature / self.scale) @ self.coefficient)) if feature is not None else None), n

    def compare(self, first, second, *, leak_judge=None):
        left, right = material(first), material(second)
        feature, n, fingerprints = self._encode_pair(left, right, leak_judge)
        p = None
        bounds = None
        direction = 'insufficient_material'
        if feature is not None:
            p = float(expit((feature / self.scale) @ self.coefficient))
            direction = 'unmeasured_stability'
            if self.replicates is not None:
                bounds = np.percentile(expit(self.replicates @ feature), [5, 95]).tolist()
                direction = 'left' if p > .5 and bounds[0] > .5 else 'right' if p < .5 and bounds[1] < .5 else 'unresolved'
        return dict(left=first['id'], right=second['id'], left_overall=first.get('overall'), right_overall=second.get('overall'),
                    state='available' if p is not None else 'insufficient_material', wordpieces_per_side=n,
                    tensor_sha256=fingerprints,
                    left_statements=len(left), right_statements=len(right), probability_prefer_left=p,
                    training_stability90=bounds, direction=direction, model=self.model_id, endpoint='expert_pair_preference_research')
