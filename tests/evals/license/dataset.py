"""Golden cases for the license assessor.

Each case is a hand-labelled dependency, a project license and a distribution model,
plus the verdict a competent compliance reviewer would reach from that input alone.
Every label is derivable from the compatibility matrix the agent is handed and the
obligations the licenses themselves impose — no case depends on knowledge the model
was not given, so a failure is always the model's, never the dataset's.

Where the matrix is deliberately silent, `also_accepts` records the second defensible
answer, because "escalate this to a lawyer" and "this conflicts" are both honest
readings of an unruled license and the eval should not pretend otherwise.

`probes` names the specific failure mode the case is designed to catch. Add a case
whenever a model surprises you in production; that is the loop.
"""

from dataclasses import dataclass, field

from src.domain.models import Distribution, EnrichedPackage, LicenseCompatibility

COMPATIBLE = LicenseCompatibility.COMPATIBLE
INCOMPATIBLE = LicenseCompatibility.INCOMPATIBLE
REVIEW = LicenseCompatibility.REQUIRES_HUMAN_REVIEW


@dataclass(frozen=True)
class EvalCase:
    id: str
    probes: str
    package: EnrichedPackage
    project_license: str | None
    distribution: Distribution
    expected_license: str | None
    expected_verdict: LicenseCompatibility
    also_accepts: frozenset[LicenseCompatibility] = field(default=frozenset())
    expects_alternatives: bool = False
    must_not_appear: tuple[str, ...] = field(default=())

    @property
    def accepted_verdicts(self) -> frozenset[LicenseCompatibility]:
        return self.also_accepts | {self.expected_verdict}


