"""Golden cases for the security assessor.

Each case is a hand-labelled EnrichedPackage plus the verdict a competent analyst
would reach from that input alone. The labels are derivable from the advisory
text in the case itself — no case depends on knowledge the model was not given,
so a failure is always the model's, never the dataset's.

`probes` names the specific failure mode the case is designed to catch. Add a
case whenever a model surprises you in production; that is the loop.
"""

from dataclasses import dataclass, field

from src.domain.models import EnrichedPackage, Severity, UpgradeDistance, Vulnerability


@dataclass(frozen=True)
class EvalCase:
    id: str
    probes: str
    package: EnrichedPackage
    expected_cves: frozenset[str]
    expected_severity: Severity
    expected_fixed_in: str | None
    expected_distance: UpgradeDistance
    must_not_appear: tuple[str, ...] = field(default=())


def vulnerability(
    id: str,
    aliases: tuple[str, ...] = (),
    severity: str | None = None,
    fixed_in: str | None = None,
    summary: str | None = None,
    details: str | None = None,
) -> Vulnerability:
    return Vulnerability(
        id=id,
        aliases=list(aliases),
        severity=severity,
        fixed_in=fixed_in,
        summary=summary,
        details=details,
    )


def package(name: str, version: str, *vulns: Vulnerability, latest: str | None = None) -> EnrichedPackage:
    return EnrichedPackage(
        name=name, version=version, vulnerabilities=list(vulns), latest_version=latest
    )


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        id="single-high-major-fix",
        probes="reads one advisory straight: severity, fix version, major distance",
        package=package(
            "paramiko",
            "2.7.2",
            vulnerability(
                "GHSA-45x7-px36-x8w8",
                aliases=("CVE-2023-48795",),
                severity="HIGH",
                fixed_in="3.4.0",
                summary="Terrapin attack allows a MITM to truncate the SSH extension negotiation.",
                details="All versions below 3.4.0 are affected. The prefix truncation attack "
                "lets an active network attacker remove messages from the handshake.",
            ),
            latest="3.4.0",
        ),
        expected_cves=frozenset({"GHSA-45x7-px36-x8w8", "CVE-2023-48795"}),
        expected_severity=Severity.HIGH,
        expected_fixed_in="3.4.0",
        expected_distance=UpgradeDistance.MAJOR,
    ),
    EvalCase(
        id="patch-distance",
        probes="a patch-level fix is not inflated into a minor or major bump",
        package=package(
            "urllib3",
            "2.0.4",
            vulnerability(
                "GHSA-34jh-p97f-mpxf",
                aliases=("CVE-2024-37891",),
                severity="MODERATE",
                fixed_in="2.0.7",
                summary="Proxy-Authorization header is not stripped on cross-origin redirects.",
            ),
            latest="2.2.3",
        ),
        expected_cves=frozenset({"GHSA-34jh-p97f-mpxf", "CVE-2024-37891"}),
        expected_severity=Severity.MODERATE,
        expected_fixed_in="2.0.7",
        expected_distance=UpgradeDistance.PATCH,
    ),
    EvalCase(
        id="no-fix-available",
        probes="an unfixable advisory yields a null fix, not an invented version",
        package=package(
            "pycrypto",
            "2.6.1",
            vulnerability(
                "CVE-2013-7459",
                severity="HIGH",
                fixed_in=None,
                summary="Heap-based buffer overflow in the ALGnew function.",
                details="pycrypto is unmaintained; no fixed release exists.",
            ),
            latest="2.6.1",
        ),
        expected_cves=frozenset({"CVE-2013-7459"}),
        expected_severity=Severity.HIGH,
        expected_fixed_in=None,
        expected_distance=UpgradeDistance.UNKNOWN,
    ),
    EvalCase(
        id="highest-severity-wins",
        probes="max_severity is the maximum across advisories, not the first or last",
        package=package(
            "example-multi",
            "1.2.0",
            vulnerability("GHSA-aaaa", severity="LOW", fixed_in="1.2.1", summary="Log injection."),
            vulnerability("GHSA-bbbb", severity="CRITICAL", fixed_in="1.3.0", summary="Remote code execution via pickle."),
            vulnerability("GHSA-cccc", severity="MODERATE", fixed_in="1.2.5", summary="Timing side channel."),
            latest="1.3.0",
        ),
        expected_cves=frozenset({"GHSA-aaaa", "GHSA-bbbb", "GHSA-cccc"}),
        expected_severity=Severity.CRITICAL,
        expected_fixed_in="1.3.0",
        expected_distance=UpgradeDistance.MINOR,
    ),
    EvalCase(
        id="partial-fix-is-no-fix",
        probes="one unfixable advisory means the package as a whole has no fix",
        package=package(
            "example-partial",
            "3.1.0",
            vulnerability("GHSA-dddd", severity="MODERATE", fixed_in="3.1.4", summary="Path traversal in the archive extractor."),
            vulnerability("GHSA-eeee", severity="HIGH", fixed_in=None, summary="Design flaw in the token format; no fix planned."),
            latest="3.2.0",
        ),
        expected_cves=frozenset({"GHSA-dddd", "GHSA-eeee"}),
        expected_severity=Severity.HIGH,
        expected_fixed_in=None,
        expected_distance=UpgradeDistance.UNKNOWN,
    ),
    EvalCase(
        id="undeclared-severity",
        probes="an advisory with no declared severity is not upgraded to CRITICAL from memory",
        package=package(
            "example-quiet",
            "0.9.0",
            vulnerability(
                "PYSEC-2021-1234",
                severity=None,
                fixed_in="0.9.1",
                summary="Incorrect permission check on the admin endpoint.",
            ),
            latest="1.0.0",
        ),
        expected_cves=frozenset({"PYSEC-2021-1234"}),
        expected_severity=Severity.UNKNOWN,
        expected_fixed_in="0.9.1",
        expected_distance=UpgradeDistance.PATCH,
    ),
    EvalCase(
        id="prose-mentions-unrelated-cve",
        probes="an identifier mentioned inside advisory prose is not reported as applicable",
        package=package(
            "example-lure",
            "4.0.0",
            vulnerability(
                "GHSA-ffff",
                severity="MODERATE",
                fixed_in="4.0.2",
                summary="Improper input validation in the parser.",
                details="This issue is unrelated to CVE-2019-11111, which affected a different "
                "library and is mentioned here only for contrast.",
            ),
            latest="4.1.0",
        ),
        expected_cves=frozenset({"GHSA-ffff"}),
        expected_severity=Severity.MODERATE,
        expected_fixed_in="4.0.2",
        expected_distance=UpgradeDistance.PATCH,
        must_not_appear=("CVE-2019-11111",),
    ),
    EvalCase(
        id="already-past-the-fix",
        probes="a fix at or below the pinned version means no upgrade is needed",
        package=package(
            "example-ahead",
            "5.2.0",
            vulnerability("GHSA-gggg", severity="LOW", fixed_in="5.1.0", summary="Verbose error message leaks a path."),
            latest="5.3.0",
        ),
        expected_cves=frozenset({"GHSA-gggg"}),
        expected_severity=Severity.LOW,
        expected_fixed_in="5.1.0",
        expected_distance=UpgradeDistance.NONE,
    ),
    EvalCase(
        id="no-advisories",
        probes="a clean package short-circuits without an LLM call at all",
        package=package("requests", "2.32.3", latest="2.32.3"),
        expected_cves=frozenset(),
        expected_severity=Severity.NONE,
        expected_fixed_in=None,
        expected_distance=UpgradeDistance.NONE,
    ),
)
