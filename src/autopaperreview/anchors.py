"""Locked rank constants, the forecast panel, the discussed panel, the matched panel, the review panel, the expert read, the audit checklist, and the pole scale.

``forecast-k1`` is the preregistered rank. One call simulates three of the
reviewers the venue would assign, in the declared seats below, on the full
manuscript. Each reviewer writes a compact review, the official rating they
would most likely submit, and a probability over the six official ratings;
the pipeline takes each reviewer's expected rating under that probability,
and the per-call sample is the mean of the three expected ratings. The
shipped rank is that sample. The mean of the three integer ratings
(``vote``), the model's own estimate of the mean (``chair``) and the highest
and lowest expected rating (``high``, ``low``) are diagnostic. The 1–6
decision from the same call sets the bin. It is not the rank.

``holistic_mode = "rebuttal"`` keeps the round-10 candidate: K simulated
reviews carried through the discussion period on the full manuscript, ranked
by the mean of the final ratings. ``holistic_mode = "matched"`` keeps the round-9 candidate: K topic-matched
panels of mixed seniority on the main text with the venue's rating norms,
averaged. ``holistic_mode = "panel"`` keeps the round-8 candidate: K
mixed-role panels on the full manuscript, averaged. ``holistic_mode =
"expert"`` keeps the round-7 candidate: K field-situated expert reads,
averaged. ``holistic_mode = "audited"`` keeps the round-6 candidate: one
verified audit call then one scoring call. ``holistic_mode = "mean"`` keeps
the round-5 candidate: K venue-mean calls with no audit, averaged. The mode,
the sample count, the temperature, the main-text cap and the full-text cap
are declared here. They are not fit on a tune slice.

The pole helpers remain so the previous implied-score formula stays defined.
A package that stored only that pair still ranks from the stored samples.
New runs do not ask for a margin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LADDER_FLOOR = 1.0
LADDER_CEILING = 10.0
RELATIONS = frozenset({"better", "tie", "worse"})
# Declared distance from the weak pole to the strong pole. A margin of this
# size reaches the other pole. It is not chosen from a tune slice.
POLE_GAP = 6.0
MARGIN_MAX = POLE_GAP

# One forecast-panel call per paper. Matches the locked evaluation.
LOCKED_HOLISTIC_SAMPLES = 1
LOCKED_HOLISTIC_TEMPERATURE = 0.7
LOCKED_HOLISTIC_MODE = "forecast"
HOLISTIC_MODES = ("forecast", "rebuttal", "matched", "panel", "expert", "audited", "mean")
LOCKED_CANDIDATE = "forecast-k1"

# The matched panel reads the main text. The source text in the packet is cut
# at the first standalone reference-list heading found at or after this share
# of the text, and is capped at this many characters in every case. The cap is
# above the length of a main text within the venue's page limit. Declared, not
# fitted.
MAIN_TEXT_MAX_CHARS = 90_000
MAIN_TEXT_MIN_SHARE = 0.10
# The forecast panel and the discussed panel read the whole manuscript,
# reference list and appendix included. Only the source text of the longest
# manuscripts is clipped, at this many characters, so the per-paper cost has a
# bound; a manuscript under the cap is sent unchanged. Declared, not fitted.
FULL_TEXT_MAX_CHARS = 240_000

# Limits on the expert read. They bound the output; they are not scores.
MAX_PRIOR_WORK = 5
MAX_FIELD_CONTEXT_LINES = 4
MAX_REVIEW_POINTS = 3
# The venue review form scores soundness, presentation, and contribution 1–4.
FORM_MIN = 1
FORM_MAX = 4

# The simulated panel. Four reviewers per call, one per declared role. The
# official rating choices are the venue's; an integer off that list is kept
# in the mean and counted, not retried.
PANEL_SIZE = 4
OFFICIAL_RATINGS = (1, 3, 5, 6, 8, 10)
RATING_MIN = 1
RATING_MAX = 10
CONFIDENCE_MIN = 1
CONFIDENCE_MAX = 5


@dataclass(frozen=True)
class PanelRole:
    """One simulated reviewer: who they are and what they weigh. No digits."""

    id: str
    profile: str


PANEL_ROLES: tuple[PanelRole, ...] = (
    PanelRole(
        "specialist",
        "works on this exact problem and knows the closest prior work and the baselines the area "
        "compares against now. Checks whether the idea is new against what already exists, whether "
        "the comparison includes the methods the area would demand, and whether the gain is one the "
        "area would treat as meaningful.",
    ),
    PanelRole(
        "generalist",
        "a strong researcher from a neighbouring area, assigned by topic matching. Reads for whether "
        "the problem matters beyond its niche, whether the paper is clear and convincing on one "
        "reading, and whether the claims follow from what is shown. Rewards a clean idea with a clear "
        "story. Marks down a paper that cannot be followed or whose claims outrun the evidence.",
    ),
    PanelRole(
        "methodologist",
        "reads the experiments or the proofs for rigour. Checks baselines and tuning, scale, "
        "variance, ablations, matched comparisons, and whether the evidence covers the claim. "
        "Rewards careful evaluation. Marks down a result that could be noise, a comparison that "
        "favours the method, or a central claim left untested.",
    ),
    PanelRole(
        "senior",
        "an experienced researcher reading for significance. Asks whether the result is a step the "
        "area will build on and cite or a recombination of known parts, and whether the paper "
        "changes what a reader believes. Rates high when the paper excites them and low when the "
        "contribution is thin, however polished the paper is.",
    ),
)

PANEL_ROLE_IDS = tuple(role.id for role in PANEL_ROLES)

# Common ways a model names a role. Any word of its answer may match.
_PANEL_ROLE_ALIASES = {
    "expert": "specialist",
    "specialist": "specialist",
    "insider": "specialist",
    "generalist": "generalist",
    "general": "generalist",
    "adjacent": "generalist",
    "neighbouring": "generalist",
    "neighboring": "generalist",
    "outsider": "generalist",
    "methodologist": "methodologist",
    "methodology": "methodologist",
    "methodological": "methodologist",
    "empiricist": "methodologist",
    "experimentalist": "methodologist",
    "rigour": "methodologist",
    "rigor": "methodologist",
    "senior": "senior",
    "experienced": "senior",
    "veteran": "senior",
}


def panel_role_by_id(role_id: str) -> PanelRole:
    for role in PANEL_ROLES:
        if role.id == role_id:
            return role
    raise ValueError(f"unknown panel role {role_id}")


def normalize_reviewer_role(value: object) -> str | None:
    """The declared role id for a model's reviewer label, or ``None``.

    The first word that names a role decides, so ``Reviewer 2 (generalist)``
    is ``generalist``. An unmatched label is not an error; the review is kept
    and the role is unknown.
    """
    if not isinstance(value, str):
        return None
    for word in re.split(r"[^a-z]+", value.strip().lower()):
        if word in _PANEL_ROLE_ALIASES:
            return _PANEL_ROLE_ALIASES[word]
    return None


def panel_role_prompt_lines() -> list[str]:
    """One line per reviewer: the id and the profile."""
    return [f"- {role.id}: {role.profile}" for role in PANEL_ROLES]


def panel_mean(ratings: list[int] | tuple[int, ...], *, size: int = PANEL_SIZE) -> float:
    """Mean of one call's simulated official ratings. The per-call rank sample."""
    if len(ratings) != size:
        raise ValueError(f"a panel has exactly {size} ratings")
    for value in ratings:
        if isinstance(value, bool) or not isinstance(value, int) or value < RATING_MIN or value > RATING_MAX:
            raise ValueError(f"ratings must be integers from {RATING_MIN} to {RATING_MAX}")
    return round(sum(ratings) / len(ratings), 4)


