"""The curated license-compatibility matrix — the CAG half of assess_license.

Roughly fifty hand-written rules over license pairs. It is small, static and
authoritative, so it is loaded wholesale into the agent's context on every call
rather than retrieved: retrieval would add latency and a failure mode (the right
row not being retrieved) for no benefit at this size.

The matrix is also enforced after the fact. A rule is a *floor*: the agent may be
stricter than the matrix says, never more permissive. That asymmetry is the whole
point — an agent that talks itself out of a copyleft obligation is the failure this
tool exists to prevent, while an agent that escalates a compatible package to human
review only costs someone a minute.
"""

from dataclasses import dataclass

from src.domain.models import Distribution, LicenseCompatibility
from src.resources.spdx import PROPRIETARY, PUBLIC_DOMAIN, parse_expression

ANY_LICENSE = "*"
ALL_DISTRIBUTIONS = (Distribution.BINARY, Distribution.SOURCE, Distribution.SAAS)
CONVEYED = (Distribution.BINARY, Distribution.SOURCE)

# How much a verdict restricts what the project may do. Used to take the strictest
# of two verdicts; never used to relax one.
STRICTNESS: dict[LicenseCompatibility, int] = {
    LicenseCompatibility.COMPATIBLE: 0,
    LicenseCompatibility.REQUIRES_HUMAN_REVIEW: 1,
    LicenseCompatibility.INCOMPATIBLE: 2,
}


@dataclass(frozen=True)
class LicenseRule:
    """One row: a dependency license, under a project license, under a distribution model."""

    dependency_license: str
    verdict: LicenseCompatibility
    rationale: str
    project_license: str = ANY_LICENSE
    distributions: tuple[Distribution, ...] = ALL_DISTRIBUTIONS

    @property
    def is_wildcard(self) -> bool:
        return self.project_license == ANY_LICENSE

    def covers(self, project_license: str, distribution: Distribution) -> bool:
        return distribution in self.distributions and (
            self.is_wildcard or self.project_license.lower() == project_license.lower()
        )


def permissive(identifier: str, rationale: str) -> LicenseRule:
    return LicenseRule(identifier, LicenseCompatibility.COMPATIBLE, rationale)


