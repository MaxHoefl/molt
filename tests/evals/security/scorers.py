"""Per-field scorers.

Each scorer answers one narrow question about one verdict, so a regression points
at a behaviour rather than at "the eval went down". Scorers are deterministic —
no LLM judge — which keeps a run cheap enough to execute on every model swap.
"""

import re

from src.domain.models import SecurityVerdict, Severity
from tests.evals.scoring import Score
from tests.evals.security.dataset import EvalCase

IDENTIFIER_PATTERN = re.compile(r"\b(?:CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4,}(?:-[a-z0-9]{4,})*|PYSEC-\d{4}-\d+)\b", re.IGNORECASE)
ACTION_PHRASES = ("you should", "we recommend", "i recommend", "upgrade to", "please upgrade", "must upgrade")
SEVERITY_RANK = {
    Severity.NONE: 0,
    Severity.UNKNOWN: 0,
    Severity.LOW: 1,
    Severity.MODERATE: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def advisory_text(case: EvalCase) -> str:
    parts = []
    for vulnerability in case.package.vulnerabilities:
        parts += [vulnerability.id, *vulnerability.aliases]
        parts += [vulnerability.summary or "", vulnerability.details or ""]
    return " ".join(parts)


def known_identifiers(case: EvalCase) -> set[str]:
    identifiers = set()
    for vulnerability in case.package.vulnerabilities:
        identifiers.add(vulnerability.id.upper())
        identifiers.update(alias.upper() for alias in vulnerability.aliases)
    return identifiers


def score_cve_recall(case: EvalCase, verdict: SecurityVerdict) -> Score:
    """Did it find the advisories that apply? A miss here is a missed vulnerability."""
    if not case.expected_cves:
        return Score("cve_recall", not verdict.applicable_cves, str(verdict.applicable_cves))
    found = {cve.upper() for cve in verdict.applicable_cves}
    # An advisory may legitimately be named by its id or by any of its aliases.
    missed = [
        vulnerability.id
        for vulnerability in case.package.vulnerabilities
        if not ({vulnerability.id.upper()} | {a.upper() for a in vulnerability.aliases}) & found
    ]
    return Score("cve_recall", not missed, f"missed {missed}" if missed else "")


def score_cve_precision(case: EvalCase, verdict: SecurityVerdict) -> Score:
    """Did it report anything that isn't in the input? This is the hallucination metric."""
    reported = {cve.upper() for cve in verdict.applicable_cves}
    invented = reported - known_identifiers(case)
    return Score("cve_precision", not invented, f"invented {sorted(invented)}" if invented else "")


def score_severity_exact(case: EvalCase, verdict: SecurityVerdict) -> Score:
    return Score(
        "severity_exact",
        verdict.max_severity == case.expected_severity,
        f"{verdict.max_severity} != {case.expected_severity}",
    )


def score_severity_within_one_band(case: EvalCase, verdict: SecurityVerdict) -> Score:
    """Severity is a judgment call; being one band off is a different failure than being wrong."""
    distance = abs(SEVERITY_RANK[verdict.max_severity] - SEVERITY_RANK[case.expected_severity])
    return Score("severity_within_one_band", distance <= 1, f"off by {distance} bands")


def score_fixed_in(case: EvalCase, verdict: SecurityVerdict) -> Score:
    return Score(
        "fixed_in_exact",
        verdict.fixed_in == case.expected_fixed_in,
        f"{verdict.fixed_in} != {case.expected_fixed_in}",
    )


def score_upgrade_distance(case: EvalCase, verdict: SecurityVerdict) -> Score:
    return Score(
        "upgrade_distance",
        verdict.upgrade_distance == case.expected_distance,
        f"{verdict.upgrade_distance} != {case.expected_distance}",
    )


def score_evidence_grounded(case: EvalCase, verdict: SecurityVerdict) -> Score:
    """Every identifier the evidence names must exist in the advisories it was given."""
    cited = {match.upper() for match in IDENTIFIER_PATTERN.findall(verdict.evidence)}
    ungrounded = cited - known_identifiers(case)
    return Score("evidence_grounded", not ungrounded, f"cited {sorted(ungrounded)}" if ungrounded else "")


def score_evidence_present(case: EvalCase, verdict: SecurityVerdict) -> Score:
    return Score("evidence_present", bool(verdict.evidence.strip()), "empty evidence")


def score_no_lure(case: EvalCase, verdict: SecurityVerdict) -> Score:
    """Identifiers the case plants as distractors must not surface anywhere in the verdict."""
    surface = " ".join(verdict.applicable_cves + [verdict.evidence]).upper()
    leaked = [lure for lure in case.must_not_appear if lure.upper() in surface]
    return Score("no_lure", not leaked, f"repeated distractor {leaked}" if leaked else "")


def score_no_action_language(case: EvalCase, verdict: SecurityVerdict) -> Score:
    """The specialist characterizes risk; recommending an action is triage's job."""
    evidence = verdict.evidence.lower()
    used = [phrase for phrase in ACTION_PHRASES if phrase in evidence]
    return Score("no_action_language", not used, f"recommended action: {used}" if used else "")


def score_no_corrections(case: EvalCase, verdict: SecurityVerdict) -> Score:
    """How often the guard rails had to intervene — a direct measure of raw model quality."""
    return Score("no_corrections", not verdict.corrections, "; ".join(verdict.corrections))


def score_no_error(case: EvalCase, verdict: SecurityVerdict) -> Score:
    return Score("no_error", verdict.error is None, verdict.error or "")


SCORERS = (
    score_no_error,
    score_cve_recall,
    score_cve_precision,
    score_severity_exact,
    score_severity_within_one_band,
    score_fixed_in,
    score_upgrade_distance,
    score_evidence_present,
    score_evidence_grounded,
    score_no_lure,
    score_no_action_language,
    score_no_corrections,
)


def score_case(case: EvalCase, verdict: SecurityVerdict) -> list[Score]:
    return [scorer(case, verdict) for scorer in SCORERS]
