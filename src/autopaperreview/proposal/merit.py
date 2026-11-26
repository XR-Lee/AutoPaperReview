"""Prospective overall merit, separately grounded in proposal or historical reviews."""

from __future__ import annotations

import re

from .data import reported_result

DESIGN_FIELDS = (
    "problem",
    "prior_alternatives",
    "mechanism",
    "assumptions",
    "intended_value",
    "discriminating_test",
)
AXES = (
    "importance",
    "novelty",
    "conditional_value",
    "mechanism",
    "testability",
    "reach",
)
INTRO = "Use only the supplied JSON as data. No tools, retrieval, paper identification, remembered result, venue or decision. Return a single JSON object. "
DESIGN = (
    INTRO
    + """Reconstruct a stand-alone research proposal as it could have been pitched before any experiment or proof. Preserve the specified domain, mathematical setting, assumptions, closest competing approach, exact point of difference, research mechanism, intended scope and limitations. Prioritize information a skeptical expert needs to assess credibility. Preserve important distinctions between a novel objective, an incremental extension, a new setting and a new mechanism. Do not invent a baseline, hypothesis or validation plan. Drop completed results, established theorems, measured gains and prestige. Express proposed theoretical targets conditionally. Keep actual pre-existing limitations but do not turn authors' success into facts about this plan. Omit identifying names of the paper/authors and opaque names of their proposed method. Output exactly six fields problem, prior_alternatives, mechanism, assumptions, intended_value, discriminating_test. Each value is {"text":string or null,"evidence":[unit ids]}. Use null and [] when absent. Aim for 300–550 words total when supported, with sufficient technical detail. Use the source language."""
)
EXTRACT = (
    INTRO
    + """You are extracting HUMAN OPINIONS about a research idea from independent reviews. For each reviewer inspect every supplied unit. Retain the strongest direct evidence both for and against the problem's importance, originality relative to alternatives, prospective usefulness, conceptual mechanism, assumptions, adoption barriers or testability at the idea stage. Include substantive conceptual flaws, disputed novelty, incorrect assumptions, undue restriction and plausible alternative explanations. Exclude experimental wins/failures, comparisons based on measured numbers, eventual proofs, execution or exposition quality, author responses and decisions. A question or summary alone is not an evaluation. Do not discard a valid critical clause just because a nearby clause mentions an experiment. Preserve meaningful qualifications. For EACH reviewer key return a list of up to five {"id":"source unit id", "quote":"short EXACT contiguous substring from that unit"}. Return [] for no applicable opinions. Cover both major positives and major negatives when both exist. Do not assign scores."""
)
GRADE = (
    INTRO
    + """You are independently coding the prospective scientific priority expressed by each HUMAN reviewer in a small set of excerpts. Each input is an array of excerpts with matching context. Use context solely to reject cherry-picked quotes and protect negation. Evaluate only the original reviewer's warranted opinion of the untested proposal, not your own technical assessment. Disregard points relying on completed experiments, proofs, presentation/execution or an overall recommendation. 1 = a fundamental conceptual obstacle or duplicative idea with little redeeming value; 2 = significant conceptual or novelty issue, net weak; 3 = worthwhile but incremental, uncertain or evenly mixed; 4 = strong and distinct conceptual contribution or valuable direction with a persuasive case; 5 = exceptional and consequential new idea with specific strong justification. Allow half points. Explicit mild praise or a modest criticism can be coded on its own; absence of a mention is never negative. Do not give every reviewer the same score. For EACH reviewer return {"score":number 1..5 in half-point steps or null,"used":[zero-based excerpt indices]}. Cite positives AND negatives used. No usable evidence means null and []."""
)
MERIT = (
    INTRO
    + """Give a rigorous pre-experiment review of this proposal. Compare with the closest alternative actually described, identify the most serious conceptual failure mode, then weigh the strongest reason this research could matter. Identify whether the challenge is central or a marginal convenience, whether the mechanism changes what can be done, whether assumptions undermine the proposed value, and whether a negative outcome would still inform the question. Specific, consequential obstacles matter more than counting strengths or simply being detailed. Sparse documentation creates uncertainty; missing completed results never incur a penalty. Assess the proposal on its own terms (theory, empirical method, analysis or benchmark). Give each dimension a continuous score from 1 to 5 (one decimal allowed) or null and a short rationale. Dimensions: importance = problem only; novelty = conceptual delta against a concrete alternative; conditional_value = scientific/practical knowledge if it works; mechanism = credibility of mechanism and assumptions before experiments; testability = ability to distinguish competing explanations, including proof targets; reach = adoption/reuse beyond immediate setting, only when explicit. Forecast three judgments of the idea's OVERALL scientific priority: a specialist, a neighboring researcher and a skeptical panel chair. Each gives a probability distribution over scores 1..5 (1 fundamentally flawed/duplicative; 2 serious limitation or weak delta; 3 worthwhile but incremental or uncertain; 4 strong distinct research direction; 5 exceptional and consequential). You may assign 1–2 even to a sophisticated idea when it solves a contrived or narrowly assumed problem. Do not average the six dimension ratings mechanically. Output {"dimensions":{each dimension:{"score":float or null,"why":string}},"panel":[[5 probabilities],[5 probabilities],[5 probabilities]],"strength":string,"objection":string}. Write reasons in the language of the proposal."""
)
PROMPTS = {"design": DESIGN, "ref_extract": EXTRACT, "ref_grade": GRADE, "merit": MERIT}