MATRIX: tuple[LicenseRule, ...] = (
    # --- permissive: notice-only obligations, no reciprocal licensing ---
    permissive("MIT", "Notice-and-attribution only; imposes no condition on the combined work."),
    permissive("MIT-0", "Notice-and-attribution only, and even the notice is waived."),
    permissive("BSD-2-Clause", "Notice-and-attribution only; no reciprocal obligation."),
    permissive("BSD-3-Clause", "Notice plus a no-endorsement clause; no reciprocal obligation."),
    permissive("ISC", "Functionally equivalent to MIT; notice only."),
    permissive("Zlib", "Notice and a no-misrepresentation clause; no reciprocal obligation."),
    permissive("Python-2.0", "PSF license; notice and a change summary, no reciprocal obligation."),
    permissive("PostgreSQL", "Notice only; equivalent in effect to MIT."),
    permissive("HPND", "Historical permission notice; notice only."),
    permissive("Unlicense", "Public-domain dedication; no obligations survive."),
    permissive("CC0-1.0", "Public-domain dedication with a fallback permissive grant."),
    permissive("Artistic-2.0", "Permits redistribution in a larger work under other terms."),
    permissive("CC-BY-4.0", "Attribution only; no share-alike obligation on the combined work."),
    LicenseRule(
        PUBLIC_DOMAIN,
        LicenseCompatibility.COMPATIBLE,
        "An informal dedication carrying no formal grant, but imposing no obligations "
        "either; treat the absence of a grant as a documentation gap, not a conflict.",
    ),
    permissive("Apache-2.0", "Notice, change notes and an express patent grant; no reciprocal obligation."),
    LicenseRule(
        "Apache-2.0",
        LicenseCompatibility.INCOMPATIBLE,
        "The Apache-2.0 patent-termination and indemnity clauses are further restrictions "
        "that GPL-2.0-only forbids adding to a covered work; the FSF holds the two "
        "one-way incompatible.",
        project_license="GPL-2.0-only",
    ),
    LicenseRule(
        "Apache-2.0",
        LicenseCompatibility.COMPATIBLE,
        "A GPL-2.0-or-later project may relicense the combination under GPL-3.0, which "
        "is compatible with Apache-2.0.",
        project_license="GPL-2.0-or-later",
    ),
    # --- weak / file-level copyleft ---
    permissive("MPL-2.0", "File-level copyleft: modified MPL files stay MPL, the larger work may not."),
    LicenseRule(
        "EPL-2.0",
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        "EPL-2.0 requires source availability for the covered modules and carries patent "
        "and indemnity terms whose interaction with the project's own terms is a legal call.",
        distributions=CONVEYED,
    ),
    LicenseRule(
        "EPL-2.0",
        LicenseCompatibility.COMPATIBLE,
        "EPL-2.0 obligations attach to distribution; a hosted service distributes nothing.",
        distributions=(Distribution.SAAS,),
    ),
    *(
        LicenseRule(
            identifier,
            LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
            "The LGPL requires that the user be able to relink the application against a "
            "modified version of the library; shipping a binary complicates that obligation "
            "and whether it is discharged depends on how the binary is built.",
            distributions=(Distribution.BINARY,),
        )
        for identifier in ("LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later")
    ),
    *(
        LicenseRule(
            identifier,
            LicenseCompatibility.COMPATIBLE,
            "Shipping source, or not shipping at all, satisfies the LGPL relinking "
            "obligation without constraining the project's own license.",
            distributions=(Distribution.SOURCE, Distribution.SAAS),
        )
        for identifier in ("LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later")
    ),
    # --- strong copyleft ---
    *(
        LicenseRule(
            identifier,
            LicenseCompatibility.INCOMPATIBLE,
            "Conveying a work that links GPL code obliges the whole combined work to be "
            "offered under the GPL, which a project under different terms cannot do.",
            distributions=CONVEYED,
        )
        for identifier in ("GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later")
    ),
    *(
        LicenseRule(
            identifier,
            LicenseCompatibility.COMPATIBLE,
            "The GPL's reciprocal obligation is triggered by conveying a copy. Running the "
            "software on a server for users to interact with over a network is not conveying.",
            distributions=(Distribution.SAAS,),
        )
        for identifier in ("GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later")
    ),
    *(
        LicenseRule(
            dependency,
            LicenseCompatibility.COMPATIBLE,
            "The project is already under the same copyleft terms, so the reciprocal "
            "obligation is satisfied by construction.",
            project_license=project,
        )
        for dependency, project in (
            ("GPL-2.0-only", "GPL-2.0-only"),
            ("GPL-2.0-or-later", "GPL-2.0-only"),
            ("GPL-2.0-or-later", "GPL-3.0-only"),
            ("GPL-2.0-or-later", "GPL-3.0-or-later"),
            ("GPL-3.0-only", "GPL-3.0-only"),
            ("GPL-3.0-only", "GPL-3.0-or-later"),
            ("GPL-3.0-or-later", "GPL-3.0-or-later"),
            ("AGPL-3.0-only", "AGPL-3.0-only"),
            ("AGPL-3.0-or-later", "AGPL-3.0-only"),
            ("AGPL-3.0-or-later", "AGPL-3.0-or-later"),
        )
    ),
    *(
        LicenseRule(
            "GPL-2.0-only",
            LicenseCompatibility.INCOMPATIBLE,
            "GPL-2.0-only carries no 'or any later version' clause, so its code cannot be "
            "combined with a GPL-3.0 work; the two versions are mutually incompatible.",
            project_license=project,
        )
        for project in ("GPL-3.0-only", "GPL-3.0-or-later")
    ),
    *(
        LicenseRule(
            identifier,
            LicenseCompatibility.INCOMPATIBLE,
            "AGPL section 13 extends the reciprocal obligation to users interacting with the "
            "program over a network, so a hosted service conveys in the AGPL's sense and the "
            "whole work must be offered under the AGPL.",
        )
        for identifier in ("AGPL-3.0-only", "AGPL-3.0-or-later")
    ),
    # --- source-available and proprietary: not open source, whatever the repo looks like ---
    LicenseRule(
        "BUSL-1.1",
        LicenseCompatibility.INCOMPATIBLE,
        "The Business Source License forbids production use outside the vendor's stated "
        "additional-use grant until the change date; it is source-available, not open source.",
    ),
    LicenseRule(
        "SSPL-1.0",
        LicenseCompatibility.INCOMPATIBLE,
        "SSPL section 13 requires publishing the entire source of the service used to offer "
        "the program, including the surrounding infrastructure.",
        distributions=(Distribution.SAAS,),
    ),
    LicenseRule(
        "SSPL-1.0",
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        "SSPL's service-source obligation is aimed at offering the program as a service; "
        "whether a conveyed product triggers it is a legal call.",
        distributions=CONVEYED,
    ),
    LicenseRule(
        "Elastic-2.0",
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        "The Elastic License forbids providing the product as a hosted service and forbids "
        "circumventing license-key functionality; whether the project does either is a "
        "question about the product, not the license.",
    ),
    LicenseRule(
        PROPRIETARY,
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        "A proprietary grant is whatever its contract says; it cannot be assessed from an "
        "identifier and must be read.",
    ),
    LicenseRule(
        "CC-BY-SA-4.0",
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        "The share-alike condition propagates to adaptations, and whether software linking "
        "the work creates an adaptation is unsettled for a license written for media.",
    ),
    LicenseRule(
        "OSL-3.0",
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        "OSL-3.0 treats external deployment as distribution and is widely held incompatible "
        "with the GPL; its reach over a linked work is a legal call.",
    ),
    LicenseRule(
        "EUPL-1.2",
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        "The EUPL is reciprocal, covers communication to the public as distribution, and "
        "carries a compatibility annex that has to be read against the project's license.",
    ),
)


