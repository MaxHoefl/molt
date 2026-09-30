from src.domain.models import DecisionAction, EpisodicEntry, TriageBranch
from src.memory.procedural import (
    AVOID_MAJOR_BUMPS,
    AVOID_REPLACEMENTS,
    LICENSE_CLEARED_PREFIX,
    TRUSTS_AUTO_UPGRADES,
    cleared_packages,
    derive_rules,
    rule_named,
)

BREAKING = TriageBranch.UPGRADE_BREAKING


def episode(package, action, branch=BREAKING, day="12") -> EpisodicEntry:
    return EpisodicEntry(
        recorded_at=f"2026-07-{day}T09:00:00+00:00", package=package, action=action, branch=branch
    )


def rejections(*packages, branch=BREAKING) -> list[EpisodicEntry]:
    return [episode(p, DecisionAction.REJECTED, branch) for p in packages]


def test_derives_nothing_from_no_history():
    assert derive_rules([]) == []


def test_derives_nothing_from_a_single_decision():
    """One rejection is a mood; a rule needs a pattern."""
    assert derive_rules(rejections("paramiko")) == []


def test_derives_an_aversion_to_major_bumps_from_repeated_rejections():
    rules = derive_rules(rejections("paramiko", "sqlalchemy", "pydantic"))

    assert rule_named(rules, AVOID_MAJOR_BUMPS).confidence == 0.83


def test_confidence_grows_with_evidence():
    three = derive_rules(rejections("a", "b", "c"))
    six = derive_rules(rejections("a", "b", "c", "d", "e", "f"))

    assert rule_named(six, AVOID_MAJOR_BUMPS).confidence > rule_named(
        three, AVOID_MAJOR_BUMPS
    ).confidence


def test_confidence_never_reaches_certainty():
    rules = derive_rules(rejections(*[str(n) for n in range(40)]))

    assert rule_named(rules, AVOID_MAJOR_BUMPS).confidence < 1.0


def test_a_rule_names_the_decisions_it_came_from():
    rules = derive_rules(rejections("paramiko", "sqlalchemy", "pydantic"))

    assert rule_named(rules, AVOID_MAJOR_BUMPS).derived_from == [
        "paramiko rejected 2026-07-12",
        "sqlalchemy rejected 2026-07-12",
        "pydantic rejected 2026-07-12",
    ]


def test_a_rule_says_what_it_does_to_a_plan():
    rules = derive_rules(rejections("a", "b", "c"))

    assert "sorted last" in rule_named(rules, AVOID_MAJOR_BUMPS).effect


def test_a_contradicting_decision_lowers_confidence():
    consistent = derive_rules(rejections("a", "b", "c"))
    contradicted = derive_rules(
        rejections("a", "b", "c") + [episode("d", DecisionAction.APPROVED)]
    )

    assert rule_named(contradicted, AVOID_MAJOR_BUMPS).confidence < rule_named(
        consistent, AVOID_MAJOR_BUMPS
    ).confidence


def test_a_rule_the_user_keeps_contradicting_is_retired_rather_than_kept_weakly():
    episodes = rejections("a", "b") + [
        episode(p, DecisionAction.APPROVED) for p in ("c", "d", "e")
    ]

    assert rule_named(derive_rules(episodes), AVOID_MAJOR_BUMPS) is None


def test_a_deferral_is_neither_support_nor_contradiction():
    """Deferring says "not now", not "not ever"."""
    episodes = rejections("a", "b", "c") + [episode("d", DecisionAction.DEFERRED)]

    assert rule_named(derive_rules(episodes), AVOID_MAJOR_BUMPS).confidence == 0.83


def test_branches_are_tallied_separately():
    episodes = rejections("a", "b", "c") + rejections(
        "d", "e", "f", branch=TriageBranch.REPLACE
    )

    rules = derive_rules(episodes)
    assert rule_named(rules, AVOID_MAJOR_BUMPS) is not None
    assert rule_named(rules, AVOID_REPLACEMENTS) is not None


def test_derives_a_positive_rule_from_repeated_approvals():
    episodes = [
        episode(p, DecisionAction.APPROVED, TriageBranch.AUTO_UPGRADE)
        for p in ("a", "b", "c")
    ]

    assert rule_named(derive_rules(episodes), TRUSTS_AUTO_UPGRADES) is not None


def test_a_human_review_resolution_clears_that_package():
    episodes = [
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW, "12"),
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW, "20"),
    ]

    assert cleared_packages(derive_rules(episodes)) == {"chardet"}


def test_a_clearance_is_scoped_to_the_package_that_was_cleared():
    episodes = [
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW),
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW, "20"),
    ]

    assert "lxml" not in cleared_packages(derive_rules(episodes))


def test_a_clearance_can_be_taken_back_by_a_later_decision():
    episodes = [
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW),
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW, "20"),
        episode("chardet", DecisionAction.REJECTED, TriageBranch.HUMAN_REVIEW, "25"),
        episode("chardet", DecisionAction.DEFERRED, TriageBranch.HUMAN_REVIEW, "26"),
    ]

    assert cleared_packages(derive_rules(episodes)) == set()


def test_a_cleared_package_rule_names_the_package_in_its_effect():
    episodes = [
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW),
        episode("chardet", DecisionAction.RESOLVED_HUMAN_REVIEW, TriageBranch.HUMAN_REVIEW, "20"),
    ]

    rule = rule_named(derive_rules(episodes), f"{LICENSE_CLEARED_PREFIX}chardet")
    assert "chardet" in rule.effect
