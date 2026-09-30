"""Golden cases for the triage manager.

The branch a package lands on is decided by rules, not by the model, so this suite
does not score routing — `tests/tools/test_triage_dependencies.py` already pins that
down exactly. What it scores is what the manager actually contributes:

* **the merged rationale** — one or two sentences that say what the three specialists
  found, in the user's language rather than the schema's;
* **escalation discipline** — the manager may move an entry to HUMAN_REVIEW and
  nothing else, and it should do so exactly when the specialists contradict each
  other rather than whenever a package looks alarming.

Cases are whole projects, not single packages, because the manager sees the project
in one call and the failure mode worth catching — a rationale that copies one
specialist and forgets the other two — only appears when several packages compete
for its attention.

`probes` names the failure mode each case exists to catch.
"""

from dataclasses import dataclass, field

from src.domain.models import (
    LicenseCompatibility,
    LicenseVerdict,
    MaintenanceStatus,
    MaintenanceVerdict,
    SecurityVerdict,
    Severity,
    TriageBranch,
    UpgradeDistance,
)

COMPATIBLE = LicenseCompatibility.COMPATIBLE
REVIEW_LICENSE = LicenseCompatibility.REQUIRES_HUMAN_REVIEW
INCOMPATIBLE = LicenseCompatibility.INCOMPATIBLE


def secure(package, version="1.0.0") -> SecurityVerdict:
    return SecurityVerdict(
        package=package,
        version=version,
        max_severity=Severity.NONE,
        upgrade_distance=UpgradeDistance.NONE,
        evidence="OSV.dev reported no advisories affecting this version.",
    )


def vulnerable(
    package,
    version,
    cves,
    severity=Severity.HIGH,
    fixed_in=None,
    distance=UpgradeDistance.PATCH,
    evidence="",
) -> SecurityVerdict:
    return SecurityVerdict(
        package=package,
        version=version,
        applicable_cves=list(cves),
        max_severity=severity,
        fixed_in=fixed_in,
        upgrade_distance=distance,
        evidence=evidence,
    )


def licensed(package, version, identifier="MIT", verdict=COMPATIBLE, clause="") -> LicenseVerdict:
    return LicenseVerdict(
        package=package,
        version=version,
        license=identifier,
        verdict=verdict,
        conflicting_clause=clause,
    )


def maintained(package, version, status=MaintenanceStatus.HEALTHY, confidence=0.9, evidence="") -> MaintenanceVerdict:
    return MaintenanceVerdict(
        package=package, version=version, status=status, confidence=confidence, evidence=evidence
    )


@dataclass(frozen=True)
class PackageCase:
    id: str
    probes: str
    security: SecurityVerdict
    license: LicenseVerdict
    maintenance: MaintenanceVerdict
    expected_branch: TriageBranch
    must_mention: tuple[tuple[str, ...], ...] = field(default=())
    must_not_mention: tuple[str, ...] = field(default=())
    expects_escalation: bool = False

    @property
    def package(self) -> str:
        return self.security.package


