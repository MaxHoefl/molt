"""The MCP surface, exercised through a real client over the in-memory transport.

These tests do not re-check any tool's reasoning — that is what the tool tests are
for. They check the things only the protocol layer can get wrong: that every tool,
prompt and resource is registered under the name the workflow prompt tells a model to
use, that arguments survive serialization, and that a resource template actually
matches the URI a client will build.
"""

import json

import pytest
from fastmcp import Client

from src.domain.models import DecisionAction, TriageBranch
from src.memory.store import InMemoryStore
from src.server import create_mcp_server

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
license = "MIT"
dependencies = ["urllib3==2.0.4"]
"""

UV_LOCK = """\
version = 1

[[package]]
name = "urllib3"
version = "2.0.4"
"""


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def client(store, monkeypatch):
    import src.routers.resources as resources
    import src.routers.tools as tools

    monkeypatch.setattr(tools, "acquire_memory_store", lambda: store)
    monkeypatch.setattr(resources, "acquire_memory_store", lambda: store)
    return Client(create_mcp_server())


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "uv.lock").write_text(UV_LOCK)
    return tmp_path


# --- the surface -------------------------------------------------------------


async def test_registers_every_tool_the_workflow_prompt_names(client):
    async with client:
        names = {tool.name for tool in await client.list_tools()}

    assert names == {
        "scan_project",
        "enrich_dependencies",
        "assess_security",
        "assess_license",
        "assess_maintenance",
        "triage_dependencies",
        "find_replacement",
        "propose_upgrade_plan",
        "apply_plan",
        "recall_project_context",
        "record_decision",
    }


async def test_every_tool_carries_the_docstring_a_model_reads(client):
    async with client:
        tools = await client.list_tools()

    assert all(tool.description and len(tool.description) > 200 for tool in tools)


async def test_exactly_one_tool_says_it_writes_to_files(client):
    async with client:
        tools = await client.list_tools()

    writers = [t.name for t in tools if "ONLY TOOL IN THIS SERVER THAT MODIFIES" in t.description]
    assert writers == ["apply_plan"]


async def test_registers_both_workflow_prompts(client):
    async with client:
        prompts = await client.list_prompts()

    assert {prompt.name for prompt in prompts} == {"audit_and_upgrade", "license_check"}


async def test_registers_the_three_resources(client):
    async with client:
        static = {str(r.uri) for r in await client.list_resources()}
        templated = {str(t.uri_template) for t in await client.list_resource_templates()}

    assert static == {"molt://license-matrix"}
    assert templated == {"molt://policy/{project*}", "molt://audits/{project*}/latest"}


# --- calling through the protocol --------------------------------------------


async def test_scans_a_project_through_the_protocol(client, project):
    async with client:
        result = await client.call_tool("scan_project", {"project_path": str(project)})

    assert result.data.project_license == "MIT"
    assert [d.name for d in result.data.dependencies] == ["urllib3"]


async def test_recalls_nothing_for_an_unaudited_project(client, project):
    async with client:
        result = await client.call_tool("recall_project_context", {"project": str(project)})

    assert result.data.episodic == []


async def test_records_a_decision_through_the_protocol(client, store, project):
    async with client:
        result = await client.call_tool(
            "record_decision",
            {
                "project": str(project),
                "decision": {
                    "package": "chardet",
                    "action": "resolved_human_review",
                    "branch": "HUMAN_REVIEW",
                    "reason": "Legal cleared LGPL-2.1 for our binary distribution.",
                },
            },
        )

    assert "semantic" in result.data.updated


async def test_a_recorded_decision_is_visible_to_the_next_call(client, project):
    async with client:
        await client.call_tool(
            "record_decision",
            {
                "project": str(project),
                "decision": {"package": "paramiko", "action": "rejected", "branch": "UPGRADE_BREAKING"},
            },
        )
        recalled = await client.call_tool("recall_project_context", {"project": str(project)})

    assert recalled.data.episodic[0].action == DecisionAction.REJECTED
    assert recalled.data.episodic[0].branch == TriageBranch.UPGRADE_BREAKING


async def test_refuses_an_apply_with_no_proposal_behind_it(client):
    async with client:
        result = await client.call_tool(
            "apply_plan", {"proposal_id": "prop_nope", "approved_packages": ["urllib3"]}
        )

    assert result.data.status == "aborted"


# --- resources ---------------------------------------------------------------


async def test_serves_the_compatibility_matrix_as_readable_json(client):
    async with client:
        contents = await client.read_resource("molt://license-matrix")

    rules = json.loads(contents[0].text)
    assert {"dep", "project", "distribution", "verdict", "why"} <= set(rules[0])
    assert len(rules) > 40


async def test_serves_an_empty_policy_for_a_project_with_no_history(client, project):
    async with client:
        contents = await client.read_resource(f"molt://policy/{project}")

    assert json.loads(contents[0].text) == []


async def test_serves_the_rules_a_project_has_earned(client, project):
    async with client:
        for package in ("a", "b", "c"):
            await client.call_tool(
                "record_decision",
                {
                    "project": str(project),
                    "decision": {
                        "package": package,
                        "action": "rejected",
                        "branch": "UPGRADE_BREAKING",
                    },
                },
            )
        contents = await client.read_resource(f"molt://policy/{project}")

    assert [rule["rule"] for rule in json.loads(contents[0].text)] == ["avoid_major_bumps"]


def test_opts_out_of_the_absolute_path_check_and_nothing_else():
    # The two project resources are keyed by an absolute path, so they have to opt out
    # of FastMCP's refusal to pass one through a URI template — but only that check.
    # A client normalises away dot segments before the server ever sees them, so this
    # asserts against the policy itself rather than through the protocol.
    from src.routers.resources import PROJECT_PATH_SECURITY

    assert PROJECT_PATH_SECURITY.validate({"project": "/Users/someone/app"}) is None
    assert PROJECT_PATH_SECURITY.validate({"project": "../../etc"}) == "project"
    assert PROJECT_PATH_SECURITY.validate({"project": "/tmp/app\x00"}) == "project"


async def test_says_plainly_when_a_project_has_no_completed_audit(client, project):
    async with client:
        contents = await client.read_resource(f"molt://audits/{project}/latest")

    assert "No completed audit" in json.loads(contents[0].text)["message"]


# --- prompts -----------------------------------------------------------------


async def test_the_audit_prompt_names_the_project_and_the_distribution(client):
    async with client:
        result = await client.get_prompt(
            "audit_and_upgrade", {"project_path": "/tmp/myapp", "distribution": "saas"}
        )

    text = result.messages[0].content.text
    assert "/tmp/myapp" in text and "saas" in text


async def test_the_audit_prompt_forbids_applying_without_approval(client):
    async with client:
        result = await client.get_prompt("audit_and_upgrade", {"project_path": "/tmp/myapp"})

    text = " ".join(result.messages[0].content.text.split())
    assert "only with packages the user has explicitly approved" in text
    assert "If the user approved nothing, do not call apply_plan at all." in text


async def test_the_audit_prompt_orders_the_whole_pipeline(client):
    async with client:
        result = await client.get_prompt("audit_and_upgrade", {"project_path": "/tmp/myapp"})

    text = result.messages[0].content.text
    order = [
        text.index(tool)
        for tool in (
            "recall_project_context",
            "scan_project",
            "enrich_dependencies",
            "assess_security",
            "triage_dependencies",
            "find_replacement",
            "propose_upgrade_plan",
            "apply_plan",
        )
    ]
    assert order == sorted(order)


async def test_the_license_prompt_never_mentions_applying_anything(client):
    async with client:
        result = await client.get_prompt("license_check", {"project_path": "/tmp/myapp"})

    text = result.messages[0].content.text
    assert "do not call apply_plan" in text
    assert "triage_dependencies" not in text
