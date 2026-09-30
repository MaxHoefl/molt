"""Golden cases for the replacement search.

This suite is deliberately different from the other three. They score a single
judgment made from a prompt; this one scores a *process* — did the agent reason
about what the package does, look candidates up before naming them, notice when a
lookup disqualified one, and rank what survived?

That means two things a reader should know before trusting a number here:

* The registry is a fixture (see `registry.py`), so the observations are the same
  on every run. Only the model varies.
* Unlike the other suites, the labels do depend on the model knowing the Python
  ecosystem well enough to *name* a candidate — the toolbox verifies names, it does
  not search for them. That is a real property of the tool, not a gap in the eval:
  a model that cannot name pycryptodome will return nothing rather than something
  wrong, which is the failure mode this design chooses.

`probes` names the failure mode each case exists to catch.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvalCase:
    id: str
    probes: str
    package: str
    reason: str
    context: str | None = None
    expected_any_of: frozenset[str] = field(default=frozenset())
    must_not_recommend: frozenset[str] = field(default=frozenset())
    expects_candidates: bool = True


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        id="archived-fork-target",
        probes="the headline case: an archived package with a well-known live fork",
        package="pycrypto",
        reason="Repository archived since 2014; unfixable CVE-2013-7459.",
        context="AES encryption of local config files",
        expected_any_of=frozenset({"pycryptodome", "cryptography"}),
        must_not_recommend=frozenset({"pycrypto", "pycrypto-fork"}),
    ),
    EvalCase(
        id="dead-successor-is-a-trap",
        probes="the obvious successor is itself dormant; the lookup has to change the answer",
        package="nose",
        reason="Abandoned; incompatible with modern Python.",
        context="the unit test suite",
        expected_any_of=frozenset({"pytest"}),
        must_not_recommend=frozenset({"nose"}),
    ),
    EvalCase(
        id="renamed-by-its-own-vendor",
        probes="a package superseded by its vendor's rewrite under a different name",
        package="raven",
        reason="Deprecated by its maintainer in favour of a rewritten client.",
        context="error reporting from a web service",
        expected_any_of=frozenset({"sentry-sdk"}),
        must_not_recommend=frozenset({"raven"}),
    ),
    EvalCase(
        id="two-defensible-answers",
        probes="two live candidates with different licenses; both are acceptable, ranking is not",
        package="mysql-python",
        reason="Unmaintained; no Python 3 support.",
        context="MySQL access from a Django application",
        expected_any_of=frozenset({"mysqlclient", "pymysql"}),
        must_not_recommend=frozenset({"mysql-python"}),
    ),
)