def package(name: str, version: str, license_declared: str | None) -> EnrichedPackage:
    return EnrichedPackage(name=name, version=version, license_declared=license_declared)


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        id="same-license-as-the-project",
        probes="a dependency under the project's own license costs no LLM call at all",
        package=package("attrs-mit", "24.2.0", "MIT License"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="MIT",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="permissive-in-permissive",
        probes="a notice-only license is cleared without inventing an obligation",
        package=package("requests-apache", "2.32.3", "Apache Software License"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="Apache-2.0",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="strong-copyleft-shipped",
        probes="the headline conflict: GPL code conveyed inside a permissively licensed product",
        package=package("pygpl-cli", "1.4.0", "GPL-3.0"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="GPL-3.0-only",
        expected_verdict=INCOMPATIBLE,
    ),
    EvalCase(
        id="strong-copyleft-hosted",
        probes="the GPL trigger is conveying, so a hosted service does not fire it",
        package=package("hosted-gpl", "2.1.0", "GPL-3.0"),
        project_license="MIT",
        distribution=Distribution.SAAS,
        expected_license="GPL-3.0-only",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="agpl-hosted",
        probes="saas is not a blanket safe harbour: AGPL section 13 reaches network use",
        package=package("netsvc-agpl", "0.8.0", "GNU Affero General Public License v3"),
        project_license="MIT",
        distribution=Distribution.SAAS,
        expected_license="AGPL-3.0-only",
        expected_verdict=INCOMPATIBLE,
    ),
    EvalCase(
        id="weak-copyleft-shipped",
        probes="the LGPL relinking obligation is escalated, not resolved by the model",
        package=package("chardet", "5.0.0", "LGPL-2.1"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="LGPL-2.1-only",
        expected_verdict=REVIEW,
        expects_alternatives=True,
    ),
    EvalCase(
        id="weak-copyleft-source",
        probes="the same license clears once the distribution model discharges the obligation",
        package=package("readline-lgpl", "3.2.0", "LGPL-2.1"),
        project_license="MIT",
        distribution=Distribution.SOURCE,
        expected_license="LGPL-2.1-only",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="file-level-copyleft",
        probes="file-level copyleft is not inflated into a whole-work obligation",
        package=package("certifi-mpl", "2024.7.4", "Mozilla Public License 2.0 (MPL 2.0)"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="MPL-2.0",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="gpl2-only-into-gpl3",
        probes="two copyleft licenses can conflict with each other, not just with permissive ones",
        package=package("oldkernel-gpl2", "1.0.0", "GNU General Public License v2 (GPLv2)"),
        project_license="GPL-3.0-only",
        distribution=Distribution.BINARY,
        expected_license="GPL-2.0-only",
        expected_verdict=INCOMPATIBLE,
    ),
    EvalCase(
        id="copyleft-into-same-copyleft",
        probes="the project license is actually read, not assumed permissive",
        package=package("sibling-gpl3", "4.0.0", "GPL-3.0"),
        project_license="GPL-3.0-or-later",
        distribution=Distribution.BINARY,
        expected_license="GPL-3.0-only",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="patent-clause-into-gpl2",
        probes="a row naming the project license overrides the permissive wildcard row",
        package=package("apache-crypto", "1.2.0", "Apache-2.0"),
        project_license="GPL-2.0-only",
        distribution=Distribution.BINARY,
        expected_license="Apache-2.0",
        expected_verdict=INCOMPATIBLE,
    ),
    EvalCase(
        id="no-license-declared",
        probes="an undeclared license is escalated, never read as permissive by default",
        package=package("mystery-lib", "0.3.1", None),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license=None,
        expected_verdict=REVIEW,
    ),
    EvalCase(
        id="license-family-not-a-license",
        probes='"BSD License" names a family; picking a variant would be a legal guess',
        package=package("legacy-bsd", "1.1.0", "BSD License"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license=None,
        expected_verdict=REVIEW,
    ),
    EvalCase(
        id="dual-licensed-choice",
        probes="OR is a choice, so the project may take the branch that suits it",
        package=package("dual-mit-gpl", "2.0.0", "MIT OR GPL-3.0"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="MIT OR GPL-3.0-only",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="conjunctive-license",
        probes="AND is an obligation, so the strictest part governs the whole",
        package=package("bundled-mit-gpl", "1.0.0", "MIT AND GPL-3.0"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="MIT AND GPL-3.0-only",
        expected_verdict=INCOMPATIBLE,
    ),
    EvalCase(
        id="public-domain-dedication",
        probes="an informal dedication imposes no obligation and is not treated as a conflict",
        package=package("pycrypto-pd", "2.6.1", "Public Domain"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="LicenseRef-Public-Domain",
        expected_verdict=COMPATIBLE,
    ),
    EvalCase(
        id="source-available-is-not-open-source",
        probes="a public repository under BUSL is not an open-source grant",
        package=package("busl-db", "3.0.0", "Business Source License 1.1"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="BUSL-1.1",
        expected_verdict=INCOMPATIBLE,
    ),
    EvalCase(
        id="service-source-obligation",
        probes="SSPL bites hardest exactly where the GPL does not: hosting",
        package=package("mongo-sspl", "1.0.0", "SSPL-1.0"),
        project_license="MIT",
        distribution=Distribution.SAAS,
        expected_license="SSPL-1.0",
        expected_verdict=INCOMPATIBLE,
    ),
    EvalCase(
        id="proprietary-grant",
        probes="a proprietary grant cannot be assessed from an identifier and must be read",
        package=package("vendor-eula", "5.5.0", "Other/Proprietary License"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="LicenseRef-Proprietary",
        expected_verdict=REVIEW,
    ),
    EvalCase(
        id="license-outside-the-matrix",
        probes="where the matrix is silent the model reasons, and prefers review to confidence",
        package=package("berkeley-store", "6.0.0", "Sleepycat"),
        project_license="MIT",
        distribution=Distribution.BINARY,
        expected_license="Sleepycat",
        expected_verdict=REVIEW,
        also_accepts=frozenset({INCOMPATIBLE}),
    ),
    EvalCase(
        id="package-name-is-not-a-license",
        probes="the license is judged, not the package name that suggests another one",
        package=package("gpl-utils", "1.0.0", "MIT"),
        project_license="Apache-2.0",
        distribution=Distribution.BINARY,
        expected_license="MIT",
        expected_verdict=COMPATIBLE,
        must_not_appear=("GPL",),
    ),
)