# The matched panel. Four reviewers the venue would assign to this paper: all
# matched to its topic, varying in closeness and seniority, none an auditor.
# The order is from closest to broadest. No digits in a profile.
MATCHED_SEATS: tuple[PanelRole, ...] = (
    PanelRole(
        "insider",
        "works on this exact problem. Knows the closest prior work and the baselines the area "
        "compares against now, and judges the paper against them: whether the idea is new against "
        "what already exists, whether the comparison includes what the area would demand, and "
        "whether the gain is one the area would treat as real. Rates high when the paper moves the "
        "problem they work on, and low when it re-does something they know or skips the comparison "
        "that matters.",
    ),
    PanelRole(
        "junior",
        "a doctoral student or early postdoc in the same area, in their first or second year of "
        "reviewing. Reads the main text closely, checks whether the experiments test what the "
        "introduction promises, and trusts strong numbers across several benchmarks. Impressed by a "
        "clear method and a thorough evaluation; marks down a method they cannot follow, a missing "
        "obvious baseline, or a claim the tables do not show.",
    ),
    PanelRole(
        "neighbour",
        "an established researcher in the broader area, assigned by topic match but not working on "
        "this problem. Reads for whether the problem matters beyond its niche, whether the paper is "
        "clear and convincing in one reading, and whether the claims follow from what is shown. "
        "Rewards a clean idea with a clear story; marks down a paper that cannot be followed or "
        "whose claims outrun its evidence.",
    ),
    PanelRole(
        "senior",
        "an experienced researcher who has seen many papers in this area. Reads for significance: "
        "whether the result is a step the area will build on and cite or a recombination of known "
        "parts, and whether the paper changes what a reader believes. Rates high when the paper "
        "excites them and low when the contribution is thin, however polished the paper is.",
    ),
)

