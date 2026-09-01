"""Pydantic models passed between layers. Source adapters and the Clay
provider both normalize into these -- nothing downstream touches raw
source HTML or raw Clay JSON directly."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class IdentityConfidence(str, Enum):
    HIGH = "HIGH"      # canonical domain matched
    MEDIUM = "MEDIUM"  # name + location match
    LOW = "LOW"        # name only / weak match


class CompanyStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    NORMALIZED = "NORMALIZED"
    DEDUPLICATED = "DEDUPLICATED"
    SUPPRESSION_CHECKED = "SUPPRESSION_CHECKED"
    FIRMOGRAPHICALLY_FILTERED = "FIRMOGRAPHICALLY_FILTERED"
    RESEARCH_PENDING = "RESEARCH_PENDING"
    RESEARCHED = "RESEARCHED"
    COORDINATORS_PENDING = "COORDINATORS_PENDING"
    COORDINATORS_FOUND = "COORDINATORS_FOUND"
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"
    DECISION_MAKER_PENDING = "DECISION_MAKER_PENDING"
    DECISION_MAKER_FOUND = "DECISION_MAKER_FOUND"
    EMPLOYMENT_VERIFIED = "EMPLOYMENT_VERIFIED"
    EMAIL_FOUND = "EMAIL_FOUND"
    PERSONALIZATION_READY = "PERSONALIZATION_READY"
    QA_READY = "QA_READY"
    READY = "READY"
    EXPORTED = "EXPORTED"
    BLOCKED = "BLOCKED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class SourceType(str, Enum):
    COMPANY_WEBSITE = "COMPANY_WEBSITE"
    JOB_POSTING = "JOB_POSTING"
    CLAY_ENRICHMENT = "CLAY_ENRICHMENT"
    GOOGLE_PLACES = "GOOGLE_PLACES"
    ASSOCIATION_DIRECTORY = "ASSOCIATION_DIRECTORY"
    OTHER = "OTHER"


class RoleCategory(str, Enum):
    COORDINATOR = "COORDINATOR"
    DECISION_MAKER = "DECISION_MAKER"


class CoordinatorClassificationLabel(str, Enum):
    QUALIFIED_COORDINATION = "QUALIFIED_COORDINATION"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    REVIEW = "REVIEW"


class Tier(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class EmailStatus(str, Enum):
    FOUND = "FOUND"
    MISSING = "MISSING"


class SuppressionType(str, Enum):
    EMAIL = "EMAIL"
    DOMAIN = "DOMAIN"
    COMPANY = "COMPANY"


class SignalType(str, Enum):
    HIRING_COORDINATOR = "HIRING_COORDINATOR"
    HIRING_SCHEDULER = "HIRING_SCHEDULER"
    HIRING_DISPATCHER = "HIRING_DISPATCHER"
    MULTIPLE_OPERATIONS_ROLES = "MULTIPLE_OPERATIONS_ROLES"
    HEADCOUNT_GROWTH = "HEADCOUNT_GROWTH"
    ACQUISITION = "ACQUISITION"
    NEW_OFFICE = "NEW_OFFICE"
    NO_CLIENT_PORTAL = "NO_CLIENT_PORTAL"
    CALL_TO_SCHEDULE = "CALL_TO_SCHEDULE"
    MANUAL_STATUS_PROCESS = "MANUAL_STATUS_PROCESS"


# ---------------------------------------------------------------------------
# Source layer output
# ---------------------------------------------------------------------------

class RawCompany(BaseModel):
    """What a source adapter produces. Companies only -- no contacts."""

    company_name: str
    website: Optional[str] = None
    domain: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "US"
    description: Optional[str] = None
    source: str
    source_url: Optional[str] = None
    source_identifier: Optional[str] = None

    @field_validator("company_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Canonical company (post normalization/dedup)
# ---------------------------------------------------------------------------

class CanonicalCompany(BaseModel):
    id: Optional[int] = None
    company_name: str
    canonical_domain: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "US"
    identity_confidence: IdentityConfidence = IdentityConfidence.LOW
    identity_key: Optional[str] = None
    employee_count: Optional[int] = None
    revenue: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    status: CompanyStatus = CompanyStatus.DISCOVERED
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Clay provider normalized results (internal -- never raw Clay JSON downstream)
# ---------------------------------------------------------------------------

class EnrichmentResult(BaseModel):
    field_name: str
    value: Optional[str] = None
    confidence: Confidence = Confidence.NONE
    raw_source: str = "clay_managed_function"


class ResearchResult(BaseModel):
    company_id: int
    campaign_id: str
    field_name: str
    value: Optional[str] = None          # "NONE" string sentinel allowed
    normalized_value: Optional[str] = None
    source_url: Optional[str] = None
    source_type: SourceType = SourceType.OTHER
    evidence_text: Optional[str] = None
    confidence: Confidence = Confidence.NONE
    researched_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    prompt_version: str = "v1"
    research_run_id: Optional[str] = None
    idempotency_key: str


class ClayPersonResult(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    raw_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    current_company: Optional[str] = None
    current_company_domain: Optional[str] = None


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

class PersonRecord(BaseModel):
    id: Optional[int] = None
    company_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    raw_title: Optional[str] = None
    normalized_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    current_company: Optional[str] = None
    current_company_domain: Optional[str] = None
    role_category: RoleCategory


class CoordinatorClassificationResult(BaseModel):
    person_id: int
    campaign_id: str
    classification: CoordinatorClassificationLabel
    classification_reason: str
    classification_confidence: Confidence


class QualificationResult(BaseModel):
    company_id: int
    campaign_id: str
    passed: bool
    disqualification_reason: Optional[str] = None
    tier: Optional[Tier] = None
    coordinator_count: int = 0
    qualification_reason: Optional[str] = None
    qualification_confidence: Confidence = Confidence.NONE


class BuyingSignal(BaseModel):
    company_id: int
    campaign_id: str
    signal_type: SignalType
    signal_value: Optional[str] = None
    source_url: Optional[str] = None
    evidence_text: Optional[str] = None
    confidence: Confidence = Confidence.NONE
    detected_at: datetime = Field(default_factory=datetime.utcnow)


class DecisionMakerStatus(BaseModel):
    person_id: int
    company_id: int
    campaign_id: str
    title_priority_rank: Optional[int] = None
    employment_verified: bool = False
    employment_source: Optional[str] = None
    employment_verified_at: Optional[datetime] = None
    email: Optional[str] = None
    email_source: Optional[str] = None
    email_status: EmailStatus = EmailStatus.MISSING


# ---------------------------------------------------------------------------
# Final prospect object -> CSV rows
# ---------------------------------------------------------------------------

class FinalProspect(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    email: str
    company: str
    domain: str
    website: Optional[str] = None
    role_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "US"
    employee_count: Optional[int] = None
    industry: Optional[str] = None
    tier: Tier
    coordinator_count: int
    role_count: int
    volume_var: Optional[str] = None
    coverage_var: Optional[str] = None
    signal_phrase: Optional[str] = None
    campaign: str
    qualification_reason: Optional[str] = None


class RejectedCompany(BaseModel):
    company: str
    domain: Optional[str] = None
    source: Optional[str] = None
    rejection_stage: str
    rejection_reason: str


class CampaignRunStats(BaseModel):
    campaign: str
    target_count: int
    buffer_pct: float = 15.0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    raw_companies: int = 0
    deduplicated: int = 0
    suppressed: int = 0
    firmographic_pass: int = 0
    research_candidates: int = 0
    research_completed: int = 0
    coordinator_candidates: int = 0
    coordinator_qualified: int = 0
    tier_a: int = 0
    tier_b: int = 0
    tier_c: int = 0
    decision_makers: int = 0
    employment_verified_count: int = 0
    usable_emails: int = 0
    final_exported: int = 0
    clay_estimated_usage: int = 0
    clay_actual_usage: int = 0
    clay_remaining_budget: Optional[int] = None
    provider_error_count: int = 0
    provider_retry_count: int = 0

    @property
    def credit_efficiency(self) -> Optional[float]:
        if self.clay_actual_usage <= 0:
            return None
        return round(self.final_exported / self.clay_actual_usage, 4)

    @property
    def max_final_pool(self) -> int:
        return int(self.target_count * (1 + self.buffer_pct / 100))
