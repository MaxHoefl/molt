import pytest

from src.domain.models import Distribution, LicenseCompatibility, LicenseVerdict
from src.tools.assess_license import assess_license_tool
from tests.support import enriched_package, license_draft, license_model

CHARDET = enriched_package(name="chardet", version="5.0.0", license_declared="LGPL-2.1")
REQUESTS = enriched_package(name="requests", version="2.32.3", license_declared="Apache-2.0")
COMPATIBLE = license_draft("compatible")
REVIEW = license_draft("requires_human_review", "LGPL-2.1 section 6 requires relinking.")
INCOMPATIBLE = license_draft("incompatible", "GPL-3.0 section 5 covers the whole work.")


async def assess(
    packages, script, project_license="MIT", distribution=Distribution.BINARY, **kwargs
) -> list[LicenseVerdict]:
    model = license_model(script)
    result = await assess_license_tool(
        packages, project_license, distribution, model=model, **kwargs
    )
    return result.verdicts


async def assess_one(package, entry, **kwargs) -> LicenseVerdict:
    return (await assess([package], [entry], **kwargs))[0]


async def test_returns_one_verdict_per_package():
    verdicts = await assess([CHARDET, REQUESTS], [REVIEW])

    assert [v.package for v in verdicts] == ["chardet", "requests"]


async def test_returns_no_verdicts_for_an_empty_input():
    assert await assess([], []) == []


async def test_carries_the_pinned_version_into_the_verdict():
    assert (await assess_one(CHARDET, REVIEW)).version == "5.0.0"


async def test_reports_the_resolved_identifier_next_to_what_was_declared():
    verdict = await assess_one(CHARDET, REVIEW)

    assert verdict.license == "LGPL-2.1-only"
    assert verdict.license_declared == "LGPL-2.1"


async def test_resolves_a_pypi_classifier_spelling_to_an_identifier():
    package = enriched_package(name="six", version="1.16.0", license_declared="MIT License")

    assert (await assess_one(package, COMPATIBLE)).license == "MIT"


async def test_keeps_the_verdict_and_the_clause_the_model_produced():
    verdict = await assess_one(CHARDET, REVIEW)

    assert verdict.verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW
    assert verdict.conflicting_clause == "LGPL-2.1 section 6 requires relinking."
    assert verdict.corrections == []


async def test_keeps_the_alternatives_the_model_suggested():
    draft = license_draft("incompatible", "GPL-3.0 section 5.", ("charset-normalizer (MIT)",))

    verdict = await assess_one(CHARDET, draft)

    assert verdict.suggested_alternatives == ["charset-normalizer (MIT)"]


async def test_clears_a_dependency_under_the_project_license_without_asking_the_model():
    package = enriched_package(name="six", version="1.16.0", license_declared="MIT")
    model = license_model([INCOMPATIBLE])

    result = await assess_license_tool([package], "MIT", model=model)

    assert model.prompts == []
    assert result.verdicts[0].verdict == LicenseCompatibility.COMPATIBLE


async def test_escalates_a_dependency_that_declares_no_license_without_asking_the_model():
    package = enriched_package(name="mystery", version="1.0.0", license_declared=None)
    model = license_model([COMPATIBLE])

    result = await assess_license_tool([package], "MIT", model=model)

    assert model.prompts == []
    assert result.verdicts[0].verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW
    assert result.verdicts[0].license is None
    assert "SPDX" in result.verdicts[0].conflicting_clause


async def test_escalates_a_license_family_that_names_no_single_license():
    package = enriched_package(name="legacy", version="1.0.0", license_declared="BSD License")

    verdict = await assess_one(package, COMPATIBLE)

    assert verdict.verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW
    assert verdict.license is None


async def test_escalates_every_package_when_the_project_declares_no_license():
    model = license_model([COMPATIBLE])

    result = await assess_license_tool([CHARDET, REQUESTS], None, model=model)

    assert model.prompts == []
    assert [v.verdict for v in result.verdicts] == [LicenseCompatibility.REQUIRES_HUMAN_REVIEW] * 2


async def test_escalates_every_package_when_the_project_license_does_not_resolve():
    model = license_model([COMPATIBLE])

    result = await assess_license_tool([CHARDET], "All rights reserved", model=model)

    assert model.prompts == []
    assert result.verdicts[0].verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW


async def test_reports_the_project_license_and_distribution_it_assessed_against():
    result = await assess_license_tool(
        [CHARDET], "MIT License", Distribution.SAAS, model=license_model([COMPATIBLE])
    )

    assert result.project_license == "MIT"
    assert result.distribution == Distribution.SAAS


async def test_raises_a_verdict_the_matrix_does_not_allow_to_be_that_permissive():
    gpl = enriched_package(name="gpl-tool", version="1.0.0", license_declared="GPL-3.0")

    verdict = await assess_one(gpl, license_draft("compatible"))

    assert verdict.verdict == LicenseCompatibility.INCOMPATIBLE
    assert "matrix" in verdict.corrections[0]


async def test_raises_a_review_verdict_the_matrix_calls_incompatible():
    gpl = enriched_package(name="gpl-tool", version="1.0.0", license_declared="GPL-3.0")

    verdict = await assess_one(gpl, license_draft("requires_human_review", "Unclear."))

    assert verdict.verdict == LicenseCompatibility.INCOMPATIBLE


