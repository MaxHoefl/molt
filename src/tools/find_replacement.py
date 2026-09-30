from collections.abc import Sequence
from datetime import UTC, date, datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from src.agents.outcome import no_structure_reason
from src.agents.replacement_agent import (
    CandidateFacts,
    ReplacementToolbox,
    build_replacement_agent,
    build_tools,
    render_request,
)
from src.config.llm_config import chat_model_for
from src.config.log_config import logger
from src.constants import FIND_REPLACEMENT, MAX_REACT_ITERATIONS, REPLACEMENT_KEY
from src.domain.models import (
    MaintenanceStatus,
    MigrationEffort,
    ReplacementCandidate,
    ReplacementSearch,
    ReplacementSearchDraft,
    SemanticFact,
)
from src.http_client import AiohttpSession, HttpSession
from src.memory.store import MemoryStore, project_key

FROM_MEMORY = "Validated for this project in an earlier audit; the search was skipped."


def remembered_replacement(store: MemoryStore | None, project: str | None, package: str):
    """A replacement this project already validated short-circuits the whole loop.

    This is semantic memory earning its place: the expensive part of the ReAct loop
    is the reasoning, and re-deriving an answer the user already accepted is pure
    cost. The fact is per-project because "drop-in" is a claim about a codebase,
    not about a package.
    """
    if store is None or project is None:
        return None
    return store.fact(project_key(project), REPLACEMENT_KEY.format(package=package))


def remember_replacement(
    store: MemoryStore, project: str, package: str, replacement: str, reason: str
) -> SemanticFact:
    fact = SemanticFact(
        key=REPLACEMENT_KEY.format(package=package),
        fact=f"{package} -> {replacement}: {reason}",
        established=datetime.now(UTC).isoformat(),
        package=package,
        value=replacement,
    )
    store.put_fact(project_key(project), fact)
    return fact


SCHEMA_TOOL = ReplacementSearchDraft.__name__


def count_iterations(messages: Sequence) -> int:
    """One iteration is one round of the model deciding to act.

    The closing turn is a tool call too — structured output is delivered as one — so
    it is excluded by name; otherwise every search would report one iteration more
    than it took.
    """
    return sum(
        1
        for message in messages
        if isinstance(message, AIMessage)
        and any(call["name"] != SCHEMA_TOOL for call in getattr(message, "tool_calls", []) or [])
    )


def build_candidate(
    draft_candidate, facts: CandidateFacts, status: MaintenanceStatus
) -> ReplacementCandidate:
    """Prose from the model, facts from the toolbox — never the other way round."""
    return ReplacementCandidate(
        name=facts.name,
        license=facts.license,
        maintenance_status=status,
        latest_version=facts.latest_version,
        compatibility=draft_candidate.compatibility.strip(),
        migration_effort=draft_candidate.migration_effort,
        evidence=draft_candidate.evidence.strip(),
        dependents=facts.dependents,
    )


def verify(
    package: str, toolbox: ReplacementToolbox, draft: ReplacementSearchDraft
) -> tuple[list[ReplacementCandidate], list[str]]:
    """Keeps only the candidates the loop actually verified against PyPI.

    A recommendation is the one output of this server a user is most likely to act
    on without checking, so a name the agent never looked up does not survive to the
    answer. The dropped names are recorded rather than silently discarded, because a
    model inventing packages is worth knowing about.
    """
    candidates: list[ReplacementCandidate] = []
    corrections: list[str] = []
    seen: set[str] = set()
    for entry in draft.candidates:
        name = entry.name.strip()
        key = name.lower()
        if key == package.strip().lower():
            corrections.append(f"dropped '{name}': it is the package being replaced")
            continue
        if key in seen:
            corrections.append(f"dropped a second entry for '{name}'")
            continue
        facts = toolbox.observed(name)
        if facts is None or not facts.exists:
            corrections.append(f"dropped '{name}': never verified against PyPI during the search")
            continue
        seen.add(key)
        candidates.append(build_candidate(entry, facts, toolbox.maintenance_status(facts)))
    return candidates, corrections


