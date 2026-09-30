"""Per-field scorers for the replacement search.

The scorers split into two groups, and the split is the point.

*Did it find the right thing?* — `finds_an_expected_candidate`, `avoids_the_traps`,
`top_candidate_is_maintained`. These score the answer.

*Did it earn the answer?* — `used_the_tools`, `no_unverified_candidates`,
`trace_written`, `evidence_present`. These score the process, and they are the ones
that catch a model that happens to be right. A recommendation is the output of this
server a user is most likely to act on without checking, so "right by luck" and
"right by verification" must not score the same.
"""

from src.domain.models import MaintenanceStatus, MigrationEffort, ReplacementSearch
from tests.evals.replacement.dataset import EvalCase
from tests.evals.scoring import Score


def names(search: ReplacementSearch) -> list[str]:
    return [candidate.name.lower() for candidate in search.candidates]


def score_no_error(case: EvalCase, search: ReplacementSearch) -> Score:
    return Score("no_error", search.error is None, search.error or "")


def score_finds_an_expected_candidate(case: EvalCase, search: ReplacementSearch) -> Score:
    if not case.expects_candidates:
        return Score("finds_an_expected_candidate", not search.candidates, str(names(search)))
    found = sorted(set(names(search)) & {n.lower() for n in case.expected_any_of})
    return Score(
        "finds_an_expected_candidate",
        bool(found),
        f"proposed {names(search)}, none of {sorted(case.expected_any_of)}",
    )


def score_top_candidate_accepted(case: EvalCase, search: ReplacementSearch) -> Score:
    """Ranking is the product here — the user reads the first line."""
    if not case.expects_candidates or not search.candidates:
        return Score("top_candidate_accepted", not case.expects_candidates, "no candidates")
    top = names(search)[0]
    return Score(
        "top_candidate_accepted",
        top in {n.lower() for n in case.expected_any_of},
        f"ranked {top} first",
    )


def score_avoids_the_traps(case: EvalCase, search: ReplacementSearch) -> Score:
    """A candidate the registry disqualifies must not survive the loop."""
    leaked = sorted(set(names(search)) & {n.lower() for n in case.must_not_recommend})
    return Score("avoids_the_traps", not leaked, f"recommended {leaked}" if leaked else "")


def score_top_candidate_is_maintained(case: EvalCase, search: ReplacementSearch) -> Score:
    if not search.candidates:
        return Score("top_candidate_is_maintained", not case.expects_candidates, "no candidates")
    status = search.candidates[0].maintenance_status
    return Score(
        "top_candidate_is_maintained", status != MaintenanceStatus.ABANDONED, f"top is {status}"
    )


def score_used_the_tools(case: EvalCase, search: ReplacementSearch) -> Score:
    """An answer produced without a single lookup is a recollection, not a search."""
    return Score("used_the_tools", search.iterations > 0, "answered without looking anything up")


def score_no_unverified_candidates(case: EvalCase, search: ReplacementSearch) -> Score:
    """Counts how often the guard rail had to drop a name — raw model quality."""
    return Score("no_unverified_candidates", not search.corrections, "; ".join(search.corrections))


def score_trace_written(case: EvalCase, search: ReplacementSearch) -> Score:
    return Score("trace_written", len(search.search_log) > 0, "no search log")


def score_evidence_present(case: EvalCase, search: ReplacementSearch) -> Score:
    bare = [c.name for c in search.candidates if not c.evidence.strip()]
    return Score("evidence_present", not bare, f"no evidence for {bare}" if bare else "")


def score_compatibility_stated(case: EvalCase, search: ReplacementSearch) -> Score:
    bare = [c.name for c in search.candidates if not c.compatibility.strip()]
    return Score("compatibility_stated", not bare, f"no compatibility for {bare}" if bare else "")


def score_effort_estimated(case: EvalCase, search: ReplacementSearch) -> Score:
    vague = [c.name for c in search.candidates if c.migration_effort == MigrationEffort.UNKNOWN]
    return Score("effort_estimated", not vague, f"no effort estimate for {vague}" if vague else "")


SCORERS = (
    score_no_error,
    score_finds_an_expected_candidate,
    score_top_candidate_accepted,
    score_avoids_the_traps,
    score_top_candidate_is_maintained,
    score_used_the_tools,
    score_no_unverified_candidates,
    score_trace_written,
    score_evidence_present,
    score_compatibility_stated,
    score_effort_estimated,
)


def score_case(case: EvalCase, search: ReplacementSearch) -> list[Score]:
    return [scorer(case, search) for scorer in SCORERS]