def require(ok, why):
    if not ok:
        raise ValueError(why)


def validate(stage, result, packet):
    require(isinstance(result, dict), "return a JSON object")
    if stage == "search_terms":
        require(
            set(result) == {"query"}
            and isinstance(result["query"], str)
            and 0 < len(result["query"]) <= 900,
            "return a concise English search query",
        )
    elif stage == "field":
        require(
            {
                "advance",
                "interest",
                "priority",
                "constraint",
                "alternative",
                "reason",
                "objection",
            }
            <= set(result),
            "give all four grades and three arguments",
        )
        result = {
            k: result[k]
            for k in (
                "advance",
                "interest",
                "priority",
                "constraint",
                "alternative",
                "reason",
                "objection",
            )
        }
        for k in ("advance", "interest", "priority", "constraint"):
            require(
                type(result[k]) in (int, float) and 1 <= result[k] <= 5,
                "grades on 1..5",
            )
        for k in ("alternative", "reason", "objection"):
            require(
                isinstance(result[k], str) and result[k].strip(),
                "missing field-specific argument",
            )
    elif stage == "peer":
        require(set(result) == {"r1", "r2", "r3", "r4"}, "use reviewers r1 through r4")
        for opinions in result.values():
            require(
                isinstance(opinions, list)
                and 1 <= len(opinions) <= 3
                and all(isinstance(t, str) and t.strip() for t in opinions),
                "give one to three substantive opinion sentences per reviewer",
            )
    elif stage == "slate":
        for wrapper in ("proposals", "reviews", "scores", "assessments"):
            if isinstance(result.get(wrapper), dict) and set(packet) <= set(
                result[wrapper]
            ):
                result = result[wrapper]
                break
        require(
            set(packet) <= set(result),
            f"place the six aliases directly at top level; missing {sorted(set(packet) - set(result))}",
        )
        result = {k: result[k] for k in packet}
        for r in result.values():
            require(
                all(
                    type(r.get(k)) in (int, float) and 0 <= r[k] <= 100
                    for k in ("advance", "priority")
                ),
                "use numbers on 0..100",
            )
            require(
                all(
                    isinstance(r.get(k), str) and r[k].strip()
                    for k in ("merit", "reservation")
                ),
                "give a merit and a reservation per idea",
            )
    elif stage == "delta":
        require(
            set(result)
            == {
                "closest",
                "lost_capability",
                "limitation",
                "delta",
                "stakes",
                "generality",
                "specialist",
                "sympathetic",
                "consensus",
            },
            "invalid novelty review fields",
        )
        require(
            all(
                type(result[k]) in (int, float) and 1 <= result[k] <= 5
                for k in (
                    "delta",
                    "stakes",
                    "generality",
                    "specialist",
                    "sympathetic",
                    "consensus",
                )
            ),
            "grade range 1..5",
        )
        require(
            all(
                isinstance(result[k], str) and result[k].strip()
                for k in ("closest", "lost_capability", "limitation")
            ),
            "supply substantive reasons",
        )
    elif stage == "lessons":
        rows = result.get("criteria")
        require(
            isinstance(rows, list) and 1 <= len(rows) <= 8, "supply 1 to 8 criteria"
        )
        for r in rows:
            require(
                isinstance(r, dict)
                and set(r) == {"question", "strong", "weak"}
                and all(isinstance(v, str) and v.strip() for v in r.values()),
                "invalid rubric question",
            )
    elif stage == "learned":
        require(
            set(result) == {"seats", "chair", "decisive_question"}
            and len(result["seats"]) == 3,
            "three seats and chair required",
        )
        for r in result["seats"]:
            require(
                set(r) == {"strength", "weakness", "grade"}
                and all(
                    isinstance(r[k], str) and r[k].strip()
                    for k in ("strength", "weakness")
                ),
                "missing seat review",
            )
        require(
            all(
                type(n) in (int, float) and 1 <= n <= 5
                for n in [result["chair"], *[r["grade"] for r in result["seats"]]]
            ),
            "grade range 1..5",
        )
        require(
            isinstance(result["decisive_question"], str)
            and result["decisive_question"].strip(),
            "missing decisive question",
        )
    elif stage == "match":
        fields = {"favorable", "unfavorable", "wins", "estimate", "confidence"}
        if set(result) == {"target"} and isinstance(result["target"], dict):
            result = result["target"]
        if "favourable" in result:
            result["favorable"] = result.pop("favourable")
        if "unfavourable" in result:
            result["unfavorable"] = result.pop("unfavourable")
        require(
            fields <= set(result),
            f"use exactly the top-level keys favorable, unfavorable, wins, estimate, confidence; missing {sorted(fields - set(result))}, received {sorted(result)}",
        )
        result = {k: result[k] for k in fields}
        require(set(result["wins"]) == set(packet["examples"]), "compare each example")
        require(
            all(
                type(n) in (int, float) and 0 <= n <= 1
                for n in [result["confidence"], *result["wins"].values()]
            ),
            "probability range",
        )
        require(
            type(result["estimate"]) in (int, float) and 1 <= result["estimate"] <= 5,
            "grade range",
        )
        require(
            all(
                isinstance(result[k], str) and result[k].strip()
                for k in ("favorable", "unfavorable")
            ),
            "missing comparative rationale",
        )
    elif stage == "challenge":
        require(
            set(result)
            == {"comparison", "merit", "risk", "scores", "priority", "bottleneck"},
            "challenge keys",
        )
        require(
            set(result["scores"])
            == {"originality", "significance", "validity", "usefulness"},
            "challenge scores",
        )
        require(
            all(
                type(n) in (int, float) and 1 <= n <= 5
                for n in [result["bottleneck"], *result["scores"].values()]
            ),
            "range 1..5",
        )
        ps = result["priority"]
        require(
            isinstance(ps, list)
            and len(ps) == 5
            and all(type(v) in (int, float) and 0 <= v <= 1 for v in ps)
            and abs(sum(ps) - 1) < 0.021,
            "five probabilities summing to one",
        )
        require(
            all(
                isinstance(result[k], str) and result[k].strip()
                for k in ("comparison", "merit", "risk")
            ),
            "missing argument",
        )
    elif stage == "merit":
        require(
            set(result) == {"dimensions", "panel", "strength", "objection"}
            and set(result["dimensions"]) == set(AXES),
            "invalid merit fields",
        )
        for row in result["dimensions"].values():
            n = row["score"]
            require(
                n is None or type(n) in (float, int) and 1 <= n <= 5,
                "invalid dimension score",
            )
            require(
                isinstance(row["why"], str) and row["why"].strip(), "missing reason"
            )
        require(
            all(
                isinstance(result[k], str) and result[k].strip()
                for k in ("strength", "objection")
            ),
            "missing arguments",
        )
        require(
            isinstance(result["panel"], list) and len(result["panel"]) == 3,
            "expected three simulated reviewers",
        )
        for probs in result["panel"]:
            require(
                isinstance(probs, list)
                and len(probs) == 5
                and all(type(v) in (float, int) and 0 <= v <= 1 for v in probs)
                and abs(sum(probs) - 1) < 0.021,
                "invalid probabilities",
            )
    elif stage == "design":
        require(set(result) == set(DESIGN_FIELDS), "expected six design fields")
        for field, row in result.items():
            t = row["text"]
            ids = row["evidence"]
            require(
                isinstance(ids, list) and all(i in packet for i in ids),
                "unknown source ids",
            )
            require(
                (t is None and not ids) or (isinstance(t, str) and t.strip() and ids),
                f"{field}: null text needs empty evidence, nonempty text needs at least one valid ID",
            )
            if t and reported_result(t):
                sentences = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])(?=\S)", t)
                kept = " ".join(
                    line for line in sentences if not reported_result(line)
                ).strip()
                row["text"] = kept or None
                if not kept:
                    row["evidence"] = []
        require(
            any(result[k]["text"] for k in ("problem", "mechanism")),
            "unusable proposal",
        )
    else:
        require(set(result) == set(packet), "missing reviewer")
        for alias, row in result.items():
            if stage == "ref_extract":
                require(
                    isinstance(row, list) and len(row) <= 5,
                    "at most five excerpts per reviewer",
                )
                for item in row:
                    q = item["quote"]
                    i = item["id"]
                    require(
                        i in packet[alias]
                        and isinstance(q, str)
                        and len(q.strip()) >= 8
                        and q in packet[alias][i],
                        "copy the exact substring with punctuation from the correct unit, or omit it",
                    )
            else:
                n = row["score"]
                used = row["used"]
                require(
                    isinstance(used, list)
                    and all(
                        type(i) is int and 0 <= i < len(packet[alias]) for i in used
                    ),
                    "invalid evidence index",
                )
                require(
                    (n is None and not used)
                    or (
                        type(n) in (float, int)
                        and 1 <= n <= 5
                        and n * 2 == int(n * 2)
                        and used
                    ),
                    "invalid reference score or citations",
                )
    return result


