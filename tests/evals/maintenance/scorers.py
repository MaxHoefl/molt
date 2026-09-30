"""Per-field scorers for the maintenance assessor.

Each scorer answers one narrow question about one verdict, so a regression points
at a behaviour rather than at "the eval went down". Scorers are deterministic — no
LLM judge — which keeps a run cheap enough to execute on every model swap.

The pair that matters most is `status_accepted` and `never_downplays`, and they
fail in opposite directions. Calling a healthy package declining costs a reviewer
a minute. Calling an abandoned one healthy is how a project ends up depending on
something nobody will patch when the CVE lands — so the direction of the error
gets its own scorer, exactly as `never_relaxes` does for licenses.
"""

import re

from src.domain.models import MaintenanceStatus, MaintenanceVerdict
from src.tools.assess_maintenance import CONCERN
from tests.evals.maintenance.dataset import EvalCase
from tests.evals.scoring import Score

ACTION_PHRASES = (
    "you should",
    "we recommend",
    "i recommend",
    "should be replaced",
    "must be replaced",
    "remove this dependency",
    "switch to",
    "migrate to",
    "upgrade to",
    "consider replacing",
)
NUMBER = re.compile(r"\d")


def score_no_error(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    return Score("no_error", verdict.error is None, verdict.error or "")


def score_status_accepted(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    return Score(
        "status_accepted",
        verdict.status in case.accepted_statuses,
        f"{verdict.status} not in {sorted(s.value for s in case.accepted_statuses)}",
    )


def score_never_downplays(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    """The metric that matters: being more worried than the label is survivable.

    Measured against the *least* concerning answer the case accepts, so a case that
    genuinely reads two ways is not punished for picking either — only for landing
    below both.
    """
    floor = min(CONCERN[status] for status in case.accepted_statuses)
    downplayed = CONCERN[verdict.status] < floor
    return Score(
        "never_downplays",
        not downplayed,
        f"called it {verdict.status}, label is {case.expected_status}" if downplayed else "",
    )


def score_confidence_calibrated(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    """Confidence tracks how much evidence there was, not how bad the news is."""
    within = case.min_confidence <= verdict.confidence <= case.max_confidence
    return Score(
        "confidence_calibrated",
        within,
        f"{verdict.confidence} outside [{case.min_confidence}, {case.max_confidence}]",
    )


def score_evidence_present(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    return Score("evidence_present", bool(verdict.evidence.strip()), "no evidence cited")


def score_evidence_cites_a_measurement(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    """Evidence that names no number is prose, not grounding.

    Every signal the agent is given is a number or a yes/no; a justification that
    contains neither is a summary of the model's prior, which is the failure this
    tool's whole input design exists to prevent.
    """
    grounded = bool(NUMBER.search(verdict.evidence)) or "archiv" in verdict.evidence.lower()
    return Score("evidence_cites_a_measurement", grounded, verdict.evidence)


def score_no_invented_signal(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    """A signal that was never measured must not appear as if it had been."""
    evidence = verdict.evidence.lower()
    leaked = [term for term in case.must_not_appear if term.lower() in evidence]
    return Score("no_invented_signal", not leaked, f"cited unmeasured {leaked}" if leaked else "")


def score_no_action_language(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    """The specialist characterizes maintenance; recommending an action is triage's job."""
    evidence = verdict.evidence.lower()
    used = [phrase for phrase in ACTION_PHRASES if phrase in evidence]
    return Score("no_action_language", not used, f"recommended action: {used}" if used else "")


def score_unknown_is_last_resort(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    """'unknown' is for absent signals, not for hard calls."""
    unjustified = verdict.status == MaintenanceStatus.UNKNOWN and not verdict.signals.is_empty
    return Score(
        "unknown_is_last_resort",
        not unjustified or MaintenanceStatus.UNKNOWN in case.accepted_statuses,
        "answered unknown despite having signals" if unjustified else "",
    )


def score_no_corrections(case: EvalCase, verdict: MaintenanceVerdict) -> Score:
    """How often the guard rails had to intervene — a direct measure of raw model quality."""
    return Score("no_corrections", not verdict.corrections, "; ".join(verdict.corrections))


SCORERS = (
    score_no_error,
    score_status_accepted,
    score_never_downplays,
    score_confidence_calibrated,
    score_evidence_present,
    score_evidence_cites_a_measurement,
    score_no_invented_signal,
    score_no_action_language,
    score_unknown_is_last_resort,
    score_no_corrections,
)


def score_case(case: EvalCase, verdict: MaintenanceVerdict) -> list[Score]:
    return [scorer(case, verdict) for scorer in SCORERS]
