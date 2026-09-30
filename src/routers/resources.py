"""The MCP resource surface: three read-only views of what drives Molt's behaviour.

Each of these exists because something that influences a verdict should be readable
without running the pipeline that uses it. The compatibility matrix encodes legal
judgment calls; the policy rules quietly re-rank a user's proposals; the last audit
is the answer to "what did we find last month?" that should not cost sixty HTTP
requests and a dozen LLM calls to obtain.
"""

import json

import opik
from fastmcp import FastMCP
from fastmcp.resources import ResourceSecurity

from src.config.log_config import logger
from src.di import acquire_memory_store
from src.memory.store import project_key
from src.resources.license_matrix import MATRIX

NO_AUDITS = "No completed audit is on record for this project."


# Both templated resources are keyed by a project's absolute path, so they have to
# opt out of FastMCP's default refusal to let an absolute path through a URI
# template. The rest of that policy stays on: a `..` segment or a null byte in a
# project path is still a malformed request, and neither resource touches the
# filesystem — `project` is normalised by project_key and used only as a memory-store
# lookup key.
PROJECT_PATH_SECURITY = ResourceSecurity(reject_absolute_paths=False)


def matrix_payload() -> list[dict]:
    return [
        {
            "dep": rule.dependency_license,
            "project": rule.project_license,
            "distribution": [distribution.value for distribution in rule.distributions],
            "verdict": rule.verdict.value,
            "why": rule.rationale,
        }
        for rule in MATRIX
    ]


def register_mcp_resources(app: FastMCP) -> None:
    @app.resource("molt://license-matrix", mime_type="application/json")
    @opik.track(type="general", tags=["resource"])
    async def license_matrix() -> str:
        """The curated license-compatibility matrix used by assess_license.

        Read-only JSON: one entry per (dependency_license, project_license,
        distribution) rule with a verdict and a one-sentence justification. A
        project_license of "*" means the rule applies whatever the project is licensed
        under; a rule naming a project license explicitly wins over it. This is the
        authoritative rule set for automatic license verdicts, and assess_license
        enforces it as a floor: an agent may be stricter than a row, never more
        permissive. Anything not covered here is escalated to human review.
        """
        logger.info(f"[RESOURCE] Serving the license matrix ({len(MATRIX)} rules)")
        return json.dumps(matrix_payload(), indent=2)

    @app.resource(
        "molt://policy/{project*}",
        mime_type="application/json",
        security=PROJECT_PATH_SECURITY,
    )
    @opik.track(type="general", tags=["resource"])
    async def policy(project: str) -> str:
        """The active procedural-memory rules for a project.

        Read-only JSON listing every inferred preference rule with its confidence, the
        past decisions it was derived from, and its effect on triage ranking. Rules are
        re-derived from the episodic log after every decision, so they can never
        disagree with the history they cite, and they change only through
        record_decision or an apply_plan outcome. Read this to answer "why is Molt
        down-ranking this suggestion?".

        Args:
            project: Absolute path of the project whose policy to read.
        """
        rules = acquire_memory_store().rules(project_key(project))
        logger.info(f"[RESOURCE] Serving {len(rules)} policy rules for {project}")
        return json.dumps([rule.model_dump() for rule in rules], indent=2)

    @app.resource(
        "molt://audits/{project*}/latest",
        mime_type="application/json",
        security=PROJECT_PATH_SECURITY,
    )
    @opik.track(type="general", tags=["resource"])
    async def latest_audit(project: str) -> str:
        """The most recent completed audit for a project.

        Read-only JSON snapshot containing the date, the proposal id, a count summary
        (proposed, applied, skipped, still open), the items left open, and the diff as
        it was reviewed. Use it to answer questions about the last audit without
        re-running the network- and LLM-expensive pipeline, or to compare consecutive
        audits.

        Args:
            project: Absolute path of the project whose last audit to read.
        """
        record = acquire_memory_store().latest_audit(project_key(project))
        logger.info(f"[RESOURCE] Serving the latest audit for {project}")
        if record is None:
            return json.dumps({"message": NO_AUDITS, "project": project_key(project)}, indent=2)
        return record.model_dump_json(indent=2)
