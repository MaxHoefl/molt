import opik
from fastmcp import FastMCP

from src.config.log_config import logger
from src.domain.models import Distribution
from src.prompts.workflows import AUDIT_AND_UPGRADE, LICENSE_CHECK


def register_mcp_prompts(app: FastMCP) -> None:
    @app.prompt
    @opik.track(type="general", tags=["prompt"])
    async def audit_and_upgrade(
        project_path: str, distribution: Distribution = Distribution.BINARY
    ) -> str:
        """Run a complete dependency audit with mandatory human review before any changes.

        Orchestrates: recall_project_context -> scan_project -> enrich_dependencies ->
        (assess_security | assess_license | assess_maintenance, concurrently) ->
        triage_dependencies -> find_replacement for every REPLACE entry ->
        propose_upgrade_plan (which pauses for per-package approval) -> apply_plan for
        approved packages only -> record_decision for anything the user resolved in
        conversation. apply_plan is never called without a reviewed proposal.

        Args:
            project_path: Absolute path to the Python project to audit.
            distribution: How the project ships: 'binary', 'source' or 'saas'. It
                changes the answer for every copyleft dependency, so ask if unsure.
        """
        logger.info(f"[PROMPT] audit_and_upgrade for {project_path} ({distribution})")
        return AUDIT_AND_UPGRADE.format(
            project_path=project_path, distribution=Distribution(distribution).value
        )

    @app.prompt
    @opik.track(type="general", tags=["prompt"])
    async def license_check(
        project_path: str, distribution: Distribution = Distribution.BINARY
    ) -> str:
        """Check only license compatibility for a project and interactively resolve findings.

        Orchestrates scan_project -> enrich_dependencies -> assess_license, presents all
        findings, and for each 'requires_human_review' item asks the user to choose:
        record a resolution, search for replacements, or leave it open. Performs no
        triage and proposes no file changes.

        Args:
            project_path: Absolute path to the Python project.
            distribution: 'binary', 'source' or 'saas'.
        """
        logger.info(f"[PROMPT] license_check for {project_path} ({distribution})")
        return LICENSE_CHECK.format(
            project_path=project_path, distribution=Distribution(distribution).value
        )