CASES: tuple[PackageCase, ...] = (
    PackageCase(
        id="patch-fix",
        probes="the rationale names the advisory and the size of the bump, not the branch",
        security=vulnerable(
            "urllib3",
            "2.0.4",
            ("CVE-2024-37891",),
            Severity.MODERATE,
            "2.0.7",
            UpgradeDistance.PATCH,
            "Proxy-Authorization is not stripped on cross-origin redirect.",
        ),
        license=licensed("urllib3", "2.0.4"),
        maintenance=maintained("urllib3", "2.0.4", evidence="Commits within the last month."),
        expected_branch=TriageBranch.AUTO_UPGRADE,
        must_mention=(("CVE-2024-37891", "advisory"), ("2.0.7",)),
        must_not_mention=("AUTO_UPGRADE",),
    ),
    PackageCase(
        id="major-bump-fix",
        probes="a breaking fix must read as breaking without naming the branch",
        security=vulnerable(
            "paramiko",
            "2.7.2",
            ("CVE-2023-48795",),
            Severity.HIGH,
            "3.4.0",
            UpgradeDistance.MAJOR,
            "The Terrapin attack truncates the SSH extension negotiation.",
        ),
        license=licensed("paramiko", "2.7.2", "LGPL-2.1-only", COMPATIBLE),
        maintenance=maintained("paramiko", "2.7.2", evidence="Active; 200 contributors."),
        expected_branch=TriageBranch.UPGRADE_BREAKING,
        must_mention=(("CVE-2023-48795", "Terrapin"), ("3.4.0",), ("major", "breaking", "3.x")),
        must_not_mention=("UPGRADE_BREAKING",),
    ),
    PackageCase(
        id="abandoned-and-unfixable",
        probes="two findings at once — the rationale must merge them, not pick one",
        security=vulnerable(
            "pycrypto",
            "2.6.1",
            ("CVE-2013-7459",),
            Severity.HIGH,
            None,
            UpgradeDistance.UNKNOWN,
            "Heap buffer overflow in ALGnew; no fixed release exists.",
        ),
        license=licensed("pycrypto", "2.6.1", "LicenseRef-Public-Domain"),
        maintenance=maintained(
            "pycrypto",
            "2.6.1",
            MaintenanceStatus.ABANDONED,
            0.98,
            "Repository archived; last release 2013-10-17.",
        ),
        expected_branch=TriageBranch.REPLACE,
        must_mention=(("archiv", "abandon"), ("no fix", "unfixable", "no fixed", "never fixed")),
        must_not_mention=("REPLACE",),
    ),
    PackageCase(
        id="license-ambiguity",
        probes="an escalated licence question must not be summarised into a recommendation",
        security=secure("chardet", "5.0.0"),
        license=licensed(
            "chardet",
            "5.0.0",
            "LGPL-2.1-only",
            REVIEW_LICENSE,
            "LGPL-2.1 section 6 requires that the user be able to relink the application "
            "against a modified version of the library.",
        ),
        maintenance=maintained("chardet", "5.0.0", evidence="Quiet but not archived."),
        expected_branch=TriageBranch.HUMAN_REVIEW,
        must_mention=(("LGPL",), ("relink", "section 6", "binary", "distribut")),
        must_not_mention=("HUMAN_REVIEW", "you should", "we recommend"),
    ),
    PackageCase(
        id="blocked-license",
        probes="a licence the project cannot use is a replacement, and the clause says why",
        security=secure("pygpl-cli", "1.4.0"),
        license=licensed(
            "pygpl-cli",
            "1.4.0",
            "GPL-3.0-only",
            INCOMPATIBLE,
            "GPL-3.0 section 5 extends copyleft to the whole conveyed work.",
        ),
        maintenance=maintained("pygpl-cli", "1.4.0"),
        expected_branch=TriageBranch.REPLACE,
        must_mention=(("GPL",), ("copyleft", "section 5", "combined", "conveyed")),
    ),
    PackageCase(
        id="clean-package",
        probes="a clean package still gets an honest sentence rather than an invented worry",
        security=secure("attrs", "24.2.0"),
        license=licensed("attrs", "24.2.0"),
        maintenance=maintained("attrs", "24.2.0", evidence="Released six weeks ago."),
        expected_branch=TriageBranch.NO_ACTION,
        must_not_mention=("CVE", "vulnerab", "abandon"),
    ),
    PackageCase(
        id="failed-specialist",
        probes="a specialist that could not report is a gap, not a clean bill of health",
        security=SecurityVerdict(
            package="lxml",
            version="4.9.0",
            max_severity=Severity.UNKNOWN,
            upgrade_distance=UpgradeDistance.UNKNOWN,
            error="HttpError: OSV.dev timed out",
        ),
        license=licensed("lxml", "4.9.0", "BSD-3-Clause"),
        maintenance=maintained("lxml", "4.9.0"),
        expected_branch=TriageBranch.HUMAN_REVIEW,
        must_mention=(("could not", "failed", "unavailable", "timed out", "not assessed", "unknown"),),
        must_not_mention=("HUMAN_REVIEW",),
    ),
    PackageCase(
        id="declining-but-clean",
        probes="decline alone is not an action, and must not be inflated into one",
        security=secure("quiet-lib", "1.0.0"),
        license=licensed("quiet-lib", "1.0.0"),
        maintenance=maintained(
            "quiet-lib",
            "1.0.0",
            MaintenanceStatus.DECLINING,
            0.6,
            "One contributor; last release 620 days ago.",
        ),
        expected_branch=TriageBranch.NO_ACTION,
        must_not_mention=("REPLACE", "you should", "we recommend"),
    ),
)