MATCHED_SEAT_IDS = tuple(seat.id for seat in MATCHED_SEATS)

# Common ways a model names a seat. Any word of its answer may match.
_MATCHED_SEAT_ALIASES = {
    "insider": "insider",
    "specialist": "insider",
    "expert": "insider",
    "closest": "insider",
    "junior": "junior",
    "student": "junior",
    "phd": "junior",
    "doctoral": "junior",
    "postdoc": "junior",
    "early": "junior",
    "neighbour": "neighbour",
    "neighbor": "neighbour",
    "neighbouring": "neighbour",
    "neighboring": "neighbour",
    "adjacent": "neighbour",
    "generalist": "neighbour",
    "broader": "neighbour",
    "senior": "senior",
    "experienced": "senior",
    "veteran": "senior",
    "established": "senior",
}


def matched_seat_by_id(seat_id: str) -> PanelRole:
    for seat in MATCHED_SEATS:
        if seat.id == seat_id:
            return seat
    raise ValueError(f"unknown matched seat {seat_id}")


def normalize_matched_seat(value: object) -> str | None:
    """The declared seat id for a model's reviewer label, or ``None``.

    The first word that names a seat decides, so ``Reviewer 3 (junior)`` is
    ``junior``. An unmatched label is not an error; the review is kept and the
    seat is unknown.
    """
    if not isinstance(value, str):
        return None
    for word in re.split(r"[^a-z]+", value.strip().lower()):
        if word in _MATCHED_SEAT_ALIASES:
            return _MATCHED_SEAT_ALIASES[word]
    return None


def matched_seat_prompt_lines() -> list[str]:
    """One line per reviewer: the seat id and the profile."""
    return [f"- {seat.id}: {seat.profile}" for seat in MATCHED_SEATS]


# The discussed panel. Three of the reviewers the venue would assign, in the
# round-8 roles whose ratings carried the human order: the topic specialist,
# the neighbouring-area generalist and the senior reader. The rigour seat is
# not among them. The profiles are the round-8 text, unchanged.
REBUTTAL_PANEL_SIZE = 3
REBUTTAL_SEAT_IDS = ("specialist", "generalist", "senior")
REBUTTAL_SEATS: tuple[PanelRole, ...] = tuple(panel_role_by_id(seat_id) for seat_id in REBUTTAL_SEAT_IDS)

# Common ways a model names a seat. Any word of its answer may match. A label
# that names the absent rigour seat does not match.
_REBUTTAL_SEAT_ALIASES = {
    "specialist": "specialist",
    "expert": "specialist",
    "insider": "specialist",
    "closest": "specialist",
    "generalist": "generalist",
    "general": "generalist",
    "adjacent": "generalist",
    "neighbour": "generalist",
    "neighbor": "generalist",
    "neighbouring": "generalist",
    "neighboring": "generalist",
    "outsider": "generalist",
    "broader": "generalist",
    "senior": "senior",
    "experienced": "senior",
    "veteran": "senior",
    "established": "senior",
}


def rebuttal_seat_by_id(seat_id: str) -> PanelRole:
    for seat in REBUTTAL_SEATS:
        if seat.id == seat_id:
            return seat
    raise ValueError(f"unknown discussed-panel seat {seat_id}")


def normalize_rebuttal_seat(value: object) -> str | None:
    """The declared seat id for a model's reviewer label, or ``None``.

    The first word that names a seat decides, so ``Reviewer 2 (generalist)``
    is ``generalist``. An unmatched label is not an error; the review is kept
    and the seat is unknown.
    """
    if not isinstance(value, str):
        return None
    for word in re.split(r"[^a-z]+", value.strip().lower()):
        if word in _REBUTTAL_SEAT_ALIASES:
            return _REBUTTAL_SEAT_ALIASES[word]
    return None


