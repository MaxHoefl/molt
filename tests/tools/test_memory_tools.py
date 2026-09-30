from datetime import UTC, datetime

from src.domain.models import Decision, DecisionAction, SemanticFact, TriageBranch
from src.memory.store import GLOBAL, InMemoryStore, project_key
from src.tools.recall_project_context import recall_project_context_tool
from src.tools.record_decision import record_decision_tool

PROJECT = "/tmp/myapp"
DAY = datetime(2026, 9, 1, tzinfo=UTC)


def record(store, package, action, branch=None, reason="", replacement=None, day=1):
    return record_decision_tool(
        PROJECT,
        Decision(
            package=package, action=action, branch=branch, reason=reason, replacement=replacement
        ),
        store,
        datetime(2026, 9, day, tzinfo=UTC),
    )


def reject_breaking(store, *packages):
    for offset, package in enumerate(packages):
        record(store, package, DecisionAction.REJECTED, TriageBranch.UPGRADE_BREAKING, day=1 + offset)


# --- record_decision ---------------------------------------------------------


def test_every_decision_lands_in_the_episodic_log():
    store = InMemoryStore()

    result = record(store, "paramiko", DecisionAction.REJECTED, TriageBranch.UPGRADE_BREAKING)

    assert result.updated == ["episodic"]
    assert store.episodes(project_key(PROJECT))[0].package == "paramiko"


def test_keeps_the_reason_the_user_gave():
    store = InMemoryStore()

    record(store, "paramiko", DecisionAction.REJECTED, reason="the SSH API changed")

    assert store.episodes(project_key(PROJECT))[0].reason == "the SSH API changed"


def test_an_accepted_replacement_becomes_a_durable_fact():
    store = InMemoryStore()

    result = record(
        store, "pycrypto", DecisionAction.APPROVED, TriageBranch.REPLACE, replacement="pycryptodome"
    )

    assert "semantic" in result.updated
    assert store.fact(project_key(PROJECT), "replacement:pycrypto").value == "pycryptodome"


def test_a_resolved_review_becomes_a_durable_fact():
    store = InMemoryStore()

    record(
        store,
        "chardet",
        DecisionAction.RESOLVED_HUMAN_REVIEW,
        TriageBranch.HUMAN_REVIEW,
        reason="Legal approved LGPL-2.1 for our binary distribution.",
    )

    fact = store.fact(project_key(PROJECT), "license_cleared:chardet")
    assert "Legal approved" in fact.fact


def test_an_ordinary_approval_leaves_no_durable_fact():
    store = InMemoryStore()

    result = record(store, "urllib3", DecisionAction.APPROVED, TriageBranch.AUTO_UPGRADE)

    assert result.updated == ["episodic"]


def test_reports_a_rule_it_created():
    store = InMemoryStore()

    reject_breaking(store, "a", "b")

    result = record(store, "c", DecisionAction.REJECTED, TriageBranch.UPGRADE_BREAKING, day=3)
    assert [(c.rule, c.action) for c in result.rules_changed] == [
        ("avoid_major_bumps", "strengthened")
    ]


def test_the_first_decision_that_forms_a_rule_reports_it_as_created():
    store = InMemoryStore()
    record(store, "a", DecisionAction.REJECTED, TriageBranch.UPGRADE_BREAKING)

    result = record(store, "b", DecisionAction.REJECTED, TriageBranch.UPGRADE_BREAKING, day=2)

    assert [(c.rule, c.action) for c in result.rules_changed] == [("avoid_major_bumps", "created")]
    assert "procedural" in result.updated


def test_a_contradicting_decision_weakens_the_rule():
    store = InMemoryStore()
    reject_breaking(store, "a", "b", "c")

    result = record(store, "d", DecisionAction.APPROVED, TriageBranch.UPGRADE_BREAKING, day=4)

    assert [(c.rule, c.action) for c in result.rules_changed] == [
        ("avoid_major_bumps", "weakened")
    ]


def test_enough_contradiction_retires_the_rule():
    store = InMemoryStore()
    reject_breaking(store, "a", "b")

    changes = [
        change
        for offset, package in enumerate(("c", "d", "e"))
        for change in record(
            store, package, DecisionAction.APPROVED, TriageBranch.UPGRADE_BREAKING, day=3 + offset
        ).rules_changed
    ]

    assert ("avoid_major_bumps", "retired") in [(c.rule, c.action) for c in changes]
    assert store.rules(project_key(PROJECT)) == []


def test_a_rule_never_disagrees_with_the_history_it_came_from():
    """Rules are re-derived, so a rule that outlived its evidence cannot exist."""
    store = InMemoryStore()
    reject_breaking(store, "a", "b", "c")
    for offset, package in enumerate(("d", "e", "f", "g")):
        record(store, package, DecisionAction.APPROVED, TriageBranch.UPGRADE_BREAKING, day=4 + offset)

    assert store.rules(project_key(PROJECT)) == []


def test_decisions_for_one_project_do_not_teach_another():
    store = InMemoryStore()
    reject_breaking(store, "a", "b", "c")

    assert store.rules(project_key("/tmp/other")) == []


# --- recall_project_context --------------------------------------------------


def test_an_unaudited_project_recalls_nothing():
    context = recall_project_context_tool(PROJECT, InMemoryStore())

    assert (context.episodic, context.procedural, context.semantic) == ([], [], [])


def test_recalls_the_whole_history():
    store = InMemoryStore()
    reject_breaking(store, "a", "b", "c")

    context = recall_project_context_tool(PROJECT, store)

    assert [e.package for e in context.episodic] == ["a", "b", "c"]


def test_recalls_the_rules_the_history_produced():
    store = InMemoryStore()
    reject_breaking(store, "a", "b", "c")

    context = recall_project_context_tool(PROJECT, store)

    assert [r.rule for r in context.procedural] == ["avoid_major_bumps"]


def test_a_recalled_rule_carries_the_decisions_it_came_from():
    store = InMemoryStore()
    reject_breaking(store, "a", "b", "c")

    context = recall_project_context_tool(PROJECT, store)

    assert len(context.procedural[0].derived_from) == 3


def test_recalls_the_package_facts_earlier_sessions_established():
    store = InMemoryStore()
    record(
        store, "pycrypto", DecisionAction.APPROVED, TriageBranch.REPLACE, replacement="pycryptodome"
    )

    context = recall_project_context_tool(PROJECT, store)

    assert [f.key for f in context.semantic] == ["replacement:pycrypto"]


def test_recalls_facts_that_are_true_of_every_project_too():
    store = InMemoryStore()
    store.put_fact(GLOBAL, SemanticFact(key="k", fact="pycrypto is archived", established="x"))

    context = recall_project_context_tool(PROJECT, store)

    assert [f.fact for f in context.semantic] == ["pycrypto is archived"]


def test_recalls_under_one_key_however_the_path_was_spelled():
    store = InMemoryStore()
    reject_breaking(store, "a", "b")

    context = recall_project_context_tool("/tmp/../tmp/myapp", store)

    assert len(context.episodic) == 2
