"""A frozen PyPI, GitHub and deps.dev for the replacement evals.

The other suites hand the agent everything it needs in the prompt, so their evals
need no network at all. This agent goes and looks things up, which leaves two
things that could vary between runs — the model's reasoning and the live registry —
and an eval that lets both vary measures neither.

So the registry is pinned here. The names are real, because the loop can only look
up names the model can name; the *facts* are fixtures, chosen to make each case
decidable, and they are not claims about those packages today.
"""

from typing import Any

from tests.support import FakeSession, github_repo, pypi_payload

REFERENCE_DAY = "2026-08-01T00:00:00Z"
OLD_DAY = "2014-06-20T00:00:00Z"


def entry(
    version: str,
    license: str,
    summary: str,
    released: str = REFERENCE_DAY,
    repo: str | None = None,
) -> dict[str, Any]:
    payload = pypi_payload(
        version=version,
        releases={version: [{"upload_time_iso_8601": released}]},
        license=license,
        project_urls={"Homepage": repo} if repo else None,
    )
    payload["info"]["summary"] = summary
    return payload


PYPI: dict[str, Any] = {
    # --- the packages being replaced ---
    "pycrypto": entry("2.6.1", "Public Domain", "Cryptographic modules for Python.", OLD_DAY),
    "nose": entry("1.3.7", "LGPL-2.1", "nose extends unittest to make testing easier.", OLD_DAY),
    "raven": entry("6.10.0", "BSD-3-Clause", "Raven is the legacy Sentry client.", OLD_DAY),
    "mysql-python": entry("1.2.5", "GPL-2.0", "Python interface to MySQL.", OLD_DAY),
    # --- healthy, well-adopted answers ---
    "pycryptodome": entry(
        "3.20.0",
        "BSD-2-Clause",
        "Cryptographic library for Python; a self-contained fork of PyCrypto keeping the "
        "Crypto.* package layout.",
        repo="https://github.com/Legrandin/pycryptodome",
    ),
    "cryptography": entry(
        "43.0.0",
        "Apache-2.0 OR BSD-3-Clause",
        "cryptography is a package which provides cryptographic recipes and primitives.",
        repo="https://github.com/pyca/cryptography",
    ),
    "pytest": entry(
        "8.3.3",
        "MIT",
        "pytest: simple powerful testing with Python.",
        repo="https://github.com/pytest-dev/pytest",
    ),
    "sentry-sdk": entry(
        "2.14.0",
        "MIT",
        "Python client for Sentry, the successor to raven.",
        repo="https://github.com/getsentry/sentry-python",
    ),
    "mysqlclient": entry(
        "2.2.4",
        "GPL-2.0",
        "Python interface to MySQL; a fork of MySQL-python with Python 3 support.",
        repo="https://github.com/PyMySQL/mysqlclient",
    ),
    "pymysql": entry(
        "1.1.1",
        "MIT",
        "Pure-Python MySQL client library implementing the MySQL client protocol.",
        repo="https://github.com/PyMySQL/PyMySQL",
    ),
    # --- traps: plausible names whose facts disqualify them ---
    "nose2": entry(
        "0.15.1",
        "BSD-2-Clause",
        "nose2 is the successor to nose.",
        released=OLD_DAY,
        repo="https://github.com/nose-devs/nose2",
    ),
    "pycrypto-fork": entry(
        "2.7.0",
        "Public Domain",
        "An unmaintained fork of pycrypto.",
        released=OLD_DAY,
        repo="https://github.com/example/pycrypto-fork",
    ),
}

REPOS: dict[str, Any] = {
    "Legrandin/pycryptodome": github_repo(
        html_url="https://github.com/Legrandin/pycryptodome",
        pushed_at="2026-08-15T00:00:00Z",
        open_issues_count=41,
    ),
    "pyca/cryptography": github_repo(
        html_url="https://github.com/pyca/cryptography",
        pushed_at="2026-08-28T00:00:00Z",
        open_issues_count=27,
    ),
    "pytest-dev/pytest": github_repo(
        html_url="https://github.com/pytest-dev/pytest",
        pushed_at="2026-08-30T00:00:00Z",
        open_issues_count=880,
    ),
    "getsentry/sentry-python": github_repo(
        html_url="https://github.com/getsentry/sentry-python",
        pushed_at="2026-08-27T00:00:00Z",
        open_issues_count=90,
    ),
    "PyMySQL/mysqlclient": github_repo(
        html_url="https://github.com/PyMySQL/mysqlclient", pushed_at="2026-07-20T00:00:00Z"
    ),
    "PyMySQL/PyMySQL": github_repo(
        html_url="https://github.com/PyMySQL/PyMySQL", pushed_at="2026-06-10T00:00:00Z"
    ),
    "nose-devs/nose2": github_repo(
        html_url="https://github.com/nose-devs/nose2", pushed_at="2021-03-01T00:00:00Z"
    ),
    "example/pycrypto-fork": github_repo(
        html_url="https://github.com/example/pycrypto-fork", archived=True, pushed_at=OLD_DAY
    ),
}

CONTRIBUTORS = {slug: [{"login": "someone"}] for slug in REPOS}

DEPENDENTS = {
    "pycryptodome": 3120,
    "cryptography": 41000,
    "pytest": 88000,
    "sentry-sdk": 4200,
    "mysqlclient": 1600,
    "pymysql": 2900,
    "nose2": 120,
    "pycrypto-fork": 0,
}


def registry() -> FakeSession:
    return FakeSession(pypi=PYPI, repos=REPOS, contributors=CONTRIBUTORS, dependents=DEPENDENTS)
