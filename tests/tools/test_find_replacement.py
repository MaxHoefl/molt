from datetime import UTC, date, datetime

from src.domain.models import MaintenanceStatus, MigrationEffort, SemanticFact
from src.memory.store import InMemoryStore, project_key
from src.constants import REPLACEMENT_KEY
from src.tools.find_replacement import find_replacement_tool
from tests.support import (
    FakeSession,
    ScriptedChatModel,
    candidate,
    github_repo,
    pypi_payload,
    replacement_draft,
    tool_call,
)

TODAY = date(2026, 9, 1)
PROJECT = "/tmp/myapp"


def session(**overrides) -> FakeSession:
    """PyPI, GitHub and deps.dev, populated with one live fork and one dead original."""
    defaults = dict(
        pypi={
            "pycryptodome": pypi_payload(
                version="3.20.0",
                releases={"3.19.0": [], "3.20.0": [{"upload_time_iso_8601": "2026-07-01T00:00:00Z"}]},
                license="BSD-2-Clause",
                project_urls={"Homepage": "https://github.com/Legrandin/pycryptodome"},
            ),
            "cryptography": pypi_payload(
                version="43.0.0",
                releases={"43.0.0": [{"upload_time_iso_8601": "2026-08-01T00:00:00Z"}]},
                license="Apache-2.0",
            ),
            "pycrypto": pypi_payload(version="2.6.1", license="Public Domain"),
        },
        repos={
            "Legrandin/pycryptodome": github_repo(
                html_url="https://github.com/Legrandin/pycryptodome",
                pushed_at="2026-08-15T00:00:00Z",
            )
        },
        dependents={"pycryptodome": 312},
    )
    return FakeSession(**(defaults | overrides))


def model(script) -> ScriptedChatModel:
    return ScriptedChatModel(script=list(script), schema_name="ReplacementSearchDraft")


async def find(script, package: str = "pycrypto", **kwargs):
    return await find_replacement_tool(
        package,
        model=model(script),
        session=kwargs.pop("session", None) or session(),
        today=TODAY,
        **kwargs,
    )


# --- the loop ----------------------------------------------------------------


async def test_returns_the_candidates_the_loop_verified():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            replacement_draft((candidate(),)),
        ]
    )

    assert [c.name for c in result.candidates] == ["pycryptodome"]


async def test_counts_the_iterations_the_loop_actually_took():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            tool_call("count_dependents", name="pycryptodome"),
            replacement_draft((candidate(),)),
        ]
    )

    assert result.iterations == 2


async def test_keeps_the_search_log_the_agent_wrote():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            replacement_draft(
                (candidate(),), ("iter1: domain is crypto primitives", "  ", "iter2: verified")
            ),
        ]
    )

    assert result.search_log == ["iter1: domain is crypto primitives", "iter2: verified"]


async def test_stops_the_loop_at_the_iteration_cap():
    """A loop that will not converge is ended rather than left to run."""
    result = await find([tool_call("lookup_package", name="pycryptodome")], max_iterations=2)

    assert result.candidates == []
    assert "GraphRecursionError" in (result.error or "")


# --- facts come from the tools, prose from the model -------------------------


async def test_takes_the_license_from_pypi_rather_than_from_the_model():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            replacement_draft((candidate(),)),
        ]
    )

    assert result.candidates[0].license == "BSD-2-Clause"


async def test_takes_the_dependent_count_from_deps_dev():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            tool_call("count_dependents", name="pycryptodome"),
            replacement_draft((candidate(),)),
        ]
    )

    assert result.candidates[0].dependents == 312


async def test_keeps_the_compatibility_judgment_the_model_made():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            replacement_draft((candidate(compatibility="drop-in", migration_effort="low"),)),
        ]
    )

    assert result.candidates[0].compatibility == "drop-in"
    assert result.candidates[0].migration_effort == MigrationEffort.LOW


async def test_derives_the_candidate_health_from_the_same_thresholds_as_the_specialist():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            tool_call("repository_activity", name="pycryptodome"),
            replacement_draft((candidate(),)),
        ]
    )

    assert result.candidates[0].maintenance_status == MaintenanceStatus.HEALTHY


async def test_a_candidate_with_a_dead_repository_is_not_reported_healthy():
    dead = session(
        repos={
            "Legrandin/pycryptodome": github_repo(
                html_url="https://github.com/Legrandin/pycryptodome", archived=True
            )
        }
    )

    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            tool_call("repository_activity", name="pycryptodome"),
            replacement_draft((candidate(),)),
        ],
        session=dead,
    )

    assert result.candidates[0].maintenance_status == MaintenanceStatus.ABANDONED