def rules_for_license(dependency_license: str) -> tuple[LicenseRule, ...]:
    """Every row mentioning this dependency license, whatever the project or distribution."""
    return tuple(
        rule
        for rule in MATRIX
        if rule.dependency_license.lower() == dependency_license.lower()
    )


def rule_for(
    dependency_license: str, project_license: str, distribution: Distribution
) -> LicenseRule | None:
    """The single row that governs this pair, with an explicit project license winning
    over a wildcard so that row order in the table above never changes an outcome."""
    candidates = [
        rule
        for rule in rules_for_license(dependency_license)
        if rule.covers(project_license, distribution)
    ]
    specific = [rule for rule in candidates if not rule.is_wildcard]
    return next(iter(specific or candidates), None)


def floor_verdict(
    normalized_license: str, project_license: str, distribution: Distribution
) -> LicenseCompatibility | None:
    """The most permissive verdict the matrix authorizes for this license expression.

    A single identifier resolves to its row. A disjunction ("MIT OR GPL-3.0") lets the
    project pick its branch, so the floor is the most permissive branch — but only when
    every branch is known, since an unknown branch might be more permissive still. A
    conjunction ("MIT AND GPL-3.0") must satisfy every part, so the floor is the
    strictest known part; unknown parts can only add obligations, never remove one.

    None means the matrix does not speak to this pair, and the agent's own judgment
    stands unmodified.
    """
    return _floor(parse_expression(normalized_license), project_license, distribution)


def _floor(node, project_license: str, distribution: Distribution) -> LicenseCompatibility | None:
    if (operator := type(node).__name__) in ("AND", "OR"):
        parts = [_floor(arg, project_license, distribution) for arg in node.args]
        if operator == "OR":
            return min(parts, key=_rank) if all(part is not None for part in parts) else None
        known = [part for part in parts if part is not None]
        return max(known, key=_rank) if known else None
    rule = rule_for(str(node), project_license, distribution)
    return rule.verdict if rule else None


def _rank(verdict: LicenseCompatibility | None) -> int:
    return STRICTNESS[verdict] if verdict is not None else -1


def render_rules(rules: tuple[LicenseRule, ...]) -> str:
    """The matrix as the agent reads it: one line per row, no schema to decode."""
    return "\n".join(
        f"- {rule.dependency_license} in {_project(rule)}, "
        f"{_distributions(rule)} -> {rule.verdict.value}: {rule.rationale}"
        for rule in rules
    )


def render_matrix() -> str:
    return render_rules(MATRIX)


def _project(rule: LicenseRule) -> str:
    return "any project" if rule.is_wildcard else f"a {rule.project_license} project"


def _distributions(rule: LicenseRule) -> str:
    if set(rule.distributions) == set(ALL_DISTRIBUTIONS):
        return "any distribution model"
    return " or ".join(distribution.value for distribution in rule.distributions) + " distribution"
