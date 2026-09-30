from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Package(BaseModel):
    name: str
    version: str


class Dependency(Package):
    required_by: list[str]


class Manifest(BaseModel):
    project_license: str | None = None
    manifests_found: list[str] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Vulnerability(BaseModel):
    id: str
    aliases: list[str] = Field(default_factory=list)
    severity: str | None = None
    cvss_vector: str | None = None
    fixed_in: str | None = None
    summary: str | None = None
    details: str | None = None


class RepoHealth(BaseModel):
    repo_url: str | None = None
    archived: bool | None = None
    last_commit: date | None = None
    open_issues: int | None = None
    contributors: int | None = None


class EnrichedPackage(Package):
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    available_versions: list[str] = Field(default_factory=list)
    latest_version: str | None = None
    latest_release_date: date | None = None
    license_declared: str | None = None
    repo_health: RepoHealth | None = None
    errors: list[str] = Field(default_factory=list)


class FetchStats(BaseModel):
    requests: int = 0
    wall_clock_seconds: float = 0.0
    cache_hits: int = 0


class EnrichmentResult(BaseModel):
    enriched: list[EnrichedPackage] = Field(default_factory=list)
    fetch_stats: FetchStats = Field(default_factory=FetchStats)


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class UpgradeDistance(StrEnum):
    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    UNKNOWN = "unknown"


class SecurityVerdictDraft(BaseModel):
    """The judgment we ask the LLM for — and nothing that can be computed instead."""

    applicable_cves: list[str] = Field(
        default_factory=list,
        description="Identifiers of the advisories that actually affect the pinned version. "
        "Use only identifiers present in the supplied advisories; never invent one.",
    )
    max_severity: Severity = Field(
        default=Severity.UNKNOWN,
        description="Highest contextual severity across the applicable advisories.",
    )
    fixed_in: str | None = Field(
        default=None,
        description="Lowest released version that fixes every applicable advisory, "
        "or null when no fix exists.",
    )
    evidence: str = Field(
        default="",
        description="Short justification quoting the supplied advisory text. "
        "Cite only what the advisories say.",
    )


class SecurityVerdict(BaseModel):
    package: str
    version: str
    applicable_cves: list[str] = Field(default_factory=list)
    max_severity: Severity = Severity.UNKNOWN
    fixed_in: str | None = None
    upgrade_distance: UpgradeDistance = UpgradeDistance.UNKNOWN
    evidence: str = ""
    corrections: list[str] = Field(default_factory=list)
    error: str | None = None


class SecurityAssessment(BaseModel):
    verdicts: list[SecurityVerdict] = Field(default_factory=list)


class Distribution(StrEnum):
    """How the project is shipped, which decides whether a copyleft trigger fires."""

    BINARY = "binary"
    SOURCE = "source"
    SAAS = "saas"