async def search(
    package: str,
    context: str | None,
    reason: str | None,
    model: BaseChatModel,
    session: HttpSession,
    today: date | None = None,
    max_iterations: int = MAX_REACT_ITERATIONS,
) -> ReplacementSearch:
    toolbox = ReplacementToolbox(session, today)
    agent = build_replacement_agent(model, build_tools(toolbox), max_iterations)
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": render_request(package, context, reason)}]},
            # Two graph steps per iteration (decide, act), plus the closing answer.
            {"recursion_limit": 2 * max_iterations + 2},
        )
    except Exception as e:
        logger.warning(f"Replacement search failed for {package}: {e}")
        return ReplacementSearch(package=package, error=f"{type(e).__name__}: {e}")
    draft = result.get("structured_response")
    if not isinstance(draft, ReplacementSearchDraft):
        return ReplacementSearch(
            package=package,
            error=f"ValueError: {no_structure_reason(result, 'structured candidate list')}",
        )
    candidates, corrections = verify(package, toolbox, draft)
    return ReplacementSearch(
        package=package,
        candidates=candidates,
        search_log=[line.strip() for line in draft.search_log if line.strip()],
        iterations=count_iterations(result.get("messages") or []),
        corrections=corrections,
    )


async def recall(
    package: str, fact: SemanticFact, session: HttpSession, today: date | None = None
) -> ReplacementSearch:
    """Re-verifies the remembered name against PyPI rather than trusting the record.

    Memory says this replacement was right once. Whether it is still maintained is a
    question only a lookup answers, so the short-circuit skips the reasoning, not the
    facts.
    """
    toolbox = ReplacementToolbox(session, today)
    await toolbox.lookup_package(fact.value or "")
    facts = toolbox.observed(fact.value or "")
    if facts is None or not facts.exists:
        return ReplacementSearch(
            package=package,
            from_memory=True,
            search_log=[f"memory: {fact.fact}"],
            corrections=[f"dropped remembered '{fact.value}': it is no longer on PyPI"],
        )
    return ReplacementSearch(
        package=package,
        from_memory=True,
        candidates=[
            ReplacementCandidate(
                name=facts.name,
                license=facts.license,
                maintenance_status=toolbox.maintenance_status(facts),
                latest_version=facts.latest_version,
                compatibility="validated previously for this project",
                migration_effort=MigrationEffort.LOW,
                evidence=fact.fact,
            )
        ],
        search_log=[f"memory: {fact.fact}", FROM_MEMORY],
    )


async def find_replacement_tool(
    package: str,
    context: str | None = None,
    reason: str | None = None,
    project: str | None = None,
    model: BaseChatModel | None = None,
    session: HttpSession | None = None,
    store: MemoryStore | None = None,
    today: date | None = None,
    max_iterations: int = MAX_REACT_ITERATIONS,
) -> ReplacementSearch:
    """Find maintained replacement candidates for an abandoned or unfixable package using a ReAct loop.

    Iteratively reasons about the package's purpose, looks candidates up on PyPI,
    GitHub and deps.dev, observes what came back, and refines until it can name
    candidates it has actually verified (at most five iterations). Every candidate the
    loop did not verify is dropped and recorded in `corrections`. A replacement already
    validated for this project short-circuits the search from semantic memory. Read-only.

    Args:
        package: Name of the package to replace.
        context: Optional description of how the project uses the package, which
            sharpens compatibility reasoning.
        reason: Optional reason the package is being replaced, e.g. "archived since
            2014" or "unfixable CVE".
        project: Optional project path; enables the semantic-memory short-circuit.
        model: Chat model to use; defaults to the model configured for this tool.
        session: HTTP session to use; defaults to a live one.
        store: Long-term memory store; defaults to none, disabling recall.
        today: Reference date for recency arithmetic; defaults to today.
        max_iterations: Cap on ReAct iterations.

    Returns:
        A ReplacementSearch with ranked candidates carrying name, license,
        maintenance_status, compatibility, migration_effort, evidence and dependent
        count, plus the search log that produced them.
    """
    logger.info(f"Searching for a replacement for {package}")
    if fact := remembered_replacement(store, project, package):
        logger.info(f"Recalled a validated replacement for {package}: {fact.value}")
        if session is None:
            async with AiohttpSession() as live:
                return await recall(package, fact, live, today)
        return await recall(package, fact, session, today)
    model = model or chat_model_for(FIND_REPLACEMENT)
    if session is None:
        async with AiohttpSession() as live:
            return await search(package, context, reason, model, live, today, max_iterations)
    return await search(package, context, reason, model, session, today, max_iterations)