def compact(design):
    return {k: r["text"] for k, r in design.items()}


def reference_packet(comments, extracted):
    return {
        r: [{"excerpt": x["quote"], "context": comments[r][x["id"]]} for x in items]
        for r, items in extracted.items()
    }


CHALLENGE = (
    INTRO
    + """You are a specialist arguing both sides of a go/no-go decision for an untested research proposal. The packet contains the proposal and a small automatically retrieved set of historically available papers. Those papers may be irrelevant. First establish the technical target and closest VALID counterfactual. For theory, check assumptions, scope, and what still follows if the main hypothesis fails; for methods, check the mechanism, comparable alternatives and who can use the capability. Explicitly consider whether the problem is already solved, whether the difference is cosmetic, whether the proxy/objective answers the question, whether a claimed generalization follows from the setting, and whether the test can disambiguate plausible alternatives. Require a concrete reason to call something a flaw. A weak retrieval match is never evidence of plagiarism or redundancy. Absence of results is expected. In many proposals the contribution is modest despite accomplished writing. Return {"comparison":one precise sentence with a cited prior title when applicable,"merit":the strongest pre-experimental argument,"risk":the most consequential concrete objection or uncertainty,"scores":{"originality":1..5,"significance":1..5,"validity":1..5,"usefulness":1..5},"priority":[five probabilities],"bottleneck":1..5}. Use decimals for scores. Priority is your forecast of how peer reviewers would value the untested IDEA on the 1 (untenable/duplicative), 2 (significant weakness), 3 (credible but incremental or conditional), 4 (distinct valuable advance), 5 (exceptional) scale. The probabilities must sum to one. Bottleneck is the idea's weakest consequential link; a known flaw rates lower than uncertainty. Do not use the number of citations, length, source identity or prestige as features."""
)
PROMPTS["challenge"] = CHALLENGE