class LicenseCompatibility(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class LicenseVerdictDraft(BaseModel):
    """The judgment we ask the LLM for — and nothing that can be resolved instead."""

    verdict: LicenseCompatibility = Field(
        default=LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        description="Whether the dependency's license is compatible with the project's "
        "license under the given distribution model. Genuine legal ambiguity is "
        "'requires_human_review'; never resolve it yourself.",
    )
    conflicting_clause: str = Field(
        default="",
        description="The specific obligation that drives a non-compatible verdict, quoted "
        "or closely paraphrased and attributed to the license it comes from. "
        "Empty when the verdict is 'compatible'.",
    )
    suggested_alternatives: list[str] = Field(
        default_factory=list,
        description="Packages serving the same purpose under a license that would be "
        "compatible, each with its license in parentheses. Empty when the verdict "
        "is 'compatible' or when no alternative is known.",
    )


class LicenseVerdict(BaseModel):
    package: str
    version: str
    license: str | None = None
    license_declared: str | None = None
    verdict: LicenseCompatibility = LicenseCompatibility.REQUIRES_HUMAN_REVIEW
    conflicting_clause: str = ""
    suggested_alternatives: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    error: str | None = None


class LicenseAssessment(BaseModel):
    project_license: str | None = None
    distribution: Distribution = Distribution.BINARY
    verdicts: list[LicenseVerdict] = Field(default_factory=list)

# --- maintenance ------------------------------------------------------------


class MaintenanceStatus(StrEnum):
    HEALTHY = "healthy"
    DECLINING = "declining"
    ABANDONED = "abandoned"
    UNKNOWN = "unknown"


class MaintenanceSignals(BaseModel):
    """The arithmetic behind a maintenance judgment, computed rather than asked for."""

    archived: bool | None = None
    days_since_last_commit: int | None = None
    days_since_last_release: int | None = None
    open_issues: int | None = None
    contributors: int | None = None
    releases_published: int | None = None

    @property
    def is_empty(self) -> bool:
        return all(getattr(self, field) is None for field in self.__class__.model_fields)


class MaintenanceVerdictDraft(BaseModel):
    """The judgment we ask the LLM for — and nothing that can be computed instead."""

    status: MaintenanceStatus = Field(
        default=MaintenanceStatus.UNKNOWN,
        description="How maintained the package is, judged only from the supplied signals.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="How strongly the supplied signals support the status, from 0 to 1. "
        "Few or contradictory signals mean low confidence.",
    )
    evidence: str = Field(
        default="",
        description="Short justification naming the specific signals that drove the status. "
        "Cite only the numbers you were given.",
    )


class MaintenanceVerdict(BaseModel):
    package: str
    version: str
    status: MaintenanceStatus = MaintenanceStatus.UNKNOWN
    confidence: float = 0.0
    evidence: str = ""
    signals: MaintenanceSignals = Field(default_factory=MaintenanceSignals)
    corrections: list[str] = Field(default_factory=list)
    error: str | None = None


class MaintenanceAssessment(BaseModel):
    verdicts: list[MaintenanceVerdict] = Field(default_factory=list)


# --- triage -----------------------------------------------------------------


class TriageBranch(StrEnum):
    AUTO_UPGRADE = "AUTO_UPGRADE"
    UPGRADE_BREAKING = "UPGRADE_BREAKING"
    REPLACE = "REPLACE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    NO_ACTION = "NO_ACTION"


class TriageEntryDraft(BaseModel):
    """What the manager agent adds to a routed entry: prose, and at most an escalation."""

    package: str = Field(description="The package this entry is about, spelled exactly as given.")
    branch: TriageBranch = Field(
        description="The action branch. Keep the routed branch unless the specialists "
        "conflict in a way that needs a person, in which case use HUMAN_REVIEW."
    )
    rationale: str = Field(
        default="",
        description="One or two sentences merging the three specialist verdicts into the "
        "reason this package is on this branch. Name the findings, not the branch.",
    )


class TriagePlanDraft(BaseModel):
    entries: list[TriageEntryDraft] = Field(default_factory=list)


class TriageEntry(BaseModel):
    package: str
    version: str
    branch: TriageBranch = TriageBranch.NO_ACTION
    target: str | None = None
    rationale: str = ""
    annotations: list[str] = Field(default_factory=list)
    max_severity: Severity = Severity.UNKNOWN
    license_verdict: LicenseCompatibility | None = None
    maintenance_status: MaintenanceStatus = MaintenanceStatus.UNKNOWN
    upgrade_distance: UpgradeDistance = UpgradeDistance.UNKNOWN
    corrections: list[str] = Field(default_factory=list)


class TriagePlan(BaseModel):
    project: str | None = None
    entries: list[TriageEntry] = Field(default_factory=list)
    error: str | None = None


# --- replacement search -----------------------------------------------------


class MigrationEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ReplacementCandidateDraft(BaseModel):
    name: str = Field(description="PyPI name of the candidate, exactly as it appears on PyPI.")
    compatibility: str = Field(
        default="",
        description="How close the candidate's API is to the package being replaced, "
        "in one phrase, e.g. 'drop-in (same Crypto.* namespace)'.",
    )
    migration_effort: MigrationEffort = Field(
        default=MigrationEffort.UNKNOWN,
        description="How much work adopting this candidate is: low, medium or high.",
    )
    evidence: str = Field(
        default="",
        description="What you observed through the tools that supports this candidate. "
        "Cite only tool output; never assert a fact you did not look up.",
    )


class ReplacementSearchDraft(BaseModel):
    candidates: list[ReplacementCandidateDraft] = Field(
        default_factory=list,
        description="Candidates ranked best first. Only packages you looked up successfully.",
    )
    search_log: list[str] = Field(
        default_factory=list,
        description="One short line per iteration recording what you looked up and what "
        "it told you.",
    )


class ReplacementCandidate(BaseModel):
    name: str
    license: str | None = None
    maintenance_status: MaintenanceStatus = MaintenanceStatus.UNKNOWN
    latest_version: str | None = None
    compatibility: str = ""
    migration_effort: MigrationEffort = MigrationEffort.UNKNOWN
    evidence: str = ""
    dependents: int | None = None


class ReplacementSearch(BaseModel):
    package: str
    candidates: list[ReplacementCandidate] = Field(default_factory=list)
    search_log: list[str] = Field(default_factory=list)
    iterations: int = 0
    from_memory: bool = False
    corrections: list[str] = Field(default_factory=list)
    error: str | None = None


# --- proposal and apply -----------------------------------------------------


class ChangeKind(StrEnum):
    UPGRADE = "upgrade"
    REPLACE = "replace"


class Approval(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"


class ApprovalStatus(StrEnum):
    REVIEWED = "reviewed"
    PENDING_EXPLICIT_APPLY = "pending_explicit_apply"


class Replacement(BaseModel):
    """A REPLACE entry resolved: which package takes over from which."""

    package: str
    name: str
    version: str | None = None
    compatibility: str = ""


class ProposedChange(BaseModel):
    package: str
    kind: ChangeKind = ChangeKind.UPGRADE
    from_version: str | None = None
    to_package: str | None = None
    to_version: str | None = None
    branch: TriageBranch = TriageBranch.AUTO_UPGRADE
    rationale: str = ""
    migration_notes: str = ""

    @property
    def target_package(self) -> str:
        return self.to_package or self.package


class UpgradeProposal(BaseModel):
    proposal_id: str
    project: str
    manifest_path: str
    manifest_hash: str
    created_at: str
    diff: str = ""
    changes: list[ProposedChange] = Field(default_factory=list)
    rationale: dict[str, str] = Field(default_factory=dict)
    approvals: dict[str, Approval] = Field(default_factory=dict)
    approval_status: ApprovalStatus = ApprovalStatus.PENDING_EXPLICIT_APPLY
    unresolved: list[str] = Field(default_factory=list)
    applied: bool = False
    warnings: list[str] = Field(default_factory=list)

    def approved_packages(self) -> list[str]:
        return [name for name, decision in self.approvals.items() if decision == Approval.APPROVED]


class AppliedChange(BaseModel):
    package: str
    kind: ChangeKind = ChangeKind.UPGRADE
    from_version: str | None = None
    to: str | None = None


class ApplyResult(BaseModel):
    status: str
    proposal_id: str
    manifest_path: str | None = None
    backup_path: str | None = None
    applied: list[AppliedChange] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    memory_updates: list[str] = Field(default_factory=list)
    error: str | None = None


# --- memory -----------------------------------------------------------------


class DecisionAction(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    RESOLVED_HUMAN_REVIEW = "resolved_human_review"
    DEFERRED = "deferred"


class Decision(BaseModel):
    package: str
    action: DecisionAction
    branch: TriageBranch | None = None
    reason: str = ""
    replacement: str | None = None


class EpisodicEntry(BaseModel):
    """One recorded event in a project's audit history."""

    recorded_at: str
    package: str
    action: DecisionAction
    branch: TriageBranch | None = None
    reason: str = ""
    proposal_id: str | None = None


class AuditRecord(BaseModel):
    """The summary of one completed audit, as the molt://audits resource serves it."""

    date: str
    proposal_id: str
    project: str
    summary: dict[str, int] = Field(default_factory=dict)
    open_items: list[dict[str, str]] = Field(default_factory=list)
    diff: str = ""


class ProceduralRule(BaseModel):
    rule: str
    confidence: float = 0.0
    derived_from: list[str] = Field(default_factory=list)
    effect: str = ""


class SemanticFact(BaseModel):
    key: str
    fact: str
    established: str
    package: str | None = None
    value: str | None = None


class ProjectContext(BaseModel):
    project: str
    episodic: list[EpisodicEntry] = Field(default_factory=list)
    procedural: list[ProceduralRule] = Field(default_factory=list)
    semantic: list[SemanticFact] = Field(default_factory=list)


class RuleChange(BaseModel):
    rule: str
    action: str
    effect: str = ""


class MemoryUpdateResult(BaseModel):
    updated: list[str] = Field(default_factory=list)
    rules_changed: list[RuleChange] = Field(default_factory=list)
