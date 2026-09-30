"""Golden cases for the maintenance assessor.

Each case is a package described only by the signals enrichment can actually
measure, plus the status a competent reviewer would reach from those signals
alone. Nothing here depends on the model recognising the package: the names are
deliberately unremarkable, because a model that answers "requests is healthy"
from memory is not doing the job this tool asks for.

`also_accepts` records the second defensible answer where the signals genuinely
support two readings — a small library quiet for two years is either "declining"
or "healthy but finished", and an eval that pretends otherwise measures the
labeller's taste rather than the model's judgment.

`probes` names the failure mode the case exists to catch. Add a case whenever a
model surprises you in production; that is the loop.
"""

from dataclasses import dataclass, field
from datetime import date

from src.domain.models import EnrichedPackage, MaintenanceStatus, RepoHealth

TODAY = date(2026, 9, 1)

HEALTHY = MaintenanceStatus.HEALTHY
DECLINING = MaintenanceStatus.DECLINING
ABANDONED = MaintenanceStatus.ABANDONED
UNKNOWN = MaintenanceStatus.UNKNOWN


@dataclass(frozen=True)
class EvalCase:
    id: str
    probes: str
    package: EnrichedPackage
    expected_status: MaintenanceStatus
    also_accepts: frozenset[MaintenanceStatus] = field(default=frozenset())
    min_confidence: float = 0.0
    max_confidence: float = 1.0
    must_not_appear: tuple[str, ...] = field(default=())

    @property
    def accepted_statuses(self) -> frozenset[MaintenanceStatus]:
        return self.also_accepts | {self.expected_status}


def package(
    name: str,
    *,
    archived: bool | None = False,
    last_commit: date | None = None,
    last_release: date | None = None,
    open_issues: int | None = None,
    contributors: int | None = None,
    releases: int = 5,
    repo: bool = True,
) -> EnrichedPackage:
    return EnrichedPackage(
        name=name,
        version="1.0.0",
        latest_version="1.0.0",
        latest_release_date=last_release,
        available_versions=[f"0.{n}.0" for n in range(releases)],
        repo_health=(
            RepoHealth(
                repo_url="https://github.com/example/" + name,
                archived=archived,
                last_commit=last_commit,
                open_issues=open_issues,
                contributors=contributors,
            )
            if repo
            else None
        ),
    )


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        id="busy-and-broad",
        probes="the easy positive: recent commits, recent release, many hands",
        package=package(
            "alpha-http",
            last_commit=date(2026, 8, 25),
            last_release=date(2026, 7, 14),
            open_issues=31,
            contributors=180,
        ),
        expected_status=HEALTHY,
        min_confidence=0.6,
    ),
    EvalCase(
        id="archived-repository",
        probes="an archived repository is abandoned however reasonable the rest looks",
        package=package(
            "beta-crypto",
            archived=True,
            last_commit=date(2014, 6, 20),
            last_release=date(2013, 10, 17),
            open_issues=194,
            contributors=12,
        ),
        expected_status=ABANDONED,
        min_confidence=0.8,
    ),
    EvalCase(
        id="archived-but-recent",
        probes="archival beats recency: a repo archived last month is still archived",
        package=package(
            "gamma-orm",
            archived=True,
            last_commit=date(2026, 7, 30),
            last_release=date(2026, 7, 30),
            open_issues=4,
            contributors=25,
        ),
        expected_status=ABANDONED,
        min_confidence=0.6,
    ),
    EvalCase(
        id="decade-of-silence",
        probes="the unambiguous negative: no commit and no release for over a decade",
        package=package(
            "delta-xml",
            last_commit=date(2013, 3, 2),
            last_release=date(2013, 4, 9),
            open_issues=88,
            contributors=6,
        ),
        expected_status=ABANDONED,
        min_confidence=0.7,
    ),
    EvalCase(
        id="two-quiet-years-one-maintainer",
        probes="quiet plus a bus factor of one is decline, not merely age",
        package=package(
            "epsilon-yaml",
            last_commit=date(2024, 5, 1),
            last_release=date(2024, 6, 1),
            open_issues=57,
            contributors=1,
        ),
        expected_status=DECLINING,
        also_accepts=frozenset({ABANDONED}),
    ),
    EvalCase(
        id="finished-and-tiny",
        probes="age alone is not decline — a two-person library that solved its problem",
        package=package(
            "zeta-slug",
            last_commit=date(2025, 4, 10),
            last_release=date(2025, 4, 10),
            open_issues=1,
            contributors=3,
            releases=4,
        ),
        expected_status=HEALTHY,
        also_accepts=frozenset({DECLINING}),
    ),
    EvalCase(
        id="commits-but-no-release",
        probes="a live commit log with a stalled release train is decline, not health",
        package=package(
            "eta-client",
            last_commit=date(2026, 8, 1),
            last_release=date(2022, 2, 2),
            open_issues=140,
            contributors=9,
        ),
        expected_status=DECLINING,
        also_accepts=frozenset({HEALTHY}),
    ),
    EvalCase(
        id="releases-but-no-commits",
        probes="the mirror case: releases still shipping while the repo signal is stale",
        package=package(
            "theta-driver",
            last_commit=date(2021, 1, 1),
            last_release=date(2026, 6, 1),
            open_issues=12,
            contributors=15,
        ),
        expected_status=DECLINING,
        also_accepts=frozenset({HEALTHY}),
    ),
    EvalCase(
        id="stalled-release-train",
        probes="commits without releases, one maintainer and a wall of issues is decline",
        package=package(
            "nu-queue",
            last_commit=date(2026, 8, 2),
            last_release=date(2023, 1, 10),
            open_issues=312,
            contributors=1,
        ),
        expected_status=DECLINING,
    ),
    EvalCase(
        id="issue-backlog-under-activity",
        probes="a large backlog under real activity is not abandonment",
        package=package(
            "iota-web",
            last_commit=date(2026, 8, 28),
            last_release=date(2026, 8, 1),
            open_issues=1400,
            contributors=430,
        ),
        expected_status=HEALTHY,
        also_accepts=frozenset({DECLINING}),
    ),
    EvalCase(
        id="single-maintainer-but-active",
        probes="a bus factor of one is a risk, not a verdict, while the work continues",
        package=package(
            "kappa-parser",
            last_commit=date(2026, 8, 20),
            last_release=date(2026, 8, 20),
            open_issues=3,
            contributors=1,
        ),
        expected_status=HEALTHY,
        also_accepts=frozenset({DECLINING}),
    ),
    EvalCase(
        id="no-repository-signals",
        probes="a package with only a release date must not be judged on invented signals",
        package=package("lambda-utils", last_release=date(2026, 5, 1), repo=False),
        expected_status=HEALTHY,
        also_accepts=frozenset({DECLINING, UNKNOWN}),
        max_confidence=0.7,
        must_not_appear=("archived", "contributor", "commit"),
    ),
    EvalCase(
        id="partial-signals-only",
        probes="missing signals lower confidence rather than becoming reassuring zeros",
        package=package(
            "mu-serial", last_commit=None, last_release=date(2026, 6, 15), contributors=None
        ),
        expected_status=HEALTHY,
        also_accepts=frozenset({DECLINING, UNKNOWN}),
        max_confidence=0.8,
        must_not_appear=("no contributors", "zero contributors"),
    ),
)
