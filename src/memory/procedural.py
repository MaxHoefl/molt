"""Procedural memory: preference rules *derived* from the episodic log.

Nothing here is written by hand. Every rule is recomputed from the decisions the
user actually made, which gives three properties that matter more than the rules
themselves:

* **Traceable.** `derived_from` names the episodes that produced the rule, so
  "why is Molt down-ranking this?" always has an answer, and the
  `molt://policy/{project}` resource can show it.
* **Self-correcting.** A contradicting decision does not need special handling —
  it changes the counts, so the confidence falls out of the same arithmetic.
* **Finite.** A rule that drops below `RETIREMENT_THRESHOLD` is not stored, so
  the policy set cannot silently accumulate stale opinions.

Confidence is a shrunk ratio: `support / total * (1 - 1 / (2 * total))`. The
first factor is how consistent the user has been, the second is how much
evidence there is — two rejections out of two is 0.75, six out of six is 0.92,
and no amount of agreement ever reaches certainty.
"""

from collections import defaultdict
from dataclasses import dataclass

from src.domain.models import DecisionAction, EpisodicEntry, ProceduralRule, TriageBranch

MIN_OBSERVATIONS = 2
RETIREMENT_THRESHOLD = 0.5

AVOID_MAJOR_BUMPS = "avoid_major_bumps"
AVOID_REPLACEMENTS = "avoid_replacements"
TRUSTS_AUTO_UPGRADES = "trusts_auto_upgrades"
LICENSE_CLEARED_PREFIX = "license_cleared:"

EFFECTS = {
    AVOID_MAJOR_BUMPS: "UPGRADE_BREAKING entries are annotated and sorted last in proposals",
    AVOID_REPLACEMENTS: "REPLACE entries are annotated and sorted last in proposals",
    TRUSTS_AUTO_UPGRADES: "AUTO_UPGRADE entries are annotated as likely to be approved",
}
LICENSE_CLEARED_EFFECT = "suppresses the license-driven HUMAN_REVIEW flag for {package}"


@dataclass
class Tally:
    """How often the user acted the way a rule predicts, and how often they did not."""

    support: list[str]
    against: list[str]

    @property
    def total(self) -> int:
        return len(self.support) + len(self.against)

    @property
    def confidence(self) -> float:
        if self.total < MIN_OBSERVATIONS or not self.support:
            return 0.0
        ratio = len(self.support) / self.total
        return round(ratio * (1 - 1 / (2 * self.total)), 2)


def cite(entry: EpisodicEntry) -> str:
    return f"{entry.package} {entry.action.value} {entry.recorded_at[:10]}"


def branch_rule(
    episodes: list[EpisodicEntry], branch: TriageBranch, supporting: DecisionAction
) -> Tally:
    """One tally over the decisions taken on a single triage branch."""
    tally = Tally(support=[], against=[])
    for entry in episodes:
        if entry.branch != branch or entry.action == DecisionAction.DEFERRED:
            continue
        side = tally.support if entry.action == supporting else tally.against
        side.append(cite(entry))
    return tally


def license_clearances(episodes: list[EpisodicEntry]) -> dict[str, Tally]:
    """Per-package tallies for licenses a human explicitly cleared.

    A later rejection or deferral of the same package contradicts the clearance,
    which is what lets a person take a clearance back by recording a new decision
    rather than by editing memory.
    """
    tallies: dict[str, Tally] = defaultdict(lambda: Tally(support=[], against=[]))
    for entry in episodes:
        if entry.action == DecisionAction.RESOLVED_HUMAN_REVIEW:
            tallies[entry.package].support.append(cite(entry))
        elif entry.branch == TriageBranch.HUMAN_REVIEW and entry.action in (
            DecisionAction.REJECTED,
            DecisionAction.DEFERRED,
        ):
            tallies[entry.package].against.append(cite(entry))
    return dict(tallies)


def derive_rules(episodes: list[EpisodicEntry]) -> list[ProceduralRule]:
    """The full rule set for a project, recomputed from its whole history."""
    tallies: dict[str, Tally] = {
        AVOID_MAJOR_BUMPS: branch_rule(
            episodes, TriageBranch.UPGRADE_BREAKING, DecisionAction.REJECTED
        ),
        AVOID_REPLACEMENTS: branch_rule(episodes, TriageBranch.REPLACE, DecisionAction.REJECTED),
        TRUSTS_AUTO_UPGRADES: branch_rule(
            episodes, TriageBranch.AUTO_UPGRADE, DecisionAction.APPROVED
        ),
    }
    rules = [
        ProceduralRule(
            rule=name,
            confidence=tally.confidence,
            derived_from=tally.support + tally.against,
            effect=EFFECTS[name],
        )
        for name, tally in tallies.items()
    ]
    rules += [
        ProceduralRule(
            rule=f"{LICENSE_CLEARED_PREFIX}{package}",
            confidence=tally.confidence,
            derived_from=tally.support + tally.against,
            effect=LICENSE_CLEARED_EFFECT.format(package=package),
        )
        for package, tally in sorted(license_clearances(episodes).items())
    ]
    return [rule for rule in rules if rule.confidence > RETIREMENT_THRESHOLD]


def cleared_packages(rules: list[ProceduralRule]) -> set[str]:
    return {
        rule.rule.removeprefix(LICENSE_CLEARED_PREFIX)
        for rule in rules
        if rule.rule.startswith(LICENSE_CLEARED_PREFIX)
    }


def rule_named(rules: list[ProceduralRule], name: str) -> ProceduralRule | None:
    return next((rule for rule in rules if rule.rule == name), None)