def rebuttal_seat_prompt_lines() -> list[str]:
    """One line per reviewer: the seat id and the profile."""
    return [f"- {seat.id}: {seat.profile}" for seat in REBUTTAL_SEATS]


# The forecast panel. The same three round-8 seats as the discussed panel, on
# the whole manuscript, with no discussion stage. Each reviewer gives the
# official rating they would most likely submit and a probability over the six
# official ratings; the expected rating under that probability is the
# reviewer's score. The profiles are the round-8 text, unchanged.
FORECAST_PANEL_SIZE = 3
FORECAST_SEAT_IDS = ("specialist", "generalist", "senior")
FORECAST_SEATS: tuple[PanelRole, ...] = tuple(panel_role_by_id(seat_id) for seat_id in FORECAST_SEAT_IDS)


def forecast_seat_by_id(seat_id: str) -> PanelRole:
    for seat in FORECAST_SEATS:
        if seat.id == seat_id:
            return seat
    raise ValueError(f"unknown forecast-panel seat {seat_id}")


def normalize_forecast_seat(value: object) -> str | None:
    """The declared seat id for a model's reviewer label, or ``None``.

    The seats and their aliases are the discussed panel's. An unmatched label
    is not an error; the review is kept and the seat is unknown.
    """
    return normalize_rebuttal_seat(value)


def forecast_seat_prompt_lines() -> list[str]:
    """One line per reviewer: the seat id and the profile."""
    return [f"- {seat.id}: {seat.profile}" for seat in FORECAST_SEATS]


def expected_rating(probabilities: list[float] | tuple[float, ...]) -> tuple[float, tuple[float, ...]]:
    """One reviewer's expected official rating and the normalised probabilities.

    ``probabilities`` holds one non-negative weight per official rating, in the
    order of ``OFFICIAL_RATINGS``. Weights that do not sum to one are
    normalised; the caller counts that. An all-zero or wrong-length list is an
    error. The expected rating is the sum of rating times probability, rounded
    to four places, and the normalised weights are rounded the same way.
    """
    if len(probabilities) != len(OFFICIAL_RATINGS):
        raise ValueError(f"a forecast has exactly {len(OFFICIAL_RATINGS)} probabilities")
    weights: list[float] = []
    for value in probabilities:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("probabilities must be numbers")
        number = float(value)
        if number != number or number < 0:
            raise ValueError("probabilities must be non-negative numbers")
        weights.append(number)
    total = sum(weights)
    if total <= 0:
        raise ValueError("probabilities must not all be zero")
    normalised = tuple(weight / total for weight in weights)
    expected = sum(rating * weight for rating, weight in zip(OFFICIAL_RATINGS, normalised))
    return round(expected, 4), tuple(round(weight, 4) for weight in normalised)


def forecast_mean(expectations: list[float] | tuple[float, ...], *, size: int = FORECAST_PANEL_SIZE) -> float:
    """Mean of one call's expected ratings. The per-call rank sample."""
    if len(expectations) != size:
        raise ValueError(f"a forecast panel has exactly {size} expected ratings")
    for value in expectations:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected ratings must be numbers")
        if value < RATING_MIN or value > RATING_MAX:
            raise ValueError(f"expected ratings must lie from {RATING_MIN} to {RATING_MAX}")
    return round(sum(float(value) for value in expectations) / len(expectations), 4)


AUDIT_VERDICTS = ("pass", "fail", "unclear")
FLAW_SEVERITIES = ("blocking", "major", "minor")
MAX_AUDIT_FLAWS = 4
# The diagnostic count maps verified passes onto the 1.0–10.0 rank scale.
CHECKS_FLOOR = 1.0
CHECKS_SPAN = 9.0


@dataclass(frozen=True)
class ContributionType:
    """One kind of paper and what reviewers in the area weigh for it."""

    id: str
    definition: str
    standard: str