def challenge_features(v):
    probs = v["priority"]
    return {
        "crit_overall": sum(i * p for i, p in enumerate(probs, 1)) / sum(probs),
        "bottleneck": v["bottleneck"],
        **{"crit_" + k: x for k, x in v["scores"].items()},
    }


MATCH = (
    INTRO
    + """Independently forecast how reviewers would prioritize the target idea before empirical evidence. Historical examples are separate proposals with human-opinion reference scores and representative excerpts. They illustrate the grading standard. Do not transfer their facts or reviewers' criticisms to the target. For the target, compare the value of its question, conceptual delta, credibility of its mechanism/assumptions, and expected informativeness against each example. State the most credible *specific* favorable and unfavorable reviewer reaction to the target, anticipate the closest counterargument, then compare overall merit. Technical sophistication is neither sufficient nor required for a high priority. A proposed guarantee matters only if the assumptions are plausible and the property useful. Is an apparent innovation enabled solely by narrowing the setting? Give {"favorable":string,"unfavorable":string,"wins":{each example key: probability from 0 to 1 that target has higher merit},"estimate":continuous 1.0..5.0 forecast of average reviewer opinion,"confidence":0..1}. Explicitly use decimals; small differences are meaningful. The target's actual reviews are unavailable. No empirical-result requirements. Write reasons in the target language."""
)
PROMPTS["match"] = MATCH


