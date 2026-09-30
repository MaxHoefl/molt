import pytest

from src.domain.models import Distribution, LicenseCompatibility
from src.resources.license_matrix import (
    ALL_DISTRIBUTIONS,
    MATRIX,
    floor_verdict,
    render_matrix,
    rule_for,
    rules_for_license,
)

COMPATIBLE = LicenseCompatibility.COMPATIBLE
INCOMPATIBLE = LicenseCompatibility.INCOMPATIBLE
REVIEW = LicenseCompatibility.REQUIRES_HUMAN_REVIEW


def test_every_rule_explains_itself():
    assert all(rule.dependency_license and rule.rationale for rule in MATRIX)


def test_every_rule_covers_at_least_one_distribution_model():
    assert all(rule.distributions for rule in MATRIX)


def test_no_two_rules_govern_the_same_case():
    """Overlapping rows would make the outcome depend on the order they were written in."""
    keys = [
        (rule.dependency_license.lower(), rule.project_license.lower(), distribution)
        for rule in MATRIX
        for distribution in rule.distributions
    ]

    assert len(keys) == len(set(keys))


def test_the_matrix_is_small_enough_to_hand_to_a_model_whole():
    rendered = render_matrix()

    assert len(rendered.splitlines()) == len(MATRIX)
    assert len(rendered) < 30_000


@pytest.mark.parametrize(
    "dependency, project, distribution, expected",
    [
        ("MIT", "MIT", Distribution.BINARY, COMPATIBLE),
        ("MIT", "GPL-3.0-only", Distribution.BINARY, COMPATIBLE),
        ("Apache-2.0", "MIT", Distribution.BINARY, COMPATIBLE),
        ("MPL-2.0", "MIT", Distribution.BINARY, COMPATIBLE),
        ("LGPL-2.1-only", "MIT", Distribution.BINARY, REVIEW),
        ("LGPL-2.1-only", "MIT", Distribution.SOURCE, COMPATIBLE),
        ("GPL-3.0-only", "MIT", Distribution.BINARY, INCOMPATIBLE),
        ("GPL-3.0-only", "MIT", Distribution.SOURCE, INCOMPATIBLE),
        ("GPL-3.0-only", "MIT", Distribution.SAAS, COMPATIBLE),
        ("AGPL-3.0-only", "MIT", Distribution.SAAS, INCOMPATIBLE),
        ("BUSL-1.1", "MIT", Distribution.SAAS, INCOMPATIBLE),
        ("SSPL-1.0", "MIT", Distribution.SAAS, INCOMPATIBLE),
        ("SSPL-1.0", "MIT", Distribution.BINARY, REVIEW),
    ],
)
def test_governs_the_pairs_the_audit_actually_meets(dependency, project, distribution, expected):
    assert rule_for(dependency, project, distribution).verdict == expected


@pytest.mark.parametrize(
    "dependency, project, expected",
    [
        ("GPL-3.0-only", "GPL-3.0-only", COMPATIBLE),
        ("GPL-3.0-only", "GPL-3.0-or-later", COMPATIBLE),
        ("GPL-2.0-only", "GPL-3.0-only", INCOMPATIBLE),
        ("GPL-2.0-or-later", "GPL-3.0-only", COMPATIBLE),
        ("Apache-2.0", "GPL-2.0-only", INCOMPATIBLE),
        ("Apache-2.0", "GPL-2.0-or-later", COMPATIBLE),
        ("AGPL-3.0-only", "AGPL-3.0-only", COMPATIBLE),
    ],
)
def test_a_row_naming_the_project_license_beats_the_wildcard(dependency, project, expected):
    assert rule_for(dependency, project, Distribution.BINARY).verdict == expected


def test_reports_no_rule_for_a_license_it_does_not_cover():
    assert rule_for("Sleepycat", "MIT", Distribution.BINARY) is None
    assert rules_for_license("Sleepycat") == ()


def test_matches_an_identifier_whatever_its_casing():
    assert rule_for("mit", "MIT", Distribution.BINARY) is not None


def test_reports_every_row_mentioning_a_license():
    rules = rules_for_license("LGPL-3.0-only")

    assert {rule.verdict for rule in rules} == {REVIEW, COMPATIBLE}
    assert set().union(*(set(rule.distributions) for rule in rules)) == set(ALL_DISTRIBUTIONS)


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("MIT", COMPATIBLE),
        ("GPL-3.0-only", INCOMPATIBLE),
        # A choice: the project may take the branch that suits it.
        ("MIT OR GPL-3.0-only", COMPATIBLE),
        ("GPL-3.0-only OR MIT", COMPATIBLE),
        # An obligation: every part has to be satisfied at once.
        ("MIT AND GPL-3.0-only", INCOMPATIBLE),
        ("MIT AND LGPL-2.1-only", REVIEW),
        # An unknown part can only add obligations, so the known parts still bind.
        ("MIT AND Sleepycat", COMPATIBLE),
    ],
)
def test_reads_a_compound_expression_as_a_choice_or_an_obligation(expression, expected):
    assert floor_verdict(expression, "MIT", Distribution.BINARY) == expected


@pytest.mark.parametrize("expression", ["Sleepycat", "MIT OR Sleepycat"])
def test_stays_silent_where_it_cannot_speak(expression):
    """An unknown branch of a choice might be more permissive than any known one."""
    assert floor_verdict(expression, "MIT", Distribution.BINARY) is None
