import asyncio
from datetime import date
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from src.constants import DEPS_DEV_API_URL, GITHUB_API_URL, OSV_API_URL, PYPI_API_URL
from src.domain.models import EnrichedPackage, RepoHealth, Vulnerability
from src.http_client import HttpResponse, HttpSession

NOT_FOUND = HttpResponse(status=404)


class FakeSession(HttpSession):
    """In-memory stand-in for the three upstream APIs.

    Entries may be a raw JSON payload, a prepared HttpResponse, or an Exception to
    raise. It also records call order and the peak number of overlapping requests,
    which is what the concurrency tests assert on.
    """

    def __init__(
        self,
        osv: dict[tuple[str, str], Any] | None = None,
        pypi: dict[str, Any] | None = None,
        repos: dict[str, Any] | None = None,
        contributors: dict[str, Any] | None = None,
        dependents: dict[str, Any] | None = None,
        latency: float = 0.0,
    ) -> None:
        self.osv = osv or {}
        self.pypi = pypi or {}
        self.repos = repos or {}
        self.contributors = contributors or {}
        self.dependents = dependents or {}
        self.latency = latency
        self.requested: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def get_json(self, url: str, params: dict[str, str] | None = None) -> HttpResponse:
        return await self._serve(url, self._route(url))

    async def post_json(self, url: str, payload: dict[str, Any]) -> HttpResponse:
        if url != OSV_API_URL:
            return NOT_FOUND
        key = (payload["package"]["name"], payload["version"])
        return await self._serve(url, self.osv.get(key, {}))

    def _route(self, url: str) -> Any:
        if url.startswith(f"{PYPI_API_URL}/") and url.endswith("/json"):
            # Keys are "<name>" or "<name>/<version>". An unkeyed release falls back to
            # the project payload, which is what PyPI returns when nothing changed.
            key = url[len(PYPI_API_URL) + 1 : -len("/json")]
            return self.pypi.get(key if key in self.pypi else key.split("/")[0])
        if url.startswith(f"{GITHUB_API_URL}/repos/"):
            path = url[len(f"{GITHUB_API_URL}/repos/") :]
            if path.endswith("/contributors"):
                return self.contributors.get(path[: -len("/contributors")], [])
            return self.repos.get(path)
        if url.startswith(f"{DEPS_DEV_API_URL}/systems/pypi/packages/"):
            return self._deps_dev(url[len(f"{DEPS_DEV_API_URL}/systems/pypi/packages/") :])
        return None

    def _deps_dev(self, path: str) -> Any:
        """deps.dev answers two questions: which version is current, and who depends on it."""
        if path.endswith(":dependents"):
            name = path.split("/versions/")[0]
            count = self.dependents.get(name)
            return None if count is None else {"dependentCount": count}
        if "/" not in path:
            if path not in self.dependents:
                return None
            return {"versions": [{"versionKey": {"version": "1.0.0"}, "isDefault": True}]}
        return None

    async def _serve(self, url: str, entry: Any) -> HttpResponse:
        self.requested.append(url)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
        finally:
            self.in_flight -= 1
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, HttpResponse):
            return entry
        if entry is None:
            return NOT_FOUND
        return HttpResponse(status=200, data=entry)


