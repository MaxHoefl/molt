"""Resolution of declared license strings to SPDX identifiers.

PyPI packages declare their license as free prose: a `license` field that may hold
anything from "MIT" to the entire license text, or a trove classifier whose trailing
segment reads "Apache Software License". Every rule in the compatibility matrix and
every verdict keys on an SPDX identifier, so the declared string has to be resolved
to one before any judgment can happen.

Resolution is deterministic and testable, and it is deliberately conservative: a
string that does not resolve to exactly one identifier returns None, which the tool
turns into `requires_human_review`. "BSD License" is the canonical example — the
classifier does not say whether it is the 2- or 3-clause variant, and guessing is
exactly the kind of unilateral legal judgment this tool must never make.
"""

import re
from functools import cache

from license_expression import ExpressionError, Licensing, get_spdx_licensing

# Non-SPDX buckets we still want the matrix to be able to rule on. They are written
# as SPDX LicenseRef- identifiers, which is the escape hatch SPDX itself provides.
PUBLIC_DOMAIN = "LicenseRef-Public-Domain"
PROPRIETARY = "LicenseRef-Proprietary"

# The declared string is a license text, not an identifier, beyond this length.
MAX_DECLARATION_LENGTH = 200

WHITESPACE = re.compile(r"\s+")

# PyPI prose and trove-classifier spellings that map onto exactly one identifier.
ALIASES: dict[str, str] = {
    "mit license": "MIT",
    "the mit license": "MIT",
    "mit license (mit)": "MIT",
    "expat": "MIT",
    "apache 2.0": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "bsd 2-clause license": "BSD-2-Clause",
    "simplified bsd": "BSD-2-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "modified bsd license": "BSD-3-Clause",
    "isc license (iscl)": "ISC",
    "isc license": "ISC",
    "zlib/libpng license": "Zlib",
    "python software foundation license": "Python-2.0",
    "psf": "Python-2.0",
    "psf-2.0": "Python-2.0",
    "postgresql license": "PostgreSQL",
    "the unlicense (unlicense)": "Unlicense",
    "the unlicense": "Unlicense",
    "cc0 1.0 universal (cc0 1.0) public domain dedication": "CC0-1.0",
    "public domain": PUBLIC_DOMAIN,
    "proprietary": PROPRIETARY,
    "other/proprietary license": PROPRIETARY,
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "eclipse public license 2.0": "EPL-2.0",
    "eclipse public license 2.0 (epl-2.0)": "EPL-2.0",
    "gnu general public license v2 (gplv2)": "GPL-2.0-only",
    "gnu general public license v2 or later (gplv2+)": "GPL-2.0-or-later",
    "gnu general public license v3 (gplv3)": "GPL-3.0-only",
    "gnu general public license v3 or later (gplv3+)": "GPL-3.0-or-later",
    "gnu lesser general public license v2 (lgplv2)": "LGPL-2.1-only",
    "gnu lesser general public license v2.1 (lgplv2.1)": "LGPL-2.1-only",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1-or-later",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "gnu lesser general public license v3 or later (lgplv3+)": "LGPL-3.0-or-later",
    "gnu affero general public license v3": "AGPL-3.0-only",
    "gnu affero general public license v3 or later (agpl-3.0+)": "AGPL-3.0-or-later",
    "historical permission notice and disclaimer (hpnd)": "HPND",
    "business source license 1.1": "BUSL-1.1",
    "server side public license": "SSPL-1.0",
    "elastic license 2.0": "Elastic-2.0",
    "artistic license 2.0": "Artistic-2.0",
}

# Spellings that are recognisable but name a family rather than a license. Resolving
# them would mean picking a variant on the user's behalf, so they resolve to nothing.
AMBIGUOUS: frozenset[str] = frozenset(
    {
        "bsd",
        "bsd license",
        "bsd-style",
        "gpl",
        "gnu general public license (gpl)",
        "lgpl",
        "gnu lesser general public license (lgpl)",
        "gnu library or lesser general public license (lgpl)",
        "creative commons",
        "free for non-commercial use",
        "freely distributable",
        "osi approved",
        "other",
        "unknown",
        "dual license",
        "see license file",
        "see license",
    }
)


@cache
def spdx_licensing() -> Licensing:
    """The SPDX symbol index is expensive to build and immutable once built."""
    return get_spdx_licensing()


def declaration_key(raw: str) -> str:
    return WHITESPACE.sub(" ", raw.strip().rstrip(".").lower())


def normalize_license(raw: str | None) -> str | None:
    """Resolves a declared license string to a canonical SPDX expression, or None.

    None means "this tool will not guess": the string was absent, named a license
    family rather than a license, was a wall of license text, or is simply not an
    SPDX identifier. Every None ends up as `requires_human_review`.
    """
    if not raw or not raw.strip():
        return None
    if len(raw.strip()) > MAX_DECLARATION_LENGTH:
        return None
    key = declaration_key(raw)
    if key in AMBIGUOUS:
        return None
    if alias := ALIASES.get(key):
        return alias
    try:
        parsed = spdx_licensing().parse(raw.strip(), validate=True, strict=True)
    except (ExpressionError, ValueError):
        return None
    return str(parsed) if parsed is not None else None


def parse_expression(normalized: str):
    """Parses a normalized identifier back into an expression tree.

    Returns the raw string for anything the SPDX parser does not know — the
    LicenseRef- buckets above, for instance — so callers can treat it as a single
    opaque symbol rather than special-casing it.
    """
    try:
        parsed = spdx_licensing().parse(normalized, validate=True, strict=True)
    except (ExpressionError, ValueError):
        return normalized
    return normalized if parsed is None else parsed