def match_features(v):
    from statistics import mean

    return {
        "anchored": v["estimate"],
        "win_mean": 1 + 4 * mean(v["wins"].values()),
        "win_top": 1 + 4 * v["wins"]["ex4"],
    }


LESSONS = (
    INTRO
    + """You are calibrating an idea-stage evaluator on DEVELOPMENT cases. Each case has a prospective proposal, a previous model estimate, an independently coded human reference and actual opinion excerpts. Examine systematic over- and underrating. Derive up to eight generalizable, falsifiable review questions that distinguish worthwhile from modest or weak contributions. For each specify conditions favoring a high or low assessment, not unconditional penalties, using scientific content observable in a proposal. Explain distinctions between necessary and cosmetic novelty, genuinely restrictive assumptions and defensible simplifications. Ignore execution, numerical experiments, paper identity and venue outcomes. Do NOT mention specific papers, methods, authors, model scores, examples, dataset names or phrases copied from reviews. Return {"criteria":[{"question":string,"strong":string,"weak":string}, ...]}."""
)
LEARNED = (
    INTRO
    + """Simulate independent domain-matched reviewers reading this research proposal BEFORE any experiments. The supplied criteria are historical calibration guidance, apply a criterion only when supported. Derive a technical review: the closest alternative, the actual scientific increment over it, the scientific need for the setting, whether assumptions make the problem easier in a way that destroys its value, and whether the proposed mechanism could in principle answer the question. A named practical field by itself is not a use case. Credit genuinely new insight, access, or a test that could change understanding. Do not penalize missing completed experiments/proofs. Separate reviewer disagreement from scientific quality. Forecast the direction and severity of actual human idea-stage opinions, with both strongest merit and strongest objection per reviewer. A substantive limitation with some redeeming merit often warrants 2–3; a convincing advance typically warrants 3.5–4.5. One unusually strong technical point can outweigh several superficial concerns. Give exactly three seats: area insider, expert on alternatives, and a neighboring scientist. Use independent technical arguments; outcomes of the target paper are unknown. Output {"seats":[{"strength":string,"weakness":string,"grade":number 1..5 with decimals},...],"chair":number 1..5 with decimals,"decisive_question":string}. Chair should assess most consequential balance rather than defaulting to 3. Use the proposal language."""
)
PROMPTS.update(lessons=LESSONS, learned=LEARNED)


