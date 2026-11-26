"""Numerical backend for equal-token, two-order comparisons of pre-experiment proposals."""

import hashlib

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from ..hashing import sha256_file
from .material import material
from .data import digest, read
from .leak_audit import apply_leak_audit, require_leak_judge

def expit(value):
    return np.exp(-np.logaddexp(0.0, -value))


def truncate(tokens, n):
    if len(tokens) <= n:
        return tokens
    left = round(.6 * n)
    right = n - left
    return tokens[:left] + (tokens[-right:] if right else [])


class PairEvaluator:
    def __init__(self, assets, checkpoint, tokenizer):
        spec = read(assets)
        if sha256_file(checkpoint) != spec['checkpoint'] or sha256_file(tokenizer) != spec['tokenizer']:
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
            if sha256_file(weights) != meta['weights'] or sha256_file(assets) != meta['central_model']:
                raise ValueError('stability model fingerprint mismatch')
            self.replicates = np.load(weights, allow_pickle=False)
            if self.replicates.shape != (meta['bootstrap'], len(self.coefficient)) or not np.isfinite(self.replicates).all():
                raise ValueError('invalid stability weight dimensions')

    def encode_pair(self, left, right, *, leak_judge=None):
        require_leak_judge(leak_judge)
        a, b = [self.tokenizer.encode('\n'.join(side), add_special_tokens=False).ids for side in (left, right)]
        n = min(96, len(a), len(b))
        if n == 0:
            return None, 0
        a, b = truncate(a, n), truncate(b, n)
        orders = [[101, *a, 102, *b, 102], [101, *b, 102, *a, 102]]
        apply_leak_audit({
            name: self.tokenizer.decode(order, skip_special_tokens=False)
            for name, order in zip(("left_first", "right_first"), orders)
        }, leak_judge)
        ids = np.array(orders, dtype='int64')
        segments = np.zeros_like(ids)
        segments[:, n+2:] = 1
        h = self.net.run(None, dict(input_ids=ids, attention_mask=(ids != 0).astype('int64'), token_type_ids=segments))[0]
        cls = h[:, 0, :] @ self.projection
        contrast = (h[:, 1:n+1, :].mean(1) - h[:, n+2:2*n+2, :].mean(1)) @ self.projection
        feature = np.concatenate(((cls[0] - cls[1]) * .5, (contrast[0] - contrast[1]) * .5))
        return feature.astype(float), n

    def preference(self, left, right, *, leak_judge=None):
        feature, n = self.encode_pair(left, right, leak_judge=leak_judge)
        return (float(expit((feature / self.scale) @ self.coefficient)) if feature is not None else None), n

    def compare(self, first, second, *, leak_judge=None):
        left, right = material(first), material(second)
        feature, n = self.encode_pair(left, right, leak_judge=leak_judge)
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
                    left_statements=len(left), right_statements=len(right), probability_prefer_left=p,
                    training_stability90=bounds, direction=direction, model=self.model_id, endpoint='expert_pair_preference_research')
