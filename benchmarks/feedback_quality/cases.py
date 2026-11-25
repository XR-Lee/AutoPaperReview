"""Original, fictional research vignettes. Each pair changes one passage."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    before: tuple[str, ...]
    clean: str
    defect: str
    after: tuple[str, ...]
    diagnosis: str
    evidence: str
    repair: str

    def manuscript(self, variant: str) -> str:
        changed = {"clean": self.clean, "defect": self.defect}[variant]
        paragraphs = (*self.before, changed, *self.after)
        headings = ("Research question", "Design", "Procedure", "Interpretation")
        sections = [f"§{i}. {heading}\n{body}"
                    for i, (heading, body) in enumerate(zip(headings, paragraphs, strict=True), 1)]
        return "\n\n".join([self.title, *sections]) + "\n"


SCENARIOS = [
    Scenario(
        "crate_split", "Recognition of Crate Markings Across Repainting Sessions",
        before=(
            "A stencil reader is meant to identify wear on a newly arrived shipping crate from a burst of stills. A crate keeps its dent pattern across visits but can acquire a new coat of paint.",
            "The archive follows 80 crates through three visits apiece. The same camera takes nine stills per visit. Exposure correction is fit on the training portion. Cropping and channel normalization are identical for both readers.",
        ),
        clean="We shuffled a list of physical crate serials and put the first quarter on the evaluation list. Every visit and still inherited the assignment of its serial. Only the remaining serials were used to fit the readers.",
        defect="We shuffled a list of visits and put the first quarter on the evaluation list. Every still inherited the assignment of its visit. An inventory confirmed that each evaluation serial also had an earlier or later visit in the fitting archive.",
        after=(
            "The proposed reader had the smaller wear-grade error on the evaluation list. We interpret that difference as evidence about a crate the workshop has never handled before.",
        ),
        diagnosis="Assigning visits to folds reuses physical crate identities and fails to test transfer to unseen crates.",
        evidence="The evaluation serials also appear in the fitting archive (§3), although §4 claims unseen crates. Merely noting that adjacent stills within a visit correlate misses the shared serial across visits.",
        repair="Split by physical serial across all visits and refit and evaluate on disjoint crates.",
    ),
    Scenario(
        "bell_horizon", "Advance Forecasts of Jams in a Bell Casting Line",
        before=(
            "A foundry must arrange a spare cart at the end of hour h for use in hour h+1. A model predicts whether a trolley will jam during h+1 from a rolling log of bell movements.",
            "The last six weeks were set aside chronologically. All smoothing widths were selected in the earlier weeks. A predictor row has one closing-load value, one air-temperature value and one smoothed vibration value, labelled with its issue hour h.",
        ),
        clean="For the smoothing channel the logger averages samples whose hour stamp runs from h−4 through h inclusive. It buffers the resulting row at the close of h; that row is exported to both the forecasting service and the evaluator.",
        defect="For the smoothing channel the logger averages samples whose hour stamp runs from h−2 through h+2 inclusive. It buffers that row once all the samples have arrived; the evaluator indexes the buffered rows by their issue hour h.",
        after=(
            "Averaging improved jam recall at the same dispatch rate. We conclude that the morning supervisor could obtain this benefit while dispatching the spare at the close of h.",
        ),
        diagnosis="The centered vibration window supplies observations after the forecast issue time, including the forecast horizon.",
        evidence="§3 uses h+1 and h+2 to construct a row indexed by h; §1/§4 require availability at the close of h. Mentioning a chronological split alone is insufficient.",
        repair="Recompute rows using a trailing window available by dispatch time and rerun evaluation at the fixed dispatch rate.",
    ),
    Scenario(
        "loom_precision", "Estimating the Benefit of a Loom-Tuning Card",
        before=(
            "We estimate how much a printed tuning card improves the number of intact ribbons per shift. The target interval should cover uncertainty from which mills participate in a subsequent rollout.",
            "Twenty mills each provided one card shift and one plain shift. Their order was randomized within each mill. One automatic counter records every ribbon. The comparison uses the difference in intact ribbons per shift at each mill, then averages the twenty differences.",
        ),
        clean="For each interval replicate we sampled twenty mills with replacement. A sampled mill supplied its complete pair of shifts and all of their ribbons. The endpoints are percentiles of the replicate means.",
        defect="For each interval replicate we held the twenty mills and their shifts fixed. We sampled ribbons with replacement within each shift, re-totaled each counter and averaged the twenty new differences. The endpoints are percentiles of the replicate means.",
        after=(
            "The reported interval is narrow. We use it to quantify sampling uncertainty in the mean improvement across independently participating mills.",
        ),
        diagnosis="Resampling ribbons within fixed mills omits the between-mill component of uncertainty in the population mean.",
        evidence="§3 holds mills fixed and resamples only ribbons. The randomized treatment order in §2 does not change that interval's sampling unit, which §1/§4 specify as mills.",
        repair="Resample whole mill pairs or fit inference accounting for independent mill-level effects while preserving the paired shifts.",
    ),
    Scenario(
        "pottery_denominator", "A Sorting Rule for Glaze-Firing Defects",
        before=(
            "A sorter may send an uncertain piece to a human or issue an automatic grade. The workshop counts the fraction of all incoming pieces correctly given an automatic grade; a referral contributes zero to this yield.",
            "The incumbent and proposed sorters each receive the same fixed stream of one hundred pieces. Grades of all pieces were checked after firing. No human grading outcome is credited to either machine. The two machines have different referral thresholds.",
        ),
        clean="The incumbent issued 90 grades and got 72 right. The proposal issued 50 and got 45 right. We used the incoming stream size as the denominator in each case. The latter machine trades a lower automated yield for greater precision on issued grades.",
        defect="The incumbent issued 90 grades and got 72 right. The proposal issued 50 and got 45 right. We divided each correct count by the number of grades issued. The latter machine delivers a greater automated yield on incoming pieces.",
        after=(
            "The workshop will select the machine with the larger estimated yield using the comparison reported above. Referral delay and the cost of human grading are outside this particular decision rule.",
        ),
        diagnosis="Conditional accuracy among issued grades is substituted for the prespecified all-piece automated yield, reversing the ordering.",
        evidence="§3 divides by issued grades despite §1 counting every arrival. The 72 versus 45 correct outputs for the same 100 arrivals expose the reversal.",
        repair="Divide correct automatic grades by all arrivals, and report coverage or conditional accuracy separately if desired.",
    ),
    Scenario(
        "puzzle_shortest", "Tracking Efficient Paths on a Puzzle Board",
        before=(
            "A battery-driven token traverses a puzzle board. Edge labels are durations in ticks. The design goal is a route that uses the fewest ticks from S to Z.",
        ),
        clean="Each board has any number of squares and directed links. Traversing any link, including a shortcut, takes exactly one tick. The published layout has links S to Z, S to M and M to Z.",
        defect="Each board has any number of squares and directed links. The published layout has S to Z costing nine ticks, S to M costing two and M to Z costing two. Durations are positive integers on every board.",
        after=(
            "The token's solver is a breadth-first queue. It marks a square on first discovery and returns the path to Z as soon as Z is discovered. It consults adjacency, never the edge labels.",
            "We call the returned route time-optimal for every admissible board. Queue storage and commissioning costs are evaluated separately and have no role in the definition of optimality.",
        ),
        diagnosis="The unweighted search optimizes hop count but need not minimize unequal traversal times.",
        evidence="§3's first-discovery queue and §2's direct nine-tick edge versus the four-tick two-edge route contradict §4. A specific reference to the unequal edge costs and ignored labels suffices.",
        repair="Use a shortest-path solver for positive weighted edges, or state and enforce an equal-duration restriction.",
    ),
    Scenario(
        "shelf_oracle", "Picking a Shelf Layout Before a Delivery Arrives",
        before=(
            "A warehouse proposes a layout once a supplier confirms the order but before the crates are unpacked. Crate dimensions vary even within one order category. We ask how much forklift travel this decision could save.",
            "A catalogue contains five admissible layouts. We replayed 120 unseen orders. A layout's score is the travel required to place the crates actually received. Each order has one confirmed category and a separate list of the dimensions measured at unpacking.",
        ),
        clean="Before replay we froze a mapping from confirmed category to layout using only older orders. Each new order used its category's frozen entry. Both the original and proposed layout were then costed on the crates received.",
        defect="For each new order we costed all five layouts on its unpacked crates. We assigned that order the layout with the smallest realized travel. Both the original and the assigned layout were then costed on those same crates.",
        after=(
            "We interpret the measured savings as savings the picker will realize before the crates are unpacked. The installation can store all five arrangements but can only prepare one per order.",
        ),
        diagnosis="The proposed layout is chosen with realized per-order information unavailable at the prospective decision, making the comparison an oracle bound.",
        evidence="§3 picks the minimum measured travel on that order's unpacked crates whereas §1/§4 require action before dimensions are known. A capacity objection about storing five layouts is unrelated.",
        repair="Commit to a policy using confirmed order information, then test prospectively or on a held-out replay; alternatively report the result as a hindsight bound.",
    ),
    Scenario(
        "kiln_interval", "Checking Equivalent Surface Finish in Two Kilns",
        before=(
            "Changing kiln liners would reduce cleaning time, but the plant regards the liners as interchangeable only if the mean shift in roughness lies strictly between minus two and plus two units.",
            "The plant fixed the equivalence bounds in advance. Lots were randomized to liners. A paired analysis returned a mean difference of 0.4 with a 90 percent interval from minus 3.1 to plus 3.9. This is the interval specified for the two one-sided equivalence tests.",
            "As a descriptive supplement we tested an exactly zero difference. That two-sided test returned a p-value of 0.72. Costs and maintenance were recorded independently of finish.",
        ),
        clean="We could not demonstrate interchangeability on finish. The interval includes shifts outside both engineering bounds, so the zero-difference test by itself cannot settle the purchasing decision.",
        defect="We demonstrated interchangeability on finish. The zero-difference test gave no reason to reject a common mean, so the purchasing team can treat any change as within its engineering bounds.",
        after=(),
        diagnosis="Failure to reject exact equality does not establish the prespecified equivalence bounds; the interval crosses both.",
        evidence="§2 reports an interval from −3.1 to 3.9 against ±2 bounds. §4 uses the nonsignificant zero-difference test in §3 to assert equivalence.",
        repair="Apply the prespecified interval containment/two one-sided tests and report equivalence as unestablished; obtain more precision to support a positive equivalence claim.",
    ),
    Scenario(
        "bin_assignment", "Do Colored Bins Speed Up Returns?",
        before=(
            "A library wants the causal effect of color-coding return bins on the minutes needed to shelve books. Counter staff know the routes well, and time of day affects the number of carts waiting at the desk.",
            "Each of forty days contained one morning and one afternoon return wave. The same staff covered both waves, and books from each wave were timed until shelved. We averaged the colored-minus-plain difference over days.",
        ),
        clean="A coin tossed independently before each day's arrivals selected which wave used the colored bins. The other wave used plain bins. The timing clerk was not told the coin result.",
        defect="A standing rule assigned the colored bins to the morning wave and the plain bins to the afternoon wave every day. The timing clerk was not told the color assignment.",
        after=(
            "The colored bins had shorter shelving times. We attribute the observed difference to the color coding, allowing for the common staffing on each day.",
        ),
        diagnosis="Bin color is fully confounded with the morning/afternoon wave; matching within days does not identify the color effect.",
        evidence="§3 always pairs colored with morning, and §1 states that cart queues vary by time. §4's shared staff do not break this confounding.",
        repair="Randomize or counterbalance bin assignments across waves/days, or restrict the result to an association.",
    ),
    Scenario(
        "lace_ablation", "What Makes a Lace-Pattern Matcher Work?",
        before=(
            "A lace matcher has a new corner aligner. We isolate the effect of that component on retrieval rank. All catalogues and test queries were fixed before fitting.",
            "The full matcher aligns corners, averages three template views and uses a cosine-distance index. A reference matcher uses no aligner, one template view and the same index. Each ablation row is retrained from scratch.",
        ),
        clean="For the row labelled 'aligner removed' we bypassed the aligner in the full matcher but retained its three-view average and index. The row was fitted and tested on the original partition.",
        defect="For the row labelled 'aligner removed' we retrained the reference matcher on the full matcher's partition and inserted its score into the ablation grid. The row kept the reference matcher's original view count and index.",
        after=(
            "The full matcher beat the aligner-removed row. We interpret their rank gap as the gain contributed by the corner aligner.",
        ),
        diagnosis="The ablation changes template view count as well as alignment and cannot isolate the aligner's contribution.",
        evidence="§2 specifies one versus three views; §3 inserts the reference row without changing that count, while §4 attributes the difference to the aligner.",
        repair="Keep the three-view average and index fixed and vary only alignment, or run a two-factor ablation.",
    ),
    Scenario(
        "transit_weight", "Average Delay Experienced by a Tram Passenger",
        before=(
            "We estimate the average waiting delay of one passenger drawn uniformly from all trips made last month. There are two routes with very different ridership. A turnstile and a departure sensor jointly record every trip.",
            "The short route carried 900 trips at a mean delay of two minutes. The hill route carried 100 trips at a mean delay of twelve minutes. There are no missing sensor records or interchanges.",
        ),
        clean="We multiplied each route's mean by its measured passenger count, summed the products, and divided by the thousand trips.",
        defect="We added the short-route and hill-route means and divided by the number of routes, giving each timetable equal influence regardless of crowding.",
        after=(
            "The resulting estimate is what a randomly drawn passenger trip experienced on average in the recorded month. We make no claim about satisfaction or future timetable performance.",
        ),
        diagnosis="The reported unweighted route mean targets a uniformly sampled route rather than a uniformly sampled passenger trip.",
        evidence="§1's unit is a passenger trip, §2 gives unequal route counts, and §3 weights the two route means equally.",
        repair="Weight each route mean by its passenger count or average the trip-level delays directly.",
    ),
    Scenario(
        "clock_budget", "Comparing Two Tile Scanners at a Fixed Power Budget",
        before=(
            "A battery runs a tile scanner, including its lamp and processor. The workshop chooses a scanner under a fixed energy allocation per tray. A separate workstation does all subsequent visualizations.",
            "Both scanners handled the same trays. The reported quantity is joules per completed tray, averaged across trays. Runtime counters were reset between trays and all attempts completed.",
        ),
        clean="Each tray ran through the same instrumented battery outlet. The meter integrated power from the common outlet across both lamp and processor for either scanner.",
        defect="The incumbent's meter integrated the common battery outlet. The new scanner's telemetry integrated just its processor rail; its lamp runs from a separate rail on the same battery. The visualizations were excluded for both.",
        after=(
            "The lower energy reading for the new scanner establishes that it leaves more of the battery allocation available for subsequent trays.",
        ),
        diagnosis="Energy accounting differs between scanners because the new scanner excludes a battery-powered lamp.",
        evidence="§3 compares common-outlet energy for one scanner with processor-rail energy for the other despite §1's whole-battery budget. The shared omission of off-battery visualization is irrelevant.",
        repair="Measure both at the common battery boundary or add measured lamp energy to the new scanner's processor energy.",
    ),
    Scenario(
        "map_coverage", "Surveying Empty Bicycle Stands Across a City",
        before=(
            "A maintenance team wants the empty-stand fraction across every city stand at midday on the survey date. Some stands are behind access gates and never appear on the route used by the public counting van.",
            "The registry has 360 roadside stands and 40 behind gates. Security staff independently counted every gated stand at midday with the same empty/occupied rule. The van sampled roadside stands uniformly on that date.",
        ),
        clean="We used the van to estimate the roadside fraction and the security census to obtain the gated fraction. The citywide fraction averages those estimates weighted by their registered stand counts.",
        defect="We fit the van observations using the registered roadside and gated stand counts as calibration margins. Security counts were stored for a future survey and did not enter this estimate. The estimator only reweights stands observed by the van.",
        after=(
            "We present the estimate as the empty-stand fraction for all city stands on that date. Traffic on other dates and demand by hour are outside the scope.",
        ),
        diagnosis="Reweighting a roadside-only sample cannot identify the gated stratum, whose inclusion probability is zero.",
        evidence="§1 says the van never observes gated stands; §3 excludes the security counts and tries to use margins alone. The separate census in §2 is a possible remedy, not part of this estimate.",
        repair="Combine the gated census with the roadside estimate using registry weights, obtain gated observations, or restrict the estimand to roadside stands.",
    ),
    Scenario(
        "mosaic_sets", "Counting Distinct Colors in a Mosaic Catalogue",
        before=(
            "The catalogue stores one color set for each mosaic panel. We need the number of distinct colors represented anywhere in the catalogue to order one paint tester per color.",
        ),
        clean="Before counting, the registry assigns disjoint palettes to panels. Each color code belongs to exactly one panel in the catalogue; two codes of the same visible color are forbidden.",
        defect="Panels use one shared registry, so adjoining panels can list the same color code. Two codes of the same visible color are forbidden. The demonstration panels list {amber, taupe} and {taupe, blue}.",
        after=(
            "The counting routine sums the cardinalities of the individual panel color sets. It records neither the color names nor pairwise intersections once a panel count has been read.",
            "The routine returns the exact number of testers needed for every admissible catalogue. The purchaser does not buy an extra tester for repeat appearances of a color.",
        ),
        diagnosis="Adding set sizes overcounts colors appearing on more than one panel.",
        evidence="§3 sums panel cardinalities and §2's panels overlap on taupe, yielding four appearances for three distinct colors.",
        repair="Compute the size of the union using canonical color codes, apply inclusion-exclusion with sufficient information, or restrict the claim to disjoint palettes.",
    ),
    Scenario(
        "failed_folds", "Response Time of an Index Under Peak Ticket Load",
        before=(
            "A museum ticket desk submits one lookup per visitor. Under the service agreement a timeout incurs the fixed ten-second wait before a clerk intervenes. Our endpoint is the mean visitor wait, including timeouts.",
            "The old and new indexes processed identical replay requests with a ten-second timeout. The old one completed all lookups. The new one completed most requests in two seconds and timed out on one quarter. Desk intervention time is outside the endpoint.",
        ),
        clean="A per-request log is opened at arrival. The average for each index includes the ten-second entries written by the timeout handler, along with the completion entries.",
        defect="A timing log is written by each index's success callback. We averaged all entries in each such log. A separate timeout counter tracked requests ending before the callback and was not joined to the timing logs.",
        after=(
            "We use the two average times to compare the mean wait that visitors would experience under the service agreement.",
        ),
        diagnosis="The new index's reported mean excludes timed-out visitors, selectively omitting their fixed waits.",
        evidence="§3 averages success callbacks only, §2 reports timeouts on the new index, and §1 defines each timeout as a ten-second wait. Merely requesting a percentile does not diagnose this exclusion.",
        repair="Include ten seconds for each timed-out request and use all arrivals in the denominator.",
    ),
    Scenario(
        "spool_selection", "Choosing a Thread Guide for a Production Line",
        before=(
            "Nine thread guides have been built. After recommending one guide we want an unbiased estimate of its mean snag reduction on new spools. The comparison setting and roll speed were fixed in advance.",
            "The first collection contains thirty spools, with a paired trial of each guide and the old guide on each spool. A second collection of thirty independently drawn spools was then prepared using the same materials and speed.",
        ),
        clean="We chose the largest average reduction in the first collection. We reported that guide's average paired reduction measured on the untouched second collection. No choice was revised after this measurement.",
        defect="We chose the largest average reduction in the first collection. We reported that guide's average paired reduction from the same first collection. The second collection was reserved for measuring wear and did not contribute to the reported reduction.",
        after=(
            "The reported reduction is our estimate of how much the recommended guide will help on new spools. We do not claim a difference for every individual spool.",
        ),
        diagnosis="Reusing the maximum-performing selection sample to estimate the chosen guide's effect induces selection optimism.",
        evidence="§3 reports the winner's first-collection average and explicitly reserves the independent collection for another outcome.",
        repair="Evaluate the frozen selected guide on independent spools, or separate selection and evaluation using appropriate nested validation.",
    ),
    Scenario(
        "lamp_marginals", "Estimating the Benefit of a Backup Reading Lamp",
        before=(
            "For a double-lamp reading stand, loss of light means both lamps go dark during the same voltage dip. We estimate that outage probability to decide whether to add a separate battery.",
            "In our bench log, twelve percent of dips extinguished lamp A and twelve percent extinguished lamp B. Both lamps draw from the same feed during each dip. Simultaneous traces with timestamps and both status bits were retained.",
        ),
        clean="We estimated stand outage frequency by counting dip timestamps where both bits indicated darkness, then dividing by all logged dips.",
        defect="We estimated stand outage frequency by multiplying the two measured lamp failure fractions. We did not use the correspondence between the status bits at individual timestamps.",
        after=(
            "This estimate gives the frequency of total lighting loss on the logged dips. Neither lamp can compensate if the other has failed unless it is lit at the same time.",
        ),
        diagnosis="Multiplying marginal failure rates assumes independence even though failures are paired on a common feed; the joint outage is not identified by the two margins.",
        evidence="§2 records simultaneous bits on the same dips, but §3 discards their correspondence and multiplies the margins.",
        repair="Use the observed joint count of both-dark dips divided by total dips, or justify and validate the independence model.",
    ),
    Scenario(
        "paint_tails", "How Reliably Does a Sign-Painting Robot Meet Its Deadline?",
        before=(
            "A council requires a painted sign to dry in under forty minutes. It tracks misses per individual sign; a typical drying time helps with planning but cannot certify that every sign meets the deadline.",
            "The test cart carried one hundred signs of the same design. Each sign had its own timer. Ninety-nine signs dried in twenty minutes and the remaining sign took seventy. The study concerns this recorded batch only.",
            "After verifying timer calibration the analyst sorted the one hundred durations and reported their median. Handling and cleanup times were recorded separately.",
        ),
        clean="The batch's median drying time was below forty minutes. One sign missed the deadline, so the sign-level zero-miss requirement was not met on this batch.",
        defect="The batch's median drying time was below forty minutes. We therefore certify that every sign on this batch met the sign-level deadline.",
        after=(),
        diagnosis="A median below the threshold does not imply that all signs met it; the measured seventy-minute sign is a direct counterexample.",
        evidence="§4 certifies every sign from the median while §2 explicitly gives a seventy-minute sign against §1's forty-minute deadline.",
        repair="Evaluate the individual miss count or maximum against the threshold and limit the median claim to typical performance.",
    ),
    Scenario(
        "valve_multiplicity", "Finding a Valve Setting With Fewer Whistles",
        before=(
            "The pipe workshop may recommend any setting in a grid of twenty settings. Under a global null of equal whistle rates, it wants the chance of making even one erroneous improvement claim across the grid to be at most five percent.",
            "Predeclared tests compared each setting with its own separately randomized control batch. Each one-sided p-value was valid for its fixed setting. The smallest of the twenty p-values was 0.018.",
        ),
        clean="We judged the smallest p-value against 0.05 divided by twenty, as fixed in the family analysis plan. No setting crossed that threshold.",
        defect="We selected the setting with the smallest p-value, judged it against 0.05, and declared that setting an improvement. We used the same 0.05 threshold regardless of the grid size.",
        after=(
            "The workshop treats the resulting recommendation procedure as meeting its stated across-grid false-claim requirement.",
        ),
        diagnosis="Thresholding the selected minimum at an unadjusted per-setting level fails to control the stated familywise error.",
        evidence="§3 selects the minimum of the twenty valid individual tests from §2; §1 requires a bound on any false claim across the grid.",
        repair="Use a prespecified familywise procedure such as Holm or Bonferroni, or validate the frozen single selection on fresh batches.",
    ),
    Scenario(
        "press_censor", "Time Until a Stamp Pad Needs Re-Inking",
        before=(
            "A press operator wants the average pad lifetime truncated at a fourteen-day planning horizon, counting survival through day fourteen as fourteen days. New pads start together at the beginning of a shift.",
            "One fixed batch of sixty pads was inspected every day. All were observed until they first needed ink or reached day fourteen, whichever came first. Ten still had ink at day fourteen.",
        ),
        clean="For each pad we recorded the day it first needed ink, using day fourteen for each pad still serviceable then. We averaged the sixty recorded durations.",
        defect="For each pad that needed ink before the end we recorded its day of first re-inking. Pads still serviceable at day fourteen had a blank re-inking date. The average skipped the rows with blank dates.",
        after=(
            "The reported average estimates the fourteen-day truncated lifetime across this complete batch. We do not extrapolate beyond the observation horizon.",
        ),
        diagnosis="Dropping pads still functioning at day fourteen removes their known contribution to the restricted mean.",
        evidence="The ten surviving pads in §2 have blank dates and are excluded in §3 despite §1 defining their contribution as fourteen days.",
        repair="Assign each surviving pad fourteen days and average over all sixty pads, or estimate the restricted mean with an appropriate survival method.",
    ),
    Scenario(
        "ice_extrapolation", "Calibrating a Freezer Gauge Along a Delivery Route",
        before=(
            "The carrier needs a calibration guarantee throughout the range from minus twenty to zero degrees. The fitted gauge correction is a cubic polynomial constrained to zero at freezing. No monotonicity or curvature bound is imposed.",
            "All calibration and independent check samples used a bath between minus six and zero degrees. The cubic had small absolute error at the check temperatures. The manufacturer supplied no additional error specification.",
            "The analyst repeated the reference reading after each gauge reading, and both instruments equilibrated in the same bath. Estimated time constants were similar.",
        ),
        clean="The independent checks establish performance at the measured temperatures. The lower part of the carrier's range remains unvalidated until we obtain colder reference readings or a justified bound on extrapolation error.",
        defect="The independent checks establish calibration performance throughout the carrier's full range. Since the cubic is continuous, its small checked errors near zero extend to minus twenty degrees.",
        after=(),
        diagnosis="Continuity and warm-range checks do not bound the cubic's error in the unobserved colder range.",
        evidence="§2 checks only −6 to 0; §4 extends to −20 by continuity without the constraints disclaimed in §1.",
        repair="Validate colder reference points, establish justified extrapolation error bounds, or limit the performance claim to the supported temperatures.",
    ),
    Scenario(
        "mug_metric", "Estimating Whether Flagged Ceramic Mugs Are Cracked",
        before=(
            "A shop will manually unpack every mug whose tap sound triggers a flag. Its question is what fraction of the flagged mugs truly contains a crack, since this determines how many unpackings are productive.",
            "Inspectors unpacked every mug in a batch regardless of flag status. Of the twenty cracked mugs, fifteen were flagged. Of the eighty intact mugs, fifteen were flagged. Inspection is the reference standard.",
        ),
        clean="The analyst used all thirty flags as the denominator and counted the fifteen cracks among them. Each mug contributed once.",
        defect="The analyst used all twenty cracks as the denominator and counted the fifteen flags among them. Each mug contributed once.",
        after=(
            "The reported fraction is the proportion of productive unpackings the shop should expect when it follows the flag rule on this batch.",
        ),
        diagnosis="The fraction flagged among cracked mugs is sensitivity, not the fraction cracked among the flagged mugs.",
        evidence="§3 uses all twenty cracks as its denominator although §1 targets flagged mugs; §2 gives thirty flags including fifteen on intact mugs.",
        repair="Use true positives divided by all positive flags (15/30) for the specified decision.",
    ),
    Scenario(
        "shift_invariance", "A Translation-Insensitive Distance Between Dial Traces",
        before=(
            "Two dials may have different arbitrary zero points. We need a distance that stays unchanged if any constant is added to every reading of just one trace. Each trace has at least two real-valued samples.",
        ),
        clean="To transform a trace, subtract the mean of that very trace from each of its readings. Use this rule separately on both traces before comparison.",
        defect="To transform a trace, subtract the fixed reference-bank mean from each of its readings. Use this same bank value on both traces before comparison.",
        after=(
            "Two traces are compared sample by sample on an agreed time grid. We take the mean absolute difference of the transformed readings. Time alignment and rotation of the sample order are outside scope.",
            "We claim the resulting distance is insensitive to either dial's arbitrary zero point, including when only the first dial's entire trace is shifted.",
        ),
        diagnosis="Subtracting the same fixed reference leaves relative offsets intact, so a one-trace constant shift can change the distance.",
        evidence="§2/§3 use a common fixed mean and compare absolute differences. Equal traces initially have distance zero, and shifting only one yields a positive distance, contrary to §4.",
        repair="Center each trace by its own location statistic, minimize over relative offsets, or weaken the claim to common shifts.",
    ),
    Scenario(
        "panel_reference", "Testing a Reader of Raised Dots on Storage Panels",
        before=(
            "A workshop needs to know how often an optical reader correctly transcribes a worn panel's intended storage code. The original stamping ledger, the new optical reader and a senior engraver's tactile reading were collected for each tested panel.",
            "The reader produced a code without access to the ledger or engraver. Each ledger entry was recorded when the panel was stamped, independently of both readings. Some worn panels are hard to interpret by touch alone.",
        ),
        clean="For every panel the evaluator used the original stamping ledger as the reference code. The engraver and the reader were compared with it separately.",
        defect="The evaluator used the engraver's code on clear panels. For panels the engraver marked uncertain, the evaluator copied the new reader's own code into the reference column before computing new reader accuracy. Ledger entries were not used in this calculation.",
        after=(
            "We interpret the reported accuracy as agreement between the optical reader and independently established intended storage codes, including those difficult to read by touch.",
        ),
        diagnosis="Using the tested reader's own output as its reference for uncertain examples makes correctness circular on those panels.",
        evidence="§3 copies predictions into the reference for uncertain panels even though §2 describes an independent ledger. §4 claims independent intended codes for the difficult cases as well.",
        repair="Compare the reader against the independent original ledger for all panels, or obtain blinded external labels and report any unresolved cases separately.",
    ),
    Scenario(
        "sign_scale", "Ranking Portable Scales by Error Magnitude",
        before=(
            "A market pays for each gram by which a portable scale differs from a certified weight, regardless of the direction. The goal is to minimize this average absolute discrepancy per weighing.",
            "Each scale handled the same sealed weights twice. The new scale's signed errors alternated between plus four and minus four grams. The old scale read two grams high on every trial. Certified weights did not change.",
        ),
        clean="For each scale we took the absolute value of the error on each trial, then averaged these trial magnitudes. This reported the smaller discrepancy for the old scale.",
        defect="For each scale we averaged the signed errors first, then took the absolute value of the average. This reported the smaller discrepancy for the new scale.",
        after=(
            "We use the reported discrepancy as the amount on which to base average compensation per weighing. Batch-total bias is not used by the market.",
        ),
        diagnosis="Opposing errors cancel in the absolute mean signed error, which is not the mean absolute error required for per-weighing compensation.",
        evidence="§3 averages signs before taking magnitude; §2's alternating ±4 errors disappear under that order even though each costs four grams.",
        repair="Average absolute per-trial errors or compute the actual per-weighing compensation and reverse the ranking accordingly.",
    ),
]
