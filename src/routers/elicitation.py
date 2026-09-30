"""The human checkpoint, as the MCP protocol expresses it.

`propose_upgrade_plan` takes an `approver` rather than a `Context` so its logic can
be tested without a client. This module is the adapter that turns one into the
other, and it is where the graceful-degradation rule actually lives: a client that
cannot elicit, a user who declines, a transport that drops — every one of them
returns None, which the tool reads as "nobody approved anything" and marks the
proposal pending rather than approved.

The response schema is a per-package field, not a free-text box. "Looks good" is not
an approval this server can act on, because `apply_plan` needs to know which packages
were meant.
"""

from typing import Literal

from pydantic import BaseModel, Field, create_model

from src.config.log_config import logger
from src.domain.models import Approval, UpgradeProposal

DECISIONS = Literal["approved", "rejected"]
HEADER = """\
Molt proposes {count} change(s) to {manifest}.

{diff}
Rationale:
{rationale}
{unresolved}
Approve or reject each package.
"""
OPEN_ITEMS = "\nLeft open for you to decide:\n{items}\n"


def approval_schema(proposal: UpgradeProposal) -> type[BaseModel]:
    """One required field per proposed package, so no answer can be ambiguous."""
    return create_model(
        "UpgradeApprovals",
        **{
            change.package.replace("-", "_").replace(".", "_"): (
                DECISIONS,
                Field(description=f"{change.package}: {change.rationale}"),
            )
            for change in proposal.changes
        },
    )


def render_message(proposal: UpgradeProposal) -> str:
    return HEADER.format(
        count=len(proposal.changes),
        manifest=proposal.manifest_path,
        diff=proposal.diff,
        rationale="\n".join(f"  {name}: {why}" for name, why in proposal.rationale.items()),
        unresolved=(
            OPEN_ITEMS.format(items="\n".join(f"  - {item}" for item in proposal.unresolved))
            if proposal.unresolved
            else ""
        ),
    )


def field_names(proposal: UpgradeProposal) -> dict[str, str]:
    """Maps the sanitized field back to the package it stands for."""
    return {
        change.package.replace("-", "_").replace(".", "_"): change.package
        for change in proposal.changes
    }


def build_approver(context):
    """Wraps an MCP Context into the callable propose_upgrade_plan expects."""

    async def approve(proposal: UpgradeProposal) -> dict[str, Approval] | None:
        if not proposal.changes:
            return {}
        result = await context.elicit(
            render_message(proposal),
            response_type=approval_schema(proposal),
            response_title="Review the proposed dependency changes",
        )
        if getattr(result, "action", None) != "accept" or result.data is None:
            logger.info(f"Proposal {proposal.proposal_id} was not approved through elicitation")
            return None
        names = field_names(proposal)
        return {
            names[field]: Approval(value)
            for field, value in result.data.model_dump().items()
            if field in names
        }

    return approve