# --- the guard rail ----------------------------------------------------------


async def test_drops_a_candidate_the_loop_never_looked_up():
    result = await find([replacement_draft((candidate(name="crypto-magic"),))])

    assert result.candidates == []
    assert "never verified against PyPI" in result.corrections[0]


async def test_drops_a_candidate_that_is_not_on_pypi():
    result = await find(
        [
            tool_call("lookup_package", name="no-such-package"),
            replacement_draft((candidate(name="no-such-package"),)),
        ]
    )

    assert result.candidates == []
    assert "never verified against PyPI" in result.corrections[0]


async def test_keeps_the_verified_candidates_when_one_is_invented():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            replacement_draft((candidate(name="crypto-magic"), candidate())),
        ]
    )

    assert [c.name for c in result.candidates] == ["pycryptodome"]
    assert len(result.corrections) == 1


async def test_drops_the_package_being_replaced():
    result = await find(
        [
            tool_call("lookup_package", name="pycrypto"),
            replacement_draft((candidate(name="pycrypto"),)),
        ]
    )

    assert result.candidates == []
    assert "the package being replaced" in result.corrections[0]


async def test_drops_a_repeated_candidate():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            replacement_draft((candidate(), candidate())),
        ]
    )

    assert len(result.candidates) == 1
    assert "second entry" in result.corrections[0]


async def test_preserves_the_ranking_the_agent_chose():
    result = await find(
        [
            tool_call("lookup_package", name="pycryptodome"),
            tool_call("lookup_package", name="cryptography"),
            replacement_draft((candidate(name="cryptography"), candidate())),
        ]
    )

    assert [c.name for c in result.candidates] == ["cryptography", "pycryptodome"]


async def test_reports_a_failed_search_rather_than_inventing_one():
    result = await find([RuntimeError("model exploded")])

    assert result.candidates == []
    assert result.error == "RuntimeError: model exploded"


# --- the memory short-circuit ------------------------------------------------


def store_with_validated_replacement() -> InMemoryStore:
    store = InMemoryStore()
    store.put_fact(
        project_key(PROJECT),
        SemanticFact(
            key=REPLACEMENT_KEY.format(package="pycrypto"),
            fact="pycrypto -> pycryptodome validated as a drop-in for this project",
            established=datetime.now(UTC).isoformat(),
            package="pycrypto",
            value="pycryptodome",
        ),
    )
    return store


async def test_a_remembered_replacement_skips_the_loop_entirely():
    scripted = model([replacement_draft()])

    result = await find_replacement_tool(
        "pycrypto",
        project=PROJECT,
        model=scripted,
        session=session(),
        store=store_with_validated_replacement(),
        today=TODAY,
    )

    assert scripted.prompts == []
    assert result.from_memory is True
    assert [c.name for c in result.candidates] == ["pycryptodome"]


async def test_a_remembered_replacement_is_still_verified_against_pypi():
    """Memory says it was right once; whether it still exists is a lookup, not a memory."""
    result = await find_replacement_tool(
        "pycrypto",
        project=PROJECT,
        model=model([]),
        session=FakeSession(),
        store=store_with_validated_replacement(),
        today=TODAY,
    )

    assert result.candidates == []
    assert "no longer on PyPI" in result.corrections[0]


async def test_memory_for_one_project_does_not_short_circuit_another():
    result = await find_replacement_tool(
        "pycrypto",
        project="/tmp/other",
        model=model([replacement_draft()]),
        session=session(),
        store=store_with_validated_replacement(),
        today=TODAY,
    )

    assert result.from_memory is False


async def test_the_search_runs_when_nothing_is_remembered():
    result = await find_replacement_tool(
        "pycrypto",
        project=PROJECT,
        model=model([tool_call("lookup_package", name="pycryptodome"), replacement_draft((candidate(),))]),
        session=session(),
        store=InMemoryStore(),
        today=TODAY,
    )

    assert result.from_memory is False
    assert [c.name for c in result.candidates] == ["pycryptodome"]


# --- what the agent is shown -------------------------------------------------


async def test_shows_the_agent_the_reason_and_the_usage_context():
    scripted = model([replacement_draft()])

    await find_replacement_tool(
        "pycrypto",
        context="AES encryption of local config files",
        reason="archived since 2014",
        model=scripted,
        session=session(),
        today=TODAY,
    )

    assert "AES encryption of local config files" in scripted.prompts[0]
    assert "archived since 2014" in scripted.prompts[0]
