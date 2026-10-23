"""Locked rank constants, plus the previous pole scale.

``mean-k3`` is the preregistered rank. Three separate calls each estimate the
mean official rating on 1.0–10.0. The shipped rank is the mean of those three
estimates. The calls run together. The sample count and the temperature are
declared here. They are not fit on a tune slice. The 1–6 decision is stored
from the same calls and sets the bin. It is not the rank.

The pole helpers remain so the previous implied-score formula stays defined.
A package that stored only that pair still ranks from the stored samples.
New runs do not ask for a margin.
"""

from __future__ import annotations

from dataclasses import dataclass

LADDER_FLOOR = 1.0
LADDER_CEILING = 10.0
RELATIONS = frozenset({"better", "tie", "worse"})
# Declared distance from the weak pole to the strong pole. A margin of this
# size reaches the other pole. It is not chosen from a tune slice.
POLE_GAP = 6.0
MARGIN_MAX = POLE_GAP

# Three draws of the venue mean. Matches the locked evaluation.
LOCKED_HOLISTIC_SAMPLES = 3
LOCKED_HOLISTIC_TEMPERATURE = 0.7
LOCKED_CANDIDATE = "mean-k3"


@dataclass(frozen=True)
class SyntheticAnchor:
    """One fictional reference. ``position`` stays in code, not in the prompt."""

    id: str
    position: float
    text: str


SYNTHETIC_ANCHORS: tuple[SyntheticAnchor, ...] = (
    SyntheticAnchor(
        "weak-reject",
        2.5,
        "A fictional submission a careful reviewer would reject because its own comparison "
        "does not show a gain they would rely on. The idea can be attractive. "
        "The empirical result is what falls short.",
    ),
    SyntheticAnchor(
        "strong-accept",
        8.5,
        "A fictional submission a careful reviewer would accept because its own comparison "
        "shows a gain they would rely on. The idea can be a step rather than a new field. "
        "The empirical result is what carries it.",
    ),
)

ANCHOR_IDS = tuple(anchor.id for anchor in SYNTHETIC_ANCHORS)


def anchor_by_id(anchor_id: str) -> SyntheticAnchor:
    for anchor in SYNTHETIC_ANCHORS:
        if anchor.id == anchor_id:
            return anchor
    raise ValueError(f"unknown reference {anchor_id}")


def anchor_prompt_text(anchor: SyntheticAnchor) -> str:
    """Prompt text for one reference. No position and no digits."""
    return f"### {anchor.id}\n{anchor.text}"


def normalize_margin(relation: str, margin: float) -> float:
    """Margin on the declared pole gap. A tie is 0. Any other relation is above 0."""
    if relation not in RELATIONS:
        raise ValueError("relation must be better, tie, or worse")
    if isinstance(margin, bool) or not isinstance(margin, (int, float)):
        raise ValueError("margin must be a number")
    value = round(float(margin), 4)
    if value < 0 or value > MARGIN_MAX:
        raise ValueError(f"margin must be from 0 to {MARGIN_MAX}")
    if relation == "tie":
        if value != 0:
            raise ValueError("a tie requires margin 0")
        return 0.0
    if value == 0:
        raise ValueError("better or worse requires a margin above 0")
    return value


def implied_score(position: float, relation: str, margin: float) -> float:
    """1.0–10.0 score from one pole.

    Better adds the margin, worse subtracts it, and a tie stays on the pole.
    Values outside 1.0–10.0 clamp to that end.
    """
    gap = normalize_margin(relation, margin)
    if relation == "better":
        raw = position + gap
    elif relation == "worse":
        raw = position - gap
    else:
        raw = position
    return round(min(LADDER_CEILING, max(LADDER_FLOOR, raw)), 4)


def isolate_score(implied: tuple[float, float] | list[float]) -> float:
    """Mean of the weak-reject and strong-accept implied scores. The locked rank."""
    if len(implied) != len(SYNTHETIC_ANCHORS):
        raise ValueError("isolate rank needs one implied score for each pole")
    return round(sum(implied) / len(implied), 4)
