"""Fixed synthetic ladder for comparative ranking.

The references are fictional descriptions written for this scale. They are not
papers, and the prompt that quotes them does not include the numeric
positions. Positions are an even declared scale from 1.0 to 10.0. They are
not fit to a tune slice.

The shipped sample count is one. A later absolute sample is not zero-mean
noise, and the locked evaluation uses this same K.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

LADDER_FLOOR = 1.0
LADDER_CEILING = 10.0
ABOVE_ALL = "above-all"
RELATIONS = frozenset({"better", "tie", "worse"})
LEAN_FRACTIONS = {"low": 0.25, "mid": 0.5, "high": 0.75}

# One holistic call. Matches the locked tune and holdout evaluation.
LOCKED_HOLISTIC_SAMPLES = 1


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
        "A fictional submission whose idea is a minor rename of a standard method. "
        "The experiment is runnable, and the measured gain sits inside the noise of that standard method.",
    ),
    SyntheticAnchor(
        "ordinary-reject",
        4.0,
        "A fictional submission with a sensible idea and a fair experiment that shows a small real gain. "
        "It omits the comparison a reviewer would require before trusting the claim. Declining it is easy.",
    ),
    SyntheticAnchor(
        "split-decision",
        5.5,
        "A fictional submission with a fair experiment and a clear but modest gain. "
        "One ablation that the claim depends on is missing. Careful reviewers would argue both sides.",
    ),
    SyntheticAnchor(
        "ordinary-accept",
        7.0,
        "A fictional submission with a clear gain against the baselines a reviewer expects. "
        "A limitation is stated and does not touch the main claim. Accepting it does not require a fight.",
    ),
    SyntheticAnchor(
        "strong-accept",
        8.5,
        "A fictional submission that would change a common practice. "
        "The comparison is controlled, and the failure cases are measured rather than described.",
    ),
)

ANCHOR_IDS = tuple(anchor.id for anchor in SYNTHETIC_ANCHORS)


def anchor_ladder_text() -> str:
    """Prompt text for the ladder. No positions and no digits."""
    lines = [
        "These five references are fictional. They are not real submissions, and they are not this manuscript.",
        "They stay fixed, from weakest to strongest. Compare this manuscript to each one.",
        "",
    ]
    for anchor in SYNTHETIC_ANCHORS:
        lines.append(f"### {anchor.id}")
        lines.append(anchor.text)
        lines.append("")
    return "\n".join(lines).strip()


def _require_relations(relations: Mapping[str, str]) -> dict[str, str]:
    if set(relations) != set(ANCHOR_IDS) or len(relations) != len(ANCHOR_IDS):
        raise ValueError(
            "placement requires each synthetic reference once: " + ", ".join(ANCHOR_IDS)
        )
    checked: dict[str, str] = {}
    for anchor_id in ANCHOR_IDS:
        relation = relations[anchor_id]
        if not isinstance(relation, str) or relation not in RELATIONS:
            raise ValueError(f"relation for {anchor_id} must be better, tie, or worse")
        checked[anchor_id] = relation
    return checked


@dataclass(frozen=True)
class _Cut:
    cut_id: str
    cut_relation: str
    low: float | None
    high: float | None
    point: float | None


def _cut(checked: dict[str, str]) -> _Cut:
    """Walk weak to strong and stop at the first reference that is not better.

    A later vote that disagrees with that cut does not move the score. The
    raw votes still feed ``count_score``.
    """
    cleared = 0
    for anchor in SYNTHETIC_ANCHORS:
        relation = checked[anchor.id]
        if relation == "better":
            cleared += 1
            continue
        if relation == "tie":
            return _Cut(anchor.id, "tie", None, None, anchor.position)
        low = LADDER_FLOOR if cleared == 0 else SYNTHETIC_ANCHORS[cleared - 1].position
        return _Cut(anchor.id, "worse", low, anchor.position, None)
    last = SYNTHETIC_ANCHORS[-1]
    return _Cut(ABOVE_ALL, "better", last.position, LADDER_CEILING, None)


def is_single_cut(relations: Mapping[str, str]) -> bool:
    """True when the votes are one prefix of better, an optional tie, then worse."""
    checked = _require_relations(relations)
    stopped = False
    for anchor_id in ANCHOR_IDS:
        relation = checked[anchor_id]
        if not stopped:
            if relation != "better":
                stopped = True
            continue
        if relation != "worse":
            return False
    return True


def placement_score(relations: Mapping[str, str], lean: str) -> float:
    """1.0–10.0 score for one cut.

    A tie sits on that reference and ignores ``lean``. An open gap uses
    ``low``, ``mid``, or ``high`` for a quarter, half, or three quarters of
    the way toward the stronger side.
    """
    if lean not in LEAN_FRACTIONS:
        raise ValueError("gap_lean must be low, mid, or high")
    checked = _require_relations(relations)
    cut = _cut(checked)
    if cut.point is not None:
        return round(cut.point, 4)
    assert cut.low is not None and cut.high is not None
    return round(cut.low + LEAN_FRACTIONS[lean] * (cut.high - cut.low), 4)


def count_score(relations: Mapping[str, str]) -> float:
    """1.0–10.0 score from every vote. A tie counts as half a win.

    This is diagnostic. It does not repair a non-monotone ladder.
    """
    checked = _require_relations(relations)
    wins = sum(value == "better" for value in checked.values())
    ties = sum(value == "tie" for value in checked.values())
    fraction = (wins + 0.5 * ties) / len(checked)
    return round(1.0 + 9.0 * fraction, 4)


def describe_placement(relations: Mapping[str, str], lean: str) -> dict[str, object]:
    """Placement record stored beside the decision. No manuscript text."""
    if lean not in LEAN_FRACTIONS:
        raise ValueError("gap_lean must be low, mid, or high")
    checked = _require_relations(relations)
    cut = _cut(checked)
    if cut.point is not None:
        place = round(cut.point, 4)
        lean_applied = False
    else:
        assert cut.low is not None and cut.high is not None
        place = round(cut.low + LEAN_FRACTIONS[lean] * (cut.high - cut.low), 4)
        lean_applied = True
    return {
        "place": place,
        "count": count_score(checked),
        "monotonic": is_single_cut(checked),
        "cut_id": cut.cut_id,
        "cut_relation": cut.cut_relation,
        "lean": lean,
        "lean_applied": lean_applied,
        "comparisons": [
            {"anchor_id": anchor_id, "relation": checked[anchor_id]} for anchor_id in ANCHOR_IDS
        ],
    }