async def test_keeps_a_verdict_stricter_than_the_matrix():
    verdict = await assess_one(REQUESTS, license_draft("incompatible", "A patent clause bites."))

    assert verdict.verdict == LicenseCompatibility.INCOMPATIBLE
    assert verdict.corrections == []


async def test_leaves_a_verdict_alone_where_the_matrix_is_silent():
    exotic = enriched_package(name="exotic", version="1.0.0", license_declared="Sleepycat")

    verdict = await assess_one(exotic, license_draft("compatible"))

    assert verdict.verdict == LicenseCompatibility.COMPATIBLE
    assert verdict.corrections == []


async def test_lets_the_distribution_model_decide_a_copyleft_dependency():
    gpl = enriched_package(name="gpl-tool", version="1.0.0", license_declared="GPL-3.0")

    hosted = await assess_one(gpl, license_draft("compatible"), distribution=Distribution.SAAS)
    shipped = await assess_one(gpl, license_draft("compatible"), distribution=Distribution.BINARY)

    assert hosted.verdict == LicenseCompatibility.COMPATIBLE
    assert shipped.verdict == LicenseCompatibility.INCOMPATIBLE


async def test_lets_the_project_license_decide_a_copyleft_dependency():
    gpl = enriched_package(name="gpl-tool", version="1.0.0", license_declared="GPL-3.0")

    verdict = await assess_one(gpl, license_draft("compatible"), project_license="GPL-3.0-or-later")

    assert verdict.verdict == LicenseCompatibility.COMPATIBLE


async def test_drops_a_conflicting_clause_from_a_compatible_verdict():
    verdict = await assess_one(REQUESTS, license_draft("compatible", "Some clause."))

    assert verdict.conflicting_clause == ""
    assert "conflicting clause" in verdict.corrections[0]


async def test_drops_alternatives_from_a_compatible_verdict():
    verdict = await assess_one(REQUESTS, license_draft("compatible", "", ("attrs (MIT)",)))

    assert verdict.suggested_alternatives == []
    assert "attrs (MIT)" in verdict.corrections[-1]


async def test_records_a_non_compatible_verdict_that_cites_nothing():
    verdict = await assess_one(CHARDET, license_draft("requires_human_review"))

    assert verdict.verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW
    assert "cites no conflicting clause" in verdict.corrections[0]


async def test_shows_the_agent_the_package_the_project_and_the_distribution():
    model = license_model([REVIEW])

    await assess_license_tool([CHARDET], "MIT", Distribution.SAAS, model=model)

    prompt = model.prompts[0]
    assert "chardet" in prompt
    assert "5.0.0" in prompt
    assert "LGPL-2.1-only" in prompt
    assert "Project license: MIT" in prompt
    assert "Distribution model: saas" in prompt


async def test_shows_the_agent_the_whole_compatibility_matrix():
    model = license_model([REVIEW])

    await assess_license_tool([CHARDET], "MIT", model=model)

    prompt = model.prompts[0]
    assert "AGPL-3.0-only" in prompt
    assert "BUSL-1.1" in prompt
    assert "MPL-2.0" in prompt


async def test_escalates_when_the_model_fails():
    verdict = await assess_one(CHARDET, RuntimeError("provider is down"))

    assert verdict.error == "RuntimeError: provider is down"
    assert verdict.verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW


async def test_one_failing_package_does_not_sink_the_others():
    model = license_model([RuntimeError("boom"), REVIEW])

    result = await assess_license_tool(
        [CHARDET, REQUESTS], "MIT", model=model, max_concurrency=1
    )

    assert result.verdicts[0].error is not None
    assert result.verdicts[1].error is None


async def test_holds_concurrent_model_calls_below_the_cap():
    packages = [
        enriched_package(name=f"pkg{i}", version="1.0.0", license_declared="MPL-2.0")
        for i in range(10)
    ]
    model = license_model([COMPATIBLE], latency=0.01)

    await assess_license_tool(packages, "MIT", model=model, max_concurrency=3)

    assert model.peak_in_flight <= 3


async def test_never_recommends_an_action():
    assert "action" not in set(LicenseVerdict.model_fields)
    assert "recommendation" not in set(LicenseVerdict.model_fields)


@pytest.mark.parametrize(
    "declared, expected",
    [
        ("MIT OR GPL-3.0-only", LicenseCompatibility.COMPATIBLE),
        ("MIT AND GPL-3.0-only", LicenseCompatibility.INCOMPATIBLE),
    ],
)
async def test_reads_a_compound_expression_as_a_choice_or_an_obligation(declared, expected):
    package = enriched_package(name="dual", version="1.0.0", license_declared=declared)

    verdict = await assess_one(package, license_draft("compatible"))

    assert verdict.verdict == expected


# --- retrieval ---------------------------------------------------------------


async def test_offers_the_agent_the_clause_corpus_to_search():
    """The matrix decides the verdict; the retrieved clause supplies the words."""
    from src.rag.retrieval import LexicalRetriever
    from src.tools.assess_license import retrieval_tools

    assert [tool.name for tool in retrieval_tools(LexicalRetriever())] == ["search_license_text"]


async def test_assesses_without_retrieval_when_it_is_turned_off():
    from src.rag.retrieval import NullRetriever
    from src.tools.assess_license import retrieval_tools

    assert retrieval_tools(NullRetriever()) == []