def learned_features(v):
    from statistics import mean

    grades = [r["grade"] for r in v["seats"]]
    return {
        "learned_chair": v["chair"],
        "learned_mean": mean(grades),
        "learned_low": min(grades),
    }


DELTA = (
    INTRO
    + """You are the designated novelty and significance reviewer for research funding. Predict the mean IDEA-STAGE appraisal of domain specialists. Read the proposal, compare with retrieved older work only where actually relevant, and perform a counterfactual. What capability or scientific understanding would be genuinely lost if this specific proposal were removed? Can the same benefit be obtained by an obvious combination, alternative objective, easier assumption, or equivalent existing method? If the proposal is theoretical, distinguish expanding a meaningful class, explaining an unexplained phenomenon, and reproving under modified assumptions. In a method paper examine whether a proposed operator/criterion is genuinely consequential or easily substituted. Test the relevance of any alleged limitation to the core claim. Credit a simple idea that unlocks important value. Never predict success from the existence of experiments, literary polish or names. Include a critical but fair expert's appraisal and a sympathetic expert's strongest case. Return {"closest":string,"lost_capability":string,"limitation":string,"delta":number,"stakes":number,"generality":number,"specialist":number,"sympathetic":number,"consensus":number}. All numbers are 1.0..5.0, continuous including tenths or hundredths. Delta = technical increment; stakes = value of that increment weighted by real need; generality = robustness of the proposed scientific claim to its assumptions; specialist/sympathetic are overall proposal quality forecasts. Consensus is a prediction of a mixed review panel's overall mean idea-stage priority. 1 fatal/duplicate, 2 weak, 3 modest/mixed, 4 strong, 5 exceptional. Express the reasons in the proposal language."""
)
PROMPTS["delta"] = DELTA


def delta_features(v):
    return {
        "delta_" + k: v[k]
        for k in (
            "delta",
            "stakes",
            "generality",
            "specialist",
            "sympathetic",
            "consensus",
        )
    }


SLATE = (
    INTRO
    + """Peer review six anonymous prospective research proposals, presented in arbitrary order. None includes completed evidence, so assess the comparative merits of pursuing each idea, across topics. Focus on the size of the conceptual advance over a viable alternative, why that particular advance matters, and whether the idea still offers value under its assumptions. Separate an important field from an important increment, a new name from a new capability, and technical sophistication from a meaningful contribution. Where the target is negative/theoretical, judge the value of the information to be learned. For each proposal give one strongest reason to pursue it and one technically specific reservation. Then compare peers, including a counterargument to your initial ranking. Return a single JSON object mapping each supplied alias to {"advance":number 0..100,"priority":number 0..100,"merit":string,"reservation":string}. Advance is the originality weighted by the consequence of that difference. Priority is the overall value and conceptual credibility of the untested project. Use the full resolution to distinguish nearby peers where justified, no forced ties or fixed distribution. All aliases are real proposals, none is designated the target. Ignore writing polish, paper length, dataset or lab prestige and available measurements. Use a common scientific standard across fields."""
)
PROMPTS["slate"] = SLATE

