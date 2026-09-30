from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from src.domain.models import EnrichedPackage, MaintenanceSignals, MaintenanceVerdictDraft
from src.prompts.assess_maintenance import NO_SIGNAL, PACKAGE_TEMPLATE, SYSTEM_PROMPT

SIGNAL_LABELS: dict[str, str] = {
    "archived": "Repository archived",
    "days_since_last_commit": "Days since last commit",
    "days_since_last_release": "Days since last release",
    "open_issues": "Open issues",
    "contributors": "Contributors",
    "releases_published": "Releases published",
}


def build_maintenance_agent(model: BaseChatModel, tools: list | None = None):
    """The third specialist agent: is anyone still home?

    No retrieval seam here on purpose. Security and license reasoning cite external
    text — an advisory, a license clause — so grounding them in a corpus buys real
    accuracy. A maintenance verdict cites nothing but the six numbers below, which
    already fit in the prompt, so a retriever would only add a failure mode.
    """
    return create_agent(
        model,
        tools=tools or [],
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(MaintenanceVerdictDraft),
    )


def render_package(package: EnrichedPackage, signals: MaintenanceSignals) -> str:
    return PACKAGE_TEMPLATE.format(
        name=package.name,
        version=package.version,
        latest_version=package.latest_version or "unknown",
        signals=render_signals(signals),
    )


def render_signals(signals: MaintenanceSignals) -> str:
    """Every signal is listed, including the missing ones.

    Naming a signal as unavailable is the point: an omitted line reads as a signal
    that did not apply, while "not available" reads as one nobody could measure —
    and the prompt asks for lower confidence in exactly that case.
    """
    return "\n".join(
        f"- {label}: {_format(getattr(signals, field))}"
        for field, label in SIGNAL_LABELS.items()
    )


def _format(value: object) -> str:
    if value is None:
        return NO_SIGNAL
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
