from src.domain.models import (
    DecisionAction,
    EpisodicEntry,
    LicenseCompatibility,
    LicenseVerdict,
    MaintenanceStatus,
    MaintenanceVerdict,
    SecurityVerdict,
    Severity,
    TriageBranch,
    UpgradeDistance,
)
from src.memory.procedural import derive_rules
from src.memory.store import InMemoryStore, project_key
from src.tools.triage_dependencies import triage_dependencies_tool
from tests.support import triage_draft, triage_model

PROJECT = "/tmp/myapp"

AUTO = TriageBranch.AUTO_UPGRADE
BREAKING = TriageBranch.UPGRADE_BREAKING
REPLACE = TriageBranch.REPLACE
REVIEW = TriageBranch.HUMAN_REVIEW
NOTHING = TriageBranch.NO_ACTION


def security(
    package="urllib3",
    cves=("CVE-2024-37891",),
    severity=Severity.MODERATE,
    fixed_in="2.0.7",
    distance=UpgradeDistance.PATCH,
    error=None,
) -> SecurityVerdict:
    return SecurityVerdict(
        package=package,
        version="2.0.4",
        applicable_cves=list(cves),
        max_severity=severity,
        fixed_in=fixed_in,
        upgrade_distance=distance,
        error=error,
    )


def clean_security(package="urllib3") -> SecurityVerdict:
    return SecurityVerdict(
        package=package,
        version="2.0.4",
        max_severity=Severity.NONE,
        upgrade_distance=UpgradeDistance.NONE,
    )


def license(
    package="urllib3", verdict=LicenseCompatibility.COMPATIBLE, error=None
) -> LicenseVerdict:
    return LicenseVerdict(
        package=package, version="2.0.4", license="MIT", verdict=verdict, error=error
    )


def maintenance(
    package="urllib3", status=MaintenanceStatus.HEALTHY, error=None
) -> MaintenanceVerdict:
    return MaintenanceVerdict(
        package=package, version="2.0.4", status=status, confidence=0.9, error=error
    )


async def triage(security_verdicts, licenses, maintenances, script=None, **kwargs):
    plan = await triage_dependencies_tool(
        security_verdicts,
        licenses,
        maintenances,
        model=triage_model(script if script is not None else [triage_draft()]),
        **kwargs,
    )
    return plan


async def route_one(security_verdict, license_verdict, maintenance_verdict, **kwargs):
    plan = await triage([security_verdict], [license_verdict], [maintenance_verdict], **kwargs)
    return plan.entries[0]


# --- routing -----------------------------------------------------------------


async def test_a_patch_level_fix_is_an_automatic_upgrade():
    entry = await route_one(security(), license(), maintenance())

    assert entry.branch == AUTO
    assert entry.target == "2.0.7"


async def test_a_minor_level_fix_is_an_automatic_upgrade():
    entry = await route_one(
        security(distance=UpgradeDistance.MINOR), license(), maintenance()
    )

    assert entry.branch == AUTO


async def test_a_fix_across_a_major_boundary_is_a_breaking_upgrade():
    entry = await route_one(
        security(fixed_in="3.4.0", distance=UpgradeDistance.MAJOR), license(), maintenance()
    )

    assert entry.branch == BREAKING
    assert entry.target == "3.4.0"


async def test_a_vulnerability_with_no_released_fix_needs_replacing():
    entry = await route_one(
        security(fixed_in=None, distance=UpgradeDistance.UNKNOWN), license(), maintenance()
    )

    assert entry.branch == REPLACE
    assert entry.target is None


async def test_an_abandoned_package_needs_replacing_even_when_it_is_clean():
    entry = await route_one(
        clean_security(), license(), maintenance(status=MaintenanceStatus.ABANDONED)
    )

    assert entry.branch == REPLACE


async def test_a_license_the_project_cannot_use_needs_replacing():
    entry = await route_one(
        clean_security(), license(verdict=LicenseCompatibility.INCOMPATIBLE), maintenance()
    )

    assert entry.branch == REPLACE


async def test_license_ambiguity_goes_to_a_person():
    entry = await route_one(
        security(), license(verdict=LicenseCompatibility.REQUIRES_HUMAN_REVIEW), maintenance()
    )

    assert entry.branch == REVIEW


async def test_a_person_decides_before_a_replacement_is_searched_for():
    """Ambiguous licence plus abandonment is a decision, not an automatic replacement."""
    entry = await route_one(
        clean_security(),
        license(verdict=LicenseCompatibility.REQUIRES_HUMAN_REVIEW),
        maintenance(status=MaintenanceStatus.ABANDONED),
    )

    assert entry.branch == REVIEW