CONTRIBUTION_TYPES: tuple[ContributionType, ...] = (
    ContributionType(
        "method",
        "a new model, algorithm, objective, or training recipe whose case rests on measured gains.",
        "a gain over the strongest current baseline under matched conditions, an ablation that "
        "credits the right component, and breadth across the settings the claim covers.",
    ),
    ContributionType(
        "theory",
        "a result whose case rests on proofs: a bound, a guarantee, a characterization, an "
        "impossibility.",
        "correctness, how general the assumptions are, and whether the result explains or predicts "
        "something practitioners see; small experiments are illustrations, not the evidence.",
    ),
    ContributionType(
        "analysis",
        "an empirical study of existing models or methods that establishes a finding rather than "
        "a new method.",
        "whether the finding is new, whether it holds across models, datasets, or scales, and "
        "whether it changes how people in the area think or work; no new state of the art is "
        "expected.",
    ),
    ContributionType(
        "benchmark",
        "a dataset, benchmark, evaluation protocol, or measurement tool.",
        "whether it measures something the area cannot already measure, coverage and validity of "
        "the data, headroom for current systems, documentation, and whether the area is likely to "
        "adopt it.",
    ),
    ContributionType(
        "system",
        "an implementation, library, or infrastructure whose case rests on cost, speed, scale, or "
        "usability.",
        "a measured gain in cost, speed, or usability at matched quality, generality beyond one "
        "setup, and release.",
    ),
    ContributionType(
        "application",
        "machine learning applied to a problem in another domain.",
        "whether the domain problem is real and the transfer from standard machine learning is not "
        "routine, with validation that domain experts would accept.",
    ),
    ContributionType(
        "position",
        "a position paper, perspective, or survey.",
        "whether the synthesis changes the agenda of the area; a position paper is rarely "
        "accepted at this venue on its own.",
    ),
)

CONTRIBUTION_TYPE_IDS = tuple(item.id for item in CONTRIBUTION_TYPES)

# Common ways a model names a type. The key is the first word of its answer.
_CONTRIBUTION_TYPE_ALIASES = {
    "methods": "method",
    "methodology": "method",
    "methodological": "method",
    "empirical": "method",
    "algorithm": "method",
    "algorithmic": "method",
    "model": "method",
    "theoretical": "theory",
    "analytical": "analysis",
    "empirical_analysis": "analysis",
    "study": "analysis",
    "dataset": "benchmark",
    "benchmarks": "benchmark",
    "evaluation": "benchmark",
    "systems": "system",
    "infrastructure": "system",
    "tool": "system",
    "applied": "application",
    "applications": "application",
    "survey": "position",
    "perspective": "position",
}


def contribution_type_by_id(type_id: str) -> ContributionType:
    for item in CONTRIBUTION_TYPES:
        if item.id == type_id:
            return item
    raise ValueError(f"unknown contribution type {type_id}")


def normalize_contribution_type(value: object) -> str:
    """The declared type id for a model's answer, or ``ValueError``.

    The first word decides, so ``benchmark/dataset`` is ``benchmark`` and
    ``Theory (learning theory)`` is ``theory``.
    """
    if not isinstance(value, str):
        raise ValueError("contribution_type must be a string")
    words = [word for word in re.split(r"[^a-z_]+", value.strip().lower()) if word]
    if not words:
        raise ValueError("contribution_type must name a type")
    first = words[0]
    first = _CONTRIBUTION_TYPE_ALIASES.get(first, first)
    if first not in CONTRIBUTION_TYPE_IDS:
        raise ValueError(f"contribution_type must be one of {', '.join(CONTRIBUTION_TYPE_IDS)}")
    return first


def contribution_type_prompt_lines() -> list[str]:
    """One line per type: the id, what it is, and what reviewers weigh."""
    return [
        f"- {item.id}: {item.definition} Reviewers weigh {item.standard}"
        for item in CONTRIBUTION_TYPES
    ]


def form_score(soundness: int, presentation: int, contribution: int) -> float:
    """1.0–10.0 from the review-form triad. Diagnostic. Declared, not fitted.

    Three ones map to 1.0 and three fours map to 10.0; each form point is one
    rank point.
    """
    values = (soundness, presentation, contribution)
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < FORM_MIN or value > FORM_MAX:
            raise ValueError(f"form scores must be integers from {FORM_MIN} to {FORM_MAX}")
    return round(LADDER_FLOOR + float(sum(values) - 3 * FORM_MIN), 4)


@dataclass(frozen=True)
class AuditCheck:
    """One fixed audit question. The wording is the whole definition."""

    id: str
    question: str
    passes: str
    fails: str


