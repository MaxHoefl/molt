from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from src.domain.models import (
    LicenseVerdict,
    MaintenanceVerdict,
    SecurityVerdict,
    Severity,
    TriageEntry,
    TriagePlanDraft,
)
from src.prompts.triage import NOT_ASSESSED, PACKAGE_TEMPLATE, PLAN_TEMPLATE, SYSTEM_PROMPT


def build_triage_agent(model: BaseChatModel, tools: list | None = None):
    """The manager agent, and the only one that sees the whole project at once.

    The specialists each answer about one package in isolation, which is what keeps
    them cheap and parallel. Merging their answers is a different job: a rationale
    that says "abandoned *and* carrying an unfixable CVE" only exists at this level,
    so the manager is handed the entire routed plan in a single call rather than one
    package at a time.
    """
    return create_agent(
        model,
        tools=tools or [],
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(TriagePlanDraft),
    )


def render_plan(project: str | None, entries: list[TriageEntry], verdicts: dict) -> str:
    return PLAN_TEMPLATE.format(
        project=project or "(unnamed)",
        packages="\n\n".join(
            render_entry(index, entry, verdicts) for index, entry in enumerate(entries, start=1)
        ),
    )


def render_entry(index: int, entry: TriageEntry, verdicts: dict) -> str:
    security, license, maintenance = verdicts.get(entry.package, (None, None, None))
    return PACKAGE_TEMPLATE.format(
        index=index,
        name=entry.package,
        version=entry.version,
        branch=entry.branch.value,
        target=f", target {entry.target}" if entry.target else "",
        security=render_security(security),
        license=render_license(license),
        maintenance=render_maintenance(maintenance),
    )


def render_security(verdict: SecurityVerdict | None) -> str:
    if verdict is None:
        return NOT_ASSESSED
    if verdict.error:
        return f"FAILED to assess ({verdict.error})"
    if not verdict.applicable_cves:
        return "no applicable advisories"
    fix = f"fixed in {verdict.fixed_in} ({verdict.upgrade_distance.value} bump)" if verdict.fixed_in else "no fix released"
    severity = verdict.max_severity.value if verdict.max_severity != Severity.UNKNOWN else "unrated"
    return f"{severity} — {', '.join(verdict.applicable_cves)}; {fix}. {verdict.evidence}".strip()


def render_license(verdict: LicenseVerdict | None) -> str:
    if verdict is None:
        return NOT_ASSESSED
    if verdict.error:
        return f"FAILED to assess ({verdict.error})"
    clause = f" {verdict.conflicting_clause}" if verdict.conflicting_clause else ""
    return f"{verdict.license or 'unresolved'} -> {verdict.verdict.value}.{clause}".strip()


def render_maintenance(verdict: MaintenanceVerdict | None) -> str:
    if verdict is None:
        return NOT_ASSESSED
    if verdict.error:
        return f"FAILED to assess ({verdict.error})"
    return f"{verdict.status.value} (confidence {verdict.confidence}). {verdict.evidence}".strip()
