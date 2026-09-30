"""Per-field scorers for the triage manager.

Routing is not scored here — it is deterministic and unit-tested. These scorers ask
whether the manager did its own job: merged the findings into one honest sentence,
stayed inside the vocabulary the user speaks, and escalated only when escalating was
warranted.

`no_corrections` is again the rawest signal: it counts how often the branch guard
rail had to put a branch back, which is the failure that would otherwise reach a
manifest.
"""

import re

from src.domain.models import TriageBranch, TriageEntry
from tests.evals.scoring import Score
from tests.evals.triage.dataset import PackageCase

BRANCH_NAMES = tuple(branch.value for branch in TriageBranch)
ACTION_PHRASES = ("you should", "we recommend", "i recommend", "please upgrade")
VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")


def known_versions(case: PackageCase) -> set[str]:
    """Every version string the manager was actually shown for this package."""
    versions = {case.security.version, case.license.version, case.maintenance.version}
    if case.security.fixed_in:
        versions.add(case.security.fixed_in)
    for text in (case.security.evidence, case.license.conflicting_clause, case.maintenance.evidence):
        versions.update(VERSION.findall(text or ""))
    versions.update(VERSION.findall(case.license.license or ""))
    return {v for v in versions if v}


def score_branch_held(case: PackageCase, entry: TriageEntry) -> Score:
    """The branch the router chose is the branch the plan carries."""
    expected = (
        TriageBranch.HUMAN_REVIEW if case.expects_escalation else case.expected_branch
    )
    return Score("branch_held", entry.branch == expected, f"{entry.branch} != {expected}")


def score_no_corrections(case: PackageCase, entry: TriageEntry) -> Score:
    """How often the guard rail had to intervene — raw model quality before the rails."""
    return Score("no_corrections", not entry.corrections, "; ".join(entry.corrections))


def score_rationale_present(case: PackageCase, entry: TriageEntry) -> Score:
    return Score("rationale_present", bool(entry.rationale.strip()), "no rationale")


def score_rationale_is_brief(case: PackageCase, entry: TriageEntry) -> Score:
    """A plan is read at a glance; a paragraph per package is not a plan."""
    words = len(entry.rationale.split())
    return Score("rationale_is_brief", words <= 60, f"{words} words")


def score_mentions_the_findings(case: PackageCase, entry: TriageEntry) -> Score:
    """Each required group is one finding; the rationale must reach every one of them."""
    rationale = entry.rationale.lower()
    missed = [
        group for group in case.must_mention if not any(term.lower() in rationale for term in group)
    ]
    return Score(
        "mentions_the_findings",
        not missed,
        f"missed {[list(group) for group in missed]}" if missed else "",
    )


def score_avoids_the_forbidden(case: PackageCase, entry: TriageEntry) -> Score:
    rationale = entry.rationale.lower()
    used = [term for term in case.must_not_mention if term.lower() in rationale]
    return Score("avoids_the_forbidden", not used, f"said {used}" if used else "")


def score_speaks_findings_not_branches(case: PackageCase, entry: TriageEntry) -> Score:
    """"This is an UPGRADE_BREAKING" restates the schema; it tells the user nothing."""
    used = [name for name in BRANCH_NAMES if name.lower() in entry.rationale.lower()]
    return Score("speaks_findings_not_branches", not used, f"named the branch {used}" if used else "")


def score_no_action_language(case: PackageCase, entry: TriageEntry) -> Score:
    """The manager routes; the user decides. A plan entry is not an instruction."""
    used = [phrase for phrase in ACTION_PHRASES if phrase in entry.rationale.lower()]
    return Score("no_action_language", not used, f"instructed the user: {used}" if used else "")


def score_no_invented_version(case: PackageCase, entry: TriageEntry) -> Score:
    """A version number nobody reported is the cheapest hallucination to catch."""
    known = known_versions(case)
    invented = [v for v in VERSION.findall(entry.rationale) if v not in known]
    return Score("no_invented_version", not invented, f"cited {invented}" if invented else "")


SCORERS = (
    score_branch_held,
    score_no_corrections,
    score_rationale_present,
    score_rationale_is_brief,
    score_mentions_the_findings,
    score_avoids_the_forbidden,
    score_speaks_findings_not_branches,
    score_no_action_language,
    score_no_invented_version,
)


def score_case(case: PackageCase, entry: TriageEntry) -> list[Score]:
    return [scorer(case, entry) for scorer in SCORERS]