AUDIT_CHECKS: tuple[AuditCheck, ...] = (
    AuditCheck(
        "claim_quantified",
        "Is the headline contribution stated as a measurable result?",
        "The abstract or introduction states the main gain with a number, the metric, "
        "and the setting it was measured in.",
        "The main claim is only qualitative, or the number appears without the metric "
        "or the setting.",
    ),
    AuditCheck(
        "closest_prior_named",
        "Does the paper name its closest prior work and state a concrete technical difference?",
        "A specific prior method is named as the closest work, and the difference is a "
        "mechanism rather than a claim of being first.",
        "Related work lists areas or many papers without naming the closest one, or the "
        "stated difference is only novelty language.",
    ),
    AuditCheck(
        "strongest_baseline_compared",
        "Does the main comparison include the strongest prior method the paper itself identifies?",
        "The closest prior work, or the best prior result the paper cites, appears in the "
        "main results under the paper's main setting.",
        "The closest prior work is named but absent from the main comparison, or the "
        "baselines are ones the paper itself describes as older or weaker.",
    ),
    AuditCheck(
        "matched_comparison",
        "Are the baselines run under matched conditions?",
        "Data, compute or parameter budget, backbone, and tuning effort are the same for "
        "the method and the baselines, or the paper states how they were matched.",
        "The paper reports or shows a mismatch that favours its method: more data, more "
        "tuning, a larger model, or an input the baselines do not get.",
    ),
    AuditCheck(
        "variance_reported",
        "Does the headline result report run-to-run variability?",
        "The headline number comes with a standard deviation, a confidence interval, "
        "several seeds, or a significance test.",
        "The headline number is a single run, or an average with no spread.",
    ),
    AuditCheck(
        "gain_beyond_noise",
        "Is the headline gain larger than the reported variability, or consistent across the reported settings?",
        "The gain exceeds the reported spread, or it holds in every reported setting.",
        "The gain sits inside the reported spread, or it reverses or vanishes in some "
        "reported settings.",
    ),
    AuditCheck(
        "ablation_isolates_claim",
        "Does an ablation isolate the component the paper credits for the gain?",
        "Removing or replacing that component is reported, and the gain moves with it.",
        "There is no ablation of that component, or the ablation shows the gain comes "
        "from something else.",
    ),
    AuditCheck(
        "breadth_of_evidence",
        "Does the evidence span more than one dataset, task, scale, or domain that the claim covers?",
        "Results are reported on at least two settings the claim covers.",
        "One dataset or one setting carries a general claim.",
    ),
    AuditCheck(
        "failure_modes_reported",
        "Does the paper report specific cases where the method does not help?",
        "A named setting, dataset, or condition where the gain disappears is reported "
        "with its evidence.",
        "Limitations are generic, or no negative or neutral result is reported.",
    ),
    AuditCheck(
        "method_reproducible",
        "Could a reader reimplement the method from the paper?",
        "The method is given as equations or an algorithm with the key hyperparameters, "
        "or code is released.",
        "A step that matters for the result is described only in words, or key settings "
        "are missing.",
    ),
    AuditCheck(
        "claims_match_evidence",
        "Do the abstract and introduction claim only what the experiments test?",
        "Each stated property of the method is measured in the experiments.",
        "A claim is general but tested in one narrow setting, or a claimed property is "
        "never measured.",
    ),
    AuditCheck(
        "analysis_explains_gain",
        "Does the paper explain why the method works, not only that it works?",
        "Theory, diagnostics, or a targeted experiment connects the mechanism to the gain.",
        "The gain is reported without any analysis of its source.",
    ),
)

AUDIT_CHECK_IDS = tuple(check.id for check in AUDIT_CHECKS)


def audit_check_by_id(check_id: str) -> AuditCheck:
    for check in AUDIT_CHECKS:
        if check.id == check_id:
            return check
    raise ValueError(f"unknown audit check {check_id}")


def audit_check_prompt_lines() -> list[str]:
    """One line per question. The ids are the only machine-readable part."""
    return [
        f"- {check.id}: {check.question} Pass: {check.passes} Fail: {check.fails}"
        for check in AUDIT_CHECKS
    ]


def checks_score(passes: int, total: int = len(AUDIT_CHECKS)) -> float:
    """1.0–10.0 from the verified pass count. Diagnostic. Declared, not fitted."""
    if total < 1:
        raise ValueError("checks_score needs at least one check")
    if isinstance(passes, bool) or not isinstance(passes, int) or passes < 0 or passes > total:
        raise ValueError(f"passes must be an integer from 0 to {total}")
    return round(CHECKS_FLOOR + CHECKS_SPAN * passes / total, 4)


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
    """Mean of the weak-reject and strong-accept implied scores. The previous locked rank."""
    if len(implied) != len(SYNTHETIC_ANCHORS):
        raise ValueError("isolate rank needs one implied score for each pole")
    return round(sum(implied) / len(implied), 4)
