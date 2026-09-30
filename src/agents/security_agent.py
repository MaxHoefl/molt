from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from src.domain.models import EnrichedPackage, SecurityVerdictDraft, Vulnerability
from src.prompts.assess_security import (
    ADVISORY_TEMPLATE,
    NO_ADVISORIES,
    PACKAGE_TEMPLATE,
    SYSTEM_PROMPT,
)


def build_security_agent(model: BaseChatModel, tools: list | None = None):
    """One specialist agent, structured output enforced by the schema itself.

    `tools` is the seam for advisory retrieval: pass a Chroma-backed retriever
    tool and the same agent grounds itself by searching rather than by reading
    only what enrichment already fetched.
    """
    return create_agent(
        model,
        tools=tools or [],
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(SecurityVerdictDraft),
    )


def render_package(package: EnrichedPackage) -> str:
    return PACKAGE_TEMPLATE.format(
        name=package.name,
        version=package.version,
        latest_version=package.latest_version or "unknown",
        advisories=render_advisories(package.vulnerabilities),
    )


def render_advisories(vulnerabilities: list[Vulnerability]) -> str:
    if not vulnerabilities:
        return NO_ADVISORIES
    return "\n".join(
        ADVISORY_TEMPLATE.format(
            index=index,
            id=vulnerability.id,
            aliases=", ".join(vulnerability.aliases) or "none",
            severity=vulnerability.severity or "not declared",
            cvss_vector=vulnerability.cvss_vector or "not declared",
            fixed_in=vulnerability.fixed_in or "no fix reported",
            summary=vulnerability.summary or "(none)",
            details=vulnerability.details or "(none)",
        )
        for index, vulnerability in enumerate(vulnerabilities, start=1)
    )
