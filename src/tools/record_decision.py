"""The write path into long-term memory, and the only one.

`apply_plan` calls it; the user can call it directly. Either way memory formation
goes through one function, which is what makes "what does Molt think it learned?" a
question with an answer — the alternative is memory writes scattered across five
tools, none of which agree on what a decision is.

Procedural rules are never written here. They are *re-derived* from the whole
episodic log after every decision, so a rule can never disagree with the history it
claims to come from.
"""

from datetime import UTC, datetime

from src.config.log_config import logger
from src.constants import REPLACEMENT_KEY
from src.domain.models import (
    Decision,
    DecisionAction,
    EpisodicEntry,
    MemoryUpdateResult,
    ProceduralRule,
    RuleChange,
    SemanticFact,
)
from src.memory.procedural import derive_rules
from src.memory.store import MemoryStore, project_key

CLEARED_KEY = "license_cleared:{package}"
CLEARED_FACT = "{package}: the license question was resolved by a person. {reason}"
REPLACEMENT_FACT = "{package} -> {replacement}: accepted for this project. {reason}"


def semantic_fact(decision: Decision, when: str) -> SemanticFact | None:
    """The durable part of a decision, if it has one.

    Most decisions are episodes and nothing more. Two kinds outlive the audit they
    happened in: a replacement that was accepted, and a license a person cleared —
    both are answers a later run should not have to derive again.
    """
    if decision.replacement:
        return SemanticFact(
            key=REPLACEMENT_KEY.format(package=decision.package),
            fact=REPLACEMENT_FACT.format(
                package=decision.package, replacement=decision.replacement, reason=decision.reason
            ).strip(),
            established=when,
            package=decision.package,
            value=decision.replacement,
        )
    if decision.action == DecisionAction.RESOLVED_HUMAN_REVIEW:
        return SemanticFact(
            key=CLEARED_KEY.format(package=decision.package),
            fact=CLEARED_FACT.format(package=decision.package, reason=decision.reason).strip(),
            established=when,
            package=decision.package,
            value=decision.package,
        )
    return None


def rule_changes(
    before: list[ProceduralRule], after: list[ProceduralRule]
) -> list[RuleChange]:
    """What the re-derivation did to the policy set, in the user's terms."""
    was = {rule.rule: rule for rule in before}
    now = {rule.rule: rule for rule in after}
    changes = [
        RuleChange(rule=name, action="created", effect=rule.effect)
        for name, rule in now.items()
        if name not in was
    ]
    changes += [
        RuleChange(rule=name, action="retired", effect="no longer applied")
        for name in was
        if name not in now
    ]
    changes += [
        RuleChange(
            rule=name,
            action="strengthened" if now[name].confidence > was[name].confidence else "weakened",
            effect=f"confidence {was[name].confidence} -> {now[name].confidence}",
        )
        for name in now
        if name in was and now[name].confidence != was[name].confidence
    ]
    return changes


def record_decision_tool(
    project: str, decision: Decision, store: MemoryStore, now: datetime | None = None
) -> MemoryUpdateResult:
    """Record a user decision into long-term memory and update inferred preferences.

    Appends the decision to the project's episodic log, stores any durable package
    fact it implies into semantic memory (an accepted replacement, a license a person
    cleared), and re-derives the project's procedural preference rules from the whole
    episodic history. Called automatically by apply_plan after a successful apply;
    call it directly to record out-of-band resolutions such as the outcome of a
    HUMAN_REVIEW item.

    Args:
        project: Project path used as the memory key.
        decision: The decision: package, action ('approved', 'rejected',
            'resolved_human_review' or 'deferred'), the triage branch it was taken on,
            an optional free-text reason, and an optional replacement package name.
        store: Long-term memory store to write to.
        now: Timestamp for the entry; defaults to the current time.

    Returns:
        A MemoryUpdateResult listing which memory stores changed and every procedural
        rule that was created, strengthened, weakened or retired as a result.
    """
    key = project_key(project)
    when = (now or datetime.now(UTC)).isoformat()
    store.append_episode(
        key,
        EpisodicEntry(
            recorded_at=when,
            package=decision.package,
            action=decision.action,
            branch=decision.branch,
            reason=decision.reason,
        ),
    )
    updated = ["episodic"]

    if fact := semantic_fact(decision, when):
        store.put_fact(key, fact)
        updated.append("semantic")

    before = store.rules(key)
    after = derive_rules(store.episodes(key))
    store.put_rules(key, after)
    changes = rule_changes(before, after)
    if changes:
        updated.append("procedural")
    logger.info(f"Recorded {decision.action.value} for {decision.package}; {len(changes)} rules changed")
    return MemoryUpdateResult(updated=updated, rules_changed=changes)