async def test_a_specialist_that_failed_sends_the_package_to_a_person():
    entry = await route_one(security(error="HttpError: OSV unreachable"), license(), maintenance())

    assert entry.branch == REVIEW


async def test_a_clean_package_needs_nothing():
    entry = await route_one(clean_security(), license(), maintenance())

    assert entry.branch == NOTHING


async def test_a_declining_package_with_no_other_finding_needs_nothing_yet():
    entry = await route_one(
        clean_security(), license(), maintenance(status=MaintenanceStatus.DECLINING)
    )

    assert entry.branch == NOTHING
    assert entry.maintenance_status == MaintenanceStatus.DECLINING


async def test_carries_the_specialist_findings_onto_the_entry():
    entry = await route_one(security(severity=Severity.HIGH), license(), maintenance())

    assert entry.max_severity == Severity.HIGH
    assert entry.license_verdict == LicenseCompatibility.COMPATIBLE
    assert entry.maintenance_status == MaintenanceStatus.HEALTHY


async def test_routes_a_package_only_one_specialist_saw():
    plan = await triage([security()], [], [])

    assert [e.package for e in plan.entries] == ["urllib3"]


async def test_produces_no_plan_for_no_verdicts():
    plan = await triage([], [], [])

    assert plan.entries == []


# --- the manager's contribution ---------------------------------------------


async def test_takes_the_rationale_the_manager_wrote():
    entry = await route_one(
        security(),
        license(),
        maintenance(),
        script=[triage_draft(("urllib3", "AUTO_UPGRADE", "Medium CVE fixed in a patch release."))],
    )

    assert entry.rationale == "Medium CVE fixed in a patch release."


async def test_lets_the_manager_escalate_to_human_review():
    entry = await route_one(
        security(),
        license(),
        maintenance(),
        script=[triage_draft(("urllib3", "HUMAN_REVIEW", "The specialists disagree about the fix."))],
    )

    assert entry.branch == REVIEW
    assert entry.target is None
    assert entry.corrections == []


async def test_never_lets_the_manager_relax_a_branch():
    entry = await route_one(
        clean_security(),
        license(),
        maintenance(status=MaintenanceStatus.ABANDONED),
        script=[triage_draft(("urllib3", "NO_ACTION", "Looks fine to me."))],
    )

    assert entry.branch == REPLACE
    assert "may only escalate" in entry.corrections[0]


async def test_never_lets_the_manager_invent_a_more_aggressive_branch():
    plan = await triage(
        [security("paramiko"), clean_security("urllib3")],
        [license("paramiko"), license("urllib3")],
        [maintenance("paramiko"), maintenance("urllib3")],
        script=[
            triage_draft(
                ("paramiko", "AUTO_UPGRADE", "Patch fix."),
                ("urllib3", "REPLACE", "I do not like this package."),
            )
        ],
    )

    urllib3 = next(e for e in plan.entries if e.package == "urllib3")
    assert urllib3.branch == NOTHING
    assert "may only escalate" in urllib3.corrections[0]


async def test_records_a_package_the_manager_forgot():
    entry = await route_one(security(), license(), maintenance(), script=[triage_draft()])

    assert entry.corrections == ["the manager wrote no entry for this package"]


async def test_records_entries_for_packages_that_were_never_routed():
    plan = await triage(
        [security()],
        [license()],
        [maintenance()],
        script=[triage_draft(("urllib3", "AUTO_UPGRADE", "ok"), ("ghost", "REPLACE", "?"))],
    )

    assert "ghost" in plan.entries[0].corrections[0]


async def test_skips_the_manager_when_every_package_is_clean():
    model = triage_model([triage_draft()])

    plan = await triage_dependencies_tool(
        [clean_security()], [license()], [maintenance()], model=model
    )

    assert model.prompts == []
    assert plan.entries[0].rationale.startswith("No applicable advisories")


async def test_keeps_the_routed_plan_when_the_manager_fails():
    plan = await triage([security()], [license()], [maintenance()], script=[RuntimeError("boom")])

    assert plan.entries[0].branch == AUTO
    assert plan.error == "RuntimeError: boom"


async def test_shows_the_manager_all_three_verdicts():
    model = triage_model([triage_draft()])

    await triage_dependencies_tool(
        [security(severity=Severity.HIGH)],
        [license(verdict=LicenseCompatibility.INCOMPATIBLE)],
        [maintenance(status=MaintenanceStatus.DECLINING)],
        model=model,
    )

    prompt = model.prompts[-1]
    assert "HIGH" in prompt and "incompatible" in prompt and "declining" in prompt


# --- ranking and learned preferences ----------------------------------------