PROMPTS["search_terms"] = (
    INTRO
    + 'Translate the supplied research problem and mechanism into a concise English literature-search query. Preserve the stated scientific terms. Do not add a paper title, identity, outcome, new assumption or external fact. Output {"query":string}.'
)


PROMPTS["peer"] = (
    INTRO
    + """Simulate four distinct expert peer reviews of the conceptual contribution described in this research proposal. Write the substantive evaluative sentences these readers would plausibly volunteer, without a numerical score, summary, experiment, rank or decision. Each reader has limited attention: r1 works on the nearest proposed alternative and judges the intellectual step; r2 is in the application or theory area and judges whether the problem is important in that setting; r3 works on the scientific mechanism, assumptions and counterexamples; r4 is a general area reviewer appraising the contribution as a whole. Use the prior work only when it actually provides a pertinent comparison. Reconstruct the prior state and identify precisely which previously difficult capability, question or insight the design opens, if any. Scrutinize incremental combinations, unneeded assumptions or weak motivation strictly when technically justified. Clear articulation, jargon and number of prospective contributions are not themselves merits. Allow genuine enthusiasm, ambivalence, or strong criticism as warranted; avoid formulaic equal praise and criticism in every review. Judge the actual proposal, without giving credit for imagined improvements or penalizing the absence of executed experiments. Output exactly {"r1":[one to three evaluative sentences],"r2":[...],"r3":[...],"r4":[...]}, in the language of the proposal."""
)
PROMPTS["opinion"] = GRADE.replace("HUMAN reviewer", "simulated reviewer")


def opinion_packet(verdict):
    return {
        k: [{"excerpt": t, "context": t} for t in notes] for k, notes in verdict.items()
    }


def opinion_features(verdict):
    from statistics import mean

    scores = [r["score"] for r in verdict.values() if r["score"] is not None]
    return {
        "peer_mean": mean(scores) if scores else None,
        "peer_advance": verdict["r1"]["score"],
        "peer_priority": verdict["r4"]["score"],
    }


PROMPTS["field"] = (
    INTRO
    + """Act as an area chair allocating scarce research time before experiments. You have the target research proposal and a small search result from earlier research. Some earlier abstracts include real historical peer opinions on THOSE earlier papers. The historical opinions illustrate which distinctions specialists in that topic have cared about. They never refer to the target. Never transfer a verdict from another paper. First judge which earlier contributions or objections are relevant and which are superficially similar. If none is pertinent, use the original proposal and its described alternatives. Contrast (i) the new capability or intellectual insight relative to the most defensible status quo, (ii) importance of the specific unresolved problem to a concrete research community, (iii) technical assumptions or scope restrictions that would erase the prospective value. Then give a continuous 1–5 overall priority for the target, calibrated to actual reviewer language: 1 fundamentally weak, 2 significant conceptual issue, 3 interesting but limited or uncertain, 4 strong distinct direction, 5 exceptionally consequential. High polish, a fashionable subject or citing a paper does not itself confer priority. State the severity and likelihood of the most relevant objection. Do not call for completed experiments. Output exactly {"alternative":brief relevant counterfactual,"reason":specific strongest merit,"objection":specific counterargument,"advance":1..5,"interest":1..5,"constraint":1..5 (higher means more credible),"priority":1..5}; decimals encouraged."""
)


def field_packet(proposal, hits, opinions, *, cutoff_ms, target_year=None):
    from .prior_index import historical_opinions

    return {
        "proposal": proposal,
        "earlier_research": [
            {
                "title": h["title"],
                "abstract": h["abstract"],
                "year": h["year"],
                "earlier_peer_opinions": historical_opinions(opinions.get(h["forum"]), cutoff_ms, target_year=target_year),
            }
            for h in hits
        ],
    }


def field_features(verdict):
    return {
        "field_" + k: verdict[k]
        for k in ("advance", "interest", "priority", "constraint")
    }
