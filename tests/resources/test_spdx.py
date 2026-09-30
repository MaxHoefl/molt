import pytest

from src.resources.spdx import PROPRIETARY, PUBLIC_DOMAIN, normalize_license


@pytest.mark.parametrize(
    "declared, expected",
    [
        ("MIT", "MIT"),
        ("MIT License", "MIT"),
        ("  mit license  ", "MIT"),
        ("MIT License.", "MIT"),
        ("Apache Software License", "Apache-2.0"),
        ("Apache 2.0", "Apache-2.0"),
        ("Apache-2.0", "Apache-2.0"),
        ("ISC License (ISCL)", "ISC"),
        ("Python Software Foundation License", "Python-2.0"),
        ("Mozilla Public License 2.0 (MPL 2.0)", "MPL-2.0"),
        ("Public Domain", PUBLIC_DOMAIN),
        ("Other/Proprietary License", PROPRIETARY),
    ],
)
def test_resolves_the_spellings_pypi_actually_uses(declared, expected):
    assert normalize_license(declared) == expected


@pytest.mark.parametrize(
    "declared, expected",
    [
        ("GPL-3.0", "GPL-3.0-only"),
        ("GNU General Public License v3 (GPLv3)", "GPL-3.0-only"),
        ("GNU General Public License v3 or later (GPLv3+)", "GPL-3.0-or-later"),
        ("GPL-3.0+", "GPL-3.0-or-later"),
        ("LGPL-2.1", "LGPL-2.1-only"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "LGPL-3.0-only"),
        ("AGPL-3.0", "AGPL-3.0-only"),
    ],
)
def test_disambiguates_the_gnu_family_to_only_or_or_later(declared, expected):
    """"GPL-3.0" is deprecated and ambiguous in SPDX; the -only form is what it means."""
    assert normalize_license(declared) == expected


@pytest.mark.parametrize(
    "declared",
    [
        None,
        "",
        "   ",
        "BSD License",
        "BSD",
        "GNU General Public License (GPL)",
        "Free for non-commercial use",
        "Other",
        "Custom Commercial EULA v2",
    ],
)
def test_refuses_to_guess_at_a_family_or_an_unknown_grant(declared):
    assert normalize_license(declared) is None


def test_refuses_a_declaration_that_is_a_license_text_rather_than_an_identifier():
    text = "Permission is hereby granted, free of charge, to any person obtaining a copy " * 5

    assert normalize_license(text) is None


@pytest.mark.parametrize(
    "declared, expected",
    [
        ("MIT OR Apache-2.0", "MIT OR Apache-2.0"),
        ("MIT AND GPL-3.0", "MIT AND GPL-3.0-only"),
        ("(MIT OR Apache-2.0) AND BSD-3-Clause", "(MIT OR Apache-2.0) AND BSD-3-Clause"),
    ],
)
def test_keeps_a_compound_expression_intact(declared, expected):
    assert normalize_license(declared) == expected