def pypi_payload(
    version: str = "1.0.0",
    releases: dict[str, list[dict[str, Any]]] | None = None,
    license: str | None = None,
    license_expression: str | None = None,
    classifiers: tuple[str, ...] = (),
    project_urls: dict[str, str] | None = None,
    home_page: str | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {"version": version, "classifiers": list(classifiers)}
    if license is not None:
        info["license"] = license
    if license_expression is not None:
        info["license_expression"] = license_expression
    if project_urls is not None:
        info["project_urls"] = project_urls
    if home_page is not None:
        info["home_page"] = home_page
    if releases is None:
        releases = {version: [{"upload_time_iso_8601": "2024-01-01T12:00:00.000000Z"}]}
    return {"info": info, "releases": releases}


def release(*upload_times: str) -> list[dict[str, Any]]:
    return [{"upload_time_iso_8601": upload_time} for upload_time in upload_times]


def osv_payload(*vulns: dict[str, Any]) -> dict[str, Any]:
    return {"vulns": list(vulns)}


def vuln(
    id: str = "GHSA-xxxx",
    severity: str | None = None,
    fixed: tuple[str, ...] = (),
    package_name: str | None = None,
    aliases: tuple[str, ...] = (),
    cvss: str | None = None,
    summary: str | None = None,
    range_type: str = "ECOSYSTEM",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": id, "aliases": list(aliases)}
    if severity is not None:
        payload["database_specific"] = {"severity": severity}
    if cvss is not None:
        payload["severity"] = [{"type": "CVSS_V3", "score": cvss}]
    if summary is not None:
        payload["summary"] = summary
    if fixed:
        payload["affected"] = [
            {
                "package": {"name": package_name, "ecosystem": "PyPI"} if package_name else {},
                "ranges": [
                    {"type": range_type, "events": [{"introduced": "0"}] + [{"fixed": f} for f in fixed]}
                ],
            }
        ]
    return payload


def github_repo(
    html_url: str = "https://github.com/psf/requests",
    archived: bool = False,
    pushed_at: str | None = "2024-05-01T09:30:00Z",
    open_issues_count: int = 7,
) -> dict[str, Any]:
    return {
        "html_url": html_url,
        "archived": archived,
        "pushed_at": pushed_at,
        "open_issues_count": open_issues_count,
    }


def contributors_page(total: int) -> HttpResponse:
    link = (
        f'<{GITHUB_API_URL}/repos/o/r/contributors?per_page=1&page=2>; rel="next", '
        f'<{GITHUB_API_URL}/repos/o/r/contributors?per_page=1&page={total}>; rel="last"'
    )
    return HttpResponse(status=200, data=[{"login": "someone"}], headers={"Link": link})


class ScriptedChatModel(BaseChatModel):
    """A chat model whose answers are written in advance.

    Each entry in `script` is either the draft-shaped payload the agent should
    "return" (shaped like whichever schema `schema_name` names), a ready AIMessage,
    or an Exception to raise. Prompts are recorded so tests can assert what the
    agent was actually shown.
    """

    script: list[Any] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)
    sequences: dict[str, list[Any]] = Field(default_factory=dict)
    cursors: dict[str, int] = Field(default_factory=dict)
    schema_name: str = "SecurityVerdictDraft"
    prompts: list[str] = Field(default_factory=list)
    latency: float = 0.0
    in_flight: int = 0
    peak_in_flight: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _next(self) -> Any:
        if self.sequences:
            return self._next_in_sequence()
        if self.answers:
            prompt = self.prompts[-1]
            for key, entry in self.answers.items():
                if key in prompt:
                    return entry
            return {}
        index = min(len(self.prompts) - 1, len(self.script) - 1)
        return self.script[index] if self.script else {}

    def _next_in_sequence(self) -> Any:
        """One scripted turn per invocation, per key — what a multi-turn loop needs.

        `answers` returns the same entry however often a key matches, which is right
        for a single-call agent and useless for a ReAct loop, where the point is that
        the second turn differs from the first.
        """
        prompt = self.prompts[-1]
        for key, entries in self.sequences.items():
            if key not in prompt:
                continue
            index = self.cursors.get(key, 0)
            self.cursors[key] = index + 1
            return entries[min(index, len(entries) - 1)]
        return {}

    def _record(self, messages: list[BaseMessage]) -> None:
        self.prompts.append("\n".join(str(message.content) for message in messages))

    def _respond(self, entry: Any) -> ChatResult:
        if isinstance(entry, Exception):
            raise entry
        message = (
            entry
            if isinstance(entry, AIMessage)
            else AIMessage(
                content="",
                tool_calls=[{"name": self.schema_name, "args": entry, "id": "call_1"}],
            )
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self._record(messages)
        return self._respond(self._next())

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self._record(messages)
        entry = self._next()
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
        finally:
            self.in_flight -= 1
        return self._respond(entry)


def draft(
    applicable_cves: tuple[str, ...] = (),
    max_severity: str = "UNKNOWN",
    fixed_in: str | None = None,
    evidence: str = "grounded in the advisory text",
) -> dict[str, Any]:
    return {
        "applicable_cves": list(applicable_cves),
        "max_severity": max_severity,
        "fixed_in": fixed_in,
        "evidence": evidence,
    }


def enriched_package(
    name: str = "paramiko",
    version: str = "2.7.2",
    vulnerabilities: tuple[Vulnerability, ...] = (),
    latest_version: str | None = None,
    license_declared: str | None = None,
    repo_health: RepoHealth | None = None,
    latest_release_date: date | None = None,
    available_versions: tuple[str, ...] = (),
) -> EnrichedPackage:
    return EnrichedPackage(
        name=name,
        version=version,
        vulnerabilities=list(vulnerabilities),
        latest_version=latest_version,
        license_declared=license_declared,
        repo_health=repo_health,
        latest_release_date=latest_release_date,
        available_versions=list(available_versions),
    )


def repo(
    archived: bool | None = False,
    last_commit: date | None = date(2026, 6, 1),
    open_issues: int | None = 12,
    contributors: int | None = 40,
) -> RepoHealth:
    return RepoHealth(
        repo_url="https://github.com/o/r",
        archived=archived,
        last_commit=last_commit,
        open_issues=open_issues,
        contributors=contributors,
    )


def maintenance_draft(
    status: str = "healthy",
    confidence: float = 0.9,
    evidence: str = "commits and releases in the last month",
) -> dict[str, Any]:
    return {"status": status, "confidence": confidence, "evidence": evidence}


def maintenance_model(
    script: Any = (), answers: dict[str, Any] | None = None, latency: float = 0.0
) -> ScriptedChatModel:
    """A scripted model answering as the maintenance agent's structured-output tool."""
    return ScriptedChatModel(
        script=list(script),
        answers=answers or {},
        schema_name="MaintenanceVerdictDraft",
        latency=latency,
    )


def license_draft(
    verdict: str = "compatible",
    conflicting_clause: str = "",
    suggested_alternatives: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "conflicting_clause": conflicting_clause,
        "suggested_alternatives": list(suggested_alternatives),
    }


def license_model(script: Any = (), answers: dict[str, Any] | None = None, latency: float = 0.0) -> ScriptedChatModel:
    """A scripted model answering as the license agent's structured-output tool."""
    return ScriptedChatModel(
        script=list(script),
        answers=answers or {},
        schema_name="LicenseVerdictDraft",
        latency=latency,
    )


def advisory(
    id: str = "GHSA-45x7-px36-x8w8",
    aliases: tuple[str, ...] = ("CVE-2023-48795",),
    severity: str | None = "HIGH",
    fixed_in: str | None = "3.4.0",
    summary: str | None = "Terrapin attack truncates the SSH extension negotiation.",
    details: str | None = None,
) -> Vulnerability:
    return Vulnerability(
        id=id,
        aliases=list(aliases),
        severity=severity,
        fixed_in=fixed_in,
        summary=summary,
        details=details,
    )


def replacement_draft(
    candidates: tuple[dict[str, Any], ...] = (),
    search_log: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {"candidates": list(candidates), "search_log": list(search_log)}


def candidate(
    name: str = "pycryptodome",
    compatibility: str = "drop-in (same Crypto.* namespace)",
    migration_effort: str = "low",
    evidence: str = "312 dependents on deps.dev; released 40 days ago.",
) -> dict[str, Any]:
    return {
        "name": name,
        "compatibility": compatibility,
        "migration_effort": migration_effort,
        "evidence": evidence,
    }


def tool_call(tool: str, **arguments: Any) -> AIMessage:
    """An assistant turn that reaches for a tool, which is what drives a ReAct loop."""
    return AIMessage(
        content="",
        tool_calls=[{"name": tool, "args": arguments, "id": f"call_{tool}"}],
    )


def triage_draft(*entries: tuple[str, str, str]) -> dict[str, Any]:
    """The manager's answer: one (package, branch, rationale) triple per package."""
    return {
        "entries": [
            {"package": package, "branch": branch, "rationale": rationale}
            for package, branch, rationale in entries
        ]
    }


def triage_model(script: Any = (), answers: dict[str, Any] | None = None) -> ScriptedChatModel:
    return ScriptedChatModel(
        script=list(script), answers=answers or {}, schema_name="TriagePlanDraft"
    )