async def test_ranks_actionable_branches_before_the_rest():
    plan = await triage(
        [clean_security("clean"), security("urllib3")],
        [license("clean"), license("urllib3")],
        [maintenance("clean"), maintenance("urllib3")],
    )

    assert [e.package for e in plan.entries] == ["urllib3", "clean"]


async def test_ranks_the_worst_severity_first_within_a_branch():
    plan = await triage(
        [security("low-one", severity=Severity.LOW), security("bad-one", severity=Severity.CRITICAL)],
        [license("low-one"), license("bad-one")],
        [maintenance("low-one"), maintenance("bad-one")],
    )

    assert [e.package for e in plan.entries] == ["bad-one", "low-one"]


def store_with(episodes) -> InMemoryStore:
    store = InMemoryStore()
    store.put_rules(project_key(PROJECT), derive_rules(episodes))
    return store


def rejections(branch, *packages):
    return [
        EpisodicEntry(
            recorded_at="2026-07-12T00:00:00+00:00",
            package=package,
            action=DecisionAction.REJECTED,
            branch=branch,
        )
        for package in packages
    ]


async def test_annotates_a_branch_the_user_keeps_rejecting():
    entry = await route_one(
        security(fixed_in="3.4.0", distance=UpgradeDistance.MAJOR),
        license(),
        maintenance(),
        project=PROJECT,
        store=store_with(rejections(BREAKING, "a", "b", "c")),
    )

    assert entry.annotations[0].startswith("likely_to_be_rejected")
    assert "avoid_major_bumps" in entry.annotations[0]


async def test_sorts_entries_the_user_habitually_rejects_last():
    plan = await triage(
        [security("paramiko", fixed_in="3.4.0", distance=UpgradeDistance.MAJOR), security("urllib3")],
        [license("paramiko"), license("urllib3")],
        [maintenance("paramiko"), maintenance("urllib3")],
        project=PROJECT,
        store=store_with(rejections(BREAKING, "a", "b", "c")),
    )

    assert [e.package for e in plan.entries] == ["urllib3", "paramiko"]


async def test_a_preference_never_changes_the_branch_itself():
    entry = await route_one(
        security(fixed_in="3.4.0", distance=UpgradeDistance.MAJOR),
        license(),
        maintenance(),
        project=PROJECT,
        store=store_with(rejections(BREAKING, "a", "b", "c")),
    )

    assert entry.branch == BREAKING


async def test_annotates_a_branch_the_user_habitually_approves():
    approvals = [
        EpisodicEntry(
            recorded_at="2026-07-12T00:00:00+00:00",
            package=package,
            action=DecisionAction.APPROVED,
            branch=AUTO,
        )
        for package in ("a", "b", "c")
    ]

    entry = await route_one(
        security(), license(), maintenance(), project=PROJECT, store=store_with(approvals)
    )

    assert entry.annotations[0].startswith("likely_to_be_approved")


def clearances(package, *days):
    return [
        EpisodicEntry(
            recorded_at=f"2026-07-{day}T00:00:00+00:00",
            package=package,
            action=DecisionAction.RESOLVED_HUMAN_REVIEW,
            branch=REVIEW,
        )
        for day in days
    ]


async def test_a_recorded_clearance_stops_re_flagging_the_same_package():
    entry = await route_one(
        clean_security("chardet"),
        license("chardet", verdict=LicenseCompatibility.REQUIRES_HUMAN_REVIEW),
        maintenance("chardet"),
        project=PROJECT,
        store=store_with(clearances("chardet", "12", "20")),
    )

    assert entry.branch == NOTHING


async def test_a_suppressed_flag_says_so_loudly():
    entry = await route_one(
        clean_security("chardet"),
        license("chardet", verdict=LicenseCompatibility.REQUIRES_HUMAN_REVIEW),
        maintenance("chardet"),
        project=PROJECT,
        store=store_with(clearances("chardet", "12", "20")),
    )

    assert entry.annotations[0].startswith("license_cleared")


async def test_a_clearance_for_one_package_does_not_clear_another():
    entry = await route_one(
        clean_security("lxml"),
        license("lxml", verdict=LicenseCompatibility.REQUIRES_HUMAN_REVIEW),
        maintenance("lxml"),
        project=PROJECT,
        store=store_with(clearances("chardet", "12", "20")),
    )

    assert entry.branch == REVIEW


async def test_ignores_memory_from_another_project():
    entry = await route_one(
        security(fixed_in="3.4.0", distance=UpgradeDistance.MAJOR),
        license(),
        maintenance(),
        project="/tmp/other",
        store=store_with(rejections(BREAKING, "a", "b", "c")),
    )

    assert entry.annotations == []
