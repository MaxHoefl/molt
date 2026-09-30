from src.config.log_config import logger
from src.domain.models import ProjectContext
from src.memory.store import GLOBAL, MemoryStore, project_key


def recall_project_context_tool(project: str, store: MemoryStore) -> ProjectContext:
    """Load all persisted memory about a project: past audits, learned preferences, and known package facts.

    Returns the episodic audit history for the project (findings, approvals,
    rejections, timestamps), the procedural preferences inferred from those decisions
    (e.g. 'avoid_major_bumps'), and the semantic package facts established in earlier
    sessions (validated replacement mappings, licenses a person has cleared).
    Read-only, and deliberately a tool rather than hidden state: memory that
    influences decisions should be something the user can read. Call it at the start
    of an audit so triage is conditioned on history rather than starting cold.

    Args:
        project: Project path used as the memory key.
        store: Long-term memory store to read from.

    Returns:
        A ProjectContext with episodic, procedural and semantic sections; all sections
        empty for a project that has never been audited.
    """
    key = project_key(project)
    context = ProjectContext(
        project=key,
        episodic=store.episodes(key),
        procedural=store.rules(key),
        # Facts recorded without a project (a package fact true everywhere) are read
        # alongside this project's own, because both are things the audit knows.
        semantic=store.facts(key) + store.facts(GLOBAL),
    )
    logger.info(
        f"Recalled {len(context.episodic)} episodes, {len(context.procedural)} rules and "
        f"{len(context.semantic)} facts for {key}"
    )
    return context
