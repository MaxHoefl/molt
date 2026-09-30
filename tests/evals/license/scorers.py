"""Per-field scorers for the license assessor.

Each scorer answers one narrow question about one verdict, so a regression points at
a behaviour rather than at "the eval went down". Scorers are deterministic — no LLM
judge — which keeps a run cheap enough to execute on every model swap.

The pair that matters most is `verdict_accepted` and `never_relaxes`, and they fail in
opposite directions. A verdict that is too strict wastes a reviewer's minute; a verdict
that is too permissive ships someone else's copyleft inside a product that cannot
honour it. Only the second one is a compliance incident, so it gets its own scorer.
"""

import re

from src.domain.models import LicenseCompatibility, LicenseVerdict
from src.resources.license_matrix import STRICTNESS
from tests.evals.license.dataset import EvalCase
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
)
FAMILY_PATTERN = re.compile(r"[A-Za-z]+")
LICENSE_REF = "LicenseRef-"


def license_families(license: str | None) -> set[str]:
    """The license names a clause about this dependency would have to mention.

    "LGPL-2.1-only" is cited as "the LGPL", "BUSL-1.1" as "the Business Source
    License" or "BUSL"; the version suffix is not what makes a citation about the
    right license, the family is.
    """
    families: set[str] = set()
    for token in (license or "").replace("(", " ").replace(")", " ").split():
        if token.upper() in ("AND", "OR", "WITH"):
            continue
        if token.startswith(LICENSE_REF):
            families.add(token.removeprefix(LICENSE_REF).replace("-", " "))
        elif match := FAMILY_PATTERN.match(token):
            families.add(match.group())
    return families


def score_no_error(case: EvalCase, verdict: LicenseVerdict) -> Score:
    return Score("no_error", verdict.error is None, verdict.error or "")


def score_license_resolved(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """The declared string resolved to the right identifier — or to none, deliberately."""
    return Score(
        "license_resolved",
        verdict.license == case.expected_license,
        f"{verdict.license} != {case.expected_license}",
    )


def score_verdict_accepted(case: EvalCase, verdict: LicenseVerdict) -> Score:
    return Score(
        "verdict_accepted",
        verdict.verdict in case.accepted_verdicts,
        f"{verdict.verdict} not in {sorted(v.value for v in case.accepted_verdicts)}",
    )


def score_never_relaxes(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """The compliance metric: being stricter than the label is survivable, laxer is not."""
    relaxed = STRICTNESS[verdict.verdict] < STRICTNESS[case.expected_verdict]
    return Score(
        "never_relaxes",
        not relaxed,
        f"cleared to {verdict.verdict}, label is {case.expected_verdict}" if relaxed else "",
    )


def score_clause_present(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """A verdict that blocks or escalates a package has to say which obligation did it."""
    if verdict.verdict == LicenseCompatibility.COMPATIBLE:
        return Score("clause_present", True)
    return Score("clause_present", bool(verdict.conflicting_clause.strip()), "no clause cited")


def score_clause_absent_when_compatible(case: EvalCase, verdict: LicenseVerdict) -> Score:
    if verdict.verdict != LicenseCompatibility.COMPATIBLE:
        return Score("clause_absent_when_compatible", True)
    return Score(
        "clause_absent_when_compatible",
        not verdict.conflicting_clause.strip(),
        verdict.conflicting_clause,
    )


def score_clause_names_the_license(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """The cited obligation must belong to a license the dependency actually carries."""
    families = license_families(verdict.license)
    if verdict.verdict == LicenseCompatibility.COMPATIBLE or not families:
        return Score("clause_names_the_license", True)
    clause = verdict.conflicting_clause.lower()
    named = [family for family in families if family.lower() in clause]
    return Score("clause_names_the_license", bool(named), f"cites none of {sorted(families)}")


def score_no_lure(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """A license the dependency does not carry must not surface anywhere in the verdict."""
    surface = " ".join([verdict.conflicting_clause, *verdict.suggested_alternatives]).upper()
    leaked = [lure for lure in case.must_not_appear if lure.upper() in surface]
    return Score("no_lure", not leaked, f"named {leaked}, which is not this license" if leaked else "")


def score_alternatives_when_expected(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """Where a well-known permissive replacement exists, a blocked package should name it."""
    if not case.expects_alternatives:
        return Score("alternatives_when_expected", True)
    return Score(
        "alternatives_when_expected",
        bool(verdict.suggested_alternatives),
        "suggested nothing",
    )


def score_alternatives_carry_a_license(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """An alternative without its license just moves the same question one package along."""
    unlabelled = [
        alternative
        for alternative in verdict.suggested_alternatives
        if "(" not in alternative or ")" not in alternative
    ]
    return Score("alternatives_carry_a_license", not unlabelled, f"unlabelled {unlabelled}")


def score_alternatives_absent_when_compatible(case: EvalCase, verdict: LicenseVerdict) -> Score:
    if verdict.verdict != LicenseCompatibility.COMPATIBLE:
        return Score("alternatives_absent_when_compatible", True)
    return Score(
        "alternatives_absent_when_compatible",
        not verdict.suggested_alternatives,
        str(verdict.suggested_alternatives),
    )


def score_no_action_language(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """The specialist characterizes compatibility; recommending an action is triage's job."""
    clause = verdict.conflicting_clause.lower()
    used = [phrase for phrase in ACTION_PHRASES if phrase in clause]
    return Score("no_action_language", not used, f"recommended action: {used}" if used else "")


def score_no_corrections(case: EvalCase, verdict: LicenseVerdict) -> Score:
    """How often the guard rails had to intervene — a direct measure of raw model quality."""
    return Score("no_corrections", not verdict.corrections, "; ".join(verdict.corrections))


SCORERS = (
    score_no_error,
    score_license_resolved,
    score_verdict_accepted,
    score_never_relaxes,
    score_clause_present,
    score_clause_absent_when_compatible,
    score_clause_names_the_license,
    score_no_lure,
    score_alternatives_when_expected,
    score_alternatives_carry_a_license,
    score_alternatives_absent_when_compatible,
    score_no_action_language,
    score_no_corrections,
)


def score_case(case: EvalCase, verdict: LicenseVerdict) -> list[Score]:
    return [scorer(case, verdict) for scorer in SCORERS]
