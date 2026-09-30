from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from src.domain.models import Distribution, EnrichedPackage, LicenseVerdictDraft
from src.prompts.assess_license import NO_RULES, PACKAGE_TEMPLATE, SYSTEM_PROMPT
from src.resources.license_matrix import render_matrix, render_rules, rules_for_license


def build_license_agent(model: BaseChatModel, tools: list | None = None):
    """The second specialist agent, and the one place both retrieval patterns meet.

    The compatibility matrix is small, static and authoritative, so it is baked into
    the system prompt wholesale — that is CAG, and being static it also caches. The
    license *texts* are large and long-tailed and only a clause at a time matters, so
    they are searched — that is RAG, and it arrives as `tools`.

    Which half answers which question is the design: the matrix decides the verdict,
    the retrieved clause supplies the words the verdict is justified in.
    """
    return create_agent(
        model,
        tools=tools or [],
        system_prompt=SYSTEM_PROMPT.format(matrix=render_matrix()),
        response_format=ToolStrategy(LicenseVerdictDraft),
    )


def render_package(
    package: EnrichedPackage,
    license: str,
    project_license: str,
    distribution: Distribution,
) -> str:
    return PACKAGE_TEMPLATE.format(
        name=package.name,
        version=package.version,
        license_declared=package.license_declared or "(none declared)",
        license=license,
        project_license=project_license,
        distribution=distribution.value,
        rules=render_applicable_rules(license),
    )


def render_applicable_rules(license: str) -> str:
    """Every row naming this license, whatever the project or distribution.

    The whole matrix is already in the system prompt; repeating the rows for this
    license saves the agent a lookup without making the choice for it — it still has
    to pick the row that matches the project license and the distribution model.
    """
    rules = tuple(
        rule
        for identifier in _identifiers(license)
        for rule in rules_for_license(identifier)
    )
    return render_rules(rules) if rules else NO_RULES


def _identifiers(license: str) -> list[str]:
    """The individual identifiers in a possibly compound expression, in order."""
    seen: list[str] = []
    for token in license.replace("(", " ").replace(")", " ").split():
        if token.upper() in ("AND", "OR", "WITH") or token in seen:
            continue
        seen.append(token)
    return seen
