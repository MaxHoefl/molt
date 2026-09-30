"""The ReAct loop: reason about the job, look candidates up, revise, repeat.

This is the one place in Molt where an iterative loop is doing real work rather
than decorating a single call. Every other agent is handed everything it needs;
here there is no way to know the right search in advance, because the right search
depends on what the package *is* — and that is itself something the agent has to
work out.

The division of labour is the same as everywhere else in this codebase: the model
proposes, the tools observe, and `verify` in the tool module keeps only what was
actually observed. A candidate the loop never looked up is dropped, not trusted.
"""

from dataclasses import dataclass, field
from datetime import date

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool

from src.clients.deps_dev import DepsDevClient
from src.clients.github import GitHubClient
from src.clients.pypi import PyPiClient, PyPiMetadata
from src.constants import MAX_REACT_ITERATIONS
from src.domain.models import (
    MaintenanceStatus,
    Package,
    RepoHealth,
    ReplacementSearchDraft,
)
from src.prompts.find_replacement import NO_CONTEXT, NO_REASON, REQUEST_TEMPLATE, SYSTEM_PROMPT
from src.resources.spdx import normalize_license
from src.tools.assess_maintenance import floor_status, signals_for

UNKNOWN = "unknown"


@dataclass
class CandidateFacts:
    """Everything the toolbox actually observed about one candidate."""

    name: str
    exists: bool = False
    summary: str | None = None
    license: str | None = None
    latest_version: str | None = None
    latest_release_date: date | None = None
    repo_url: str | None = None
    repo_health: RepoHealth | None = None
    dependents: int | None = None
    available_versions: list[str] = field(default_factory=list)


class ReplacementToolbox:
    """The agent's hands, and the only source of fact in the final answer.

    Every lookup is remembered. `observations` is what `verify` reconciles the
    model's candidate list against, which is why the tools record even a failed
    lookup: "this name is not on PyPI" is itself a finding.
    """

    def __init__(self, session, today: date | None = None) -> None:
        self._pypi = PyPiClient(session)
        self._github = GitHubClient(session)
        self._deps_dev = DepsDevClient(session)
        self._today = today or date.today()
        self.observations: dict[str, CandidateFacts] = {}

    def facts(self, name: str) -> CandidateFacts:
        return self.observations.setdefault(name.strip().lower(), CandidateFacts(name=name.strip()))

    def observed(self, name: str) -> CandidateFacts | None:
        return self.observations.get(name.strip().lower())

    async def lookup_package(self, name: str) -> str:
        """PyPI metadata for one package: summary, license, latest version and release date."""
        facts = self.facts(name)
        try:
            metadata = await self._pypi.metadata(Package(name=name, version=""))
        except Exception as e:
            return f"lookup_package({name}) failed: {e}"
        if metadata is None:
            return f"{name} is not on PyPI."
        self._absorb(facts, metadata)
        return (
            f"{name}: {facts.summary or 'no summary'}\n"
            f"  license: {facts.license or metadata.license_declared or UNKNOWN}\n"
            f"  latest version: {facts.latest_version or UNKNOWN}"
            f" released {self._ago(facts.latest_release_date)}\n"
            f"  releases published: {len(facts.available_versions) or UNKNOWN}\n"
            f"  repository: {facts.repo_url or 'none declared'}"
        )

    async def repository_activity(self, name: str) -> str:
        """Archived flag, last commit, contributor count and open issues for a package's repo."""
        facts = self.facts(name)
        if facts.repo_url is None:
            await self.lookup_package(name)
            facts = self.facts(name)
        if facts.repo_url is None:
            return f"{name} declares no repository, so no activity signals are available."
        try:
            facts.repo_health = await self._github.repo_health(facts.repo_url)
        except Exception as e:
            return f"repository_activity({name}) failed: {e}"
        health = facts.repo_health
        if health is None:
            return f"{name}: repository {facts.repo_url} could not be read."
        return (
            f"{name} ({facts.repo_url}):\n"
            f"  archived: {'yes' if health.archived else 'no'}\n"
            f"  last commit: {self._ago(health.last_commit)}\n"
            f"  contributors: {health.contributors if health.contributors is not None else UNKNOWN}\n"
            f"  open issues: {health.open_issues if health.open_issues is not None else UNKNOWN}"
        )

    async def count_dependents(self, name: str) -> str:
        """How many published packages depend on this one, per deps.dev."""
        facts = self.facts(name)
        try:
            facts.dependents = await self._deps_dev.dependents(name)
        except Exception as e:
            return f"count_dependents({name}) failed: {e}"
        if facts.dependents is None:
            return f"deps.dev has no dependent count for {name}."
        return f"{name} has {facts.dependents} dependents on deps.dev."

    def maintenance_status(self, facts: CandidateFacts) -> MaintenanceStatus:
        """The candidate's health, computed from the same thresholds assess_maintenance uses.

        Reusing that floor rather than asking the model keeps one definition of
        "abandoned" in the codebase: a replacement cannot be recommended as healthy
        under rules the maintenance specialist would call abandoned.
        """
        package = _as_enriched(facts)
        if package.repo_health is None and package.latest_release_date is None:
            return MaintenanceStatus.UNKNOWN
        return floor_status(signals_for(package, self._today)) or MaintenanceStatus.HEALTHY

    def _absorb(self, facts: CandidateFacts, metadata: PyPiMetadata) -> None:
        facts.exists = True
        facts.summary = metadata.summary
        facts.license = normalize_license(metadata.license_declared)
        facts.latest_version = metadata.latest_version
        facts.latest_release_date = metadata.latest_release_date
        facts.repo_url = metadata.repo_url
        facts.available_versions = metadata.available_versions

    def _ago(self, when: date | None) -> str:
        return f"{(self._today - when).days} days ago" if when else UNKNOWN


def _as_enriched(facts: CandidateFacts):
    from src.domain.models import EnrichedPackage

    return EnrichedPackage(
        name=facts.name,
        version=facts.latest_version or "0",
        latest_version=facts.latest_version,
        latest_release_date=facts.latest_release_date,
        available_versions=facts.available_versions,
        repo_health=facts.repo_health,
    )


def build_tools(toolbox: ReplacementToolbox) -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            coroutine=toolbox.lookup_package,
            name="lookup_package",
            description=toolbox.lookup_package.__doc__,
        ),
        StructuredTool.from_function(
            coroutine=toolbox.repository_activity,
            name="repository_activity",
            description=toolbox.repository_activity.__doc__,
        ),
        StructuredTool.from_function(
            coroutine=toolbox.count_dependents,
            name="count_dependents",
            description=toolbox.count_dependents.__doc__,
        ),
    ]


def build_replacement_agent(
    model: BaseChatModel,
    tools: list | None = None,
    max_iterations: int = MAX_REACT_ITERATIONS,
):
    return create_agent(
        model,
        tools=tools or [],
        system_prompt=SYSTEM_PROMPT.format(max_iterations=max_iterations),
        response_format=ToolStrategy(ReplacementSearchDraft),
    )


def render_request(
    package: str,
    context: str | None,
    reason: str | None,
    license: str | None = None,
    latest_version: str | None = None,
) -> str:
    return REQUEST_TEMPLATE.format(
        package=package,
        license=license or UNKNOWN,
        latest_version=latest_version or UNKNOWN,
        reason=reason or NO_REASON,
        context=context or NO_CONTEXT,
    )
