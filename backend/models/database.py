"""SQLAlchemy models + session/engine setup. SQLite for MVP."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


class Base(DeclarativeBase):
    pass


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    versions: Mapped[list["CampaignVersion"]] = relationship(back_populates="campaign")


class CampaignVersion(Base):
    __tablename__ = "campaign_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    rules_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    campaign: Mapped["Campaign"] = relationship(back_populates="versions")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_domain: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    company_name: Mapped[str] = mapped_column(String(500))
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    country: Mapped[str] = mapped_column(String(64), default="US")
    identity_confidence: Mapped[str] = mapped_column(String(16), default="LOW")
    identity_key: Mapped[Optional[str]] = mapped_column(String(500), index=True, nullable=True)
    employee_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revenue: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="DISCOVERED", index=True)
    # Set whenever status becomes DISQUALIFIED, regardless of which stage
    # (firmographic filter, coordinator qualification, ...) made the call --
    # a single durable source of "why", since a company disqualified early
    # never gets a Qualification row to hold a reason. Every disqualification
    # must have a reason (spec requirement); this is what rejected.csv reads
    # on subsequent runs, after the company that made the call is skipped.
    disqualification_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    sources: Mapped[list["CompanySource"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class CompanySource(Base):
    __tablename__ = "company_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    source_identifier: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped["Company"] = relationship(back_populates="sources")


class ResearchResult(Base):
    __tablename__ = "research_results"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_research_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    normalized_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), default="NONE")
    researched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    research_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), default="SUCCESS")

    evidence: Mapped[list["ResearchEvidence"]] = relationship(back_populates="result", cascade="all, delete-orphan")


class ResearchEvidence(Base):
    __tablename__ = "research_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_result_id: Mapped[int] = mapped_column(ForeignKey("research_results.id"), index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="OTHER")
    evidence_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    result: Mapped["ResearchResult"] = relationship(back_populates="evidence")


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    raw_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    normalized_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    current_company: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    current_company_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role_category: Mapped[str] = mapped_column(String(32))
    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CoordinatorClassification(Base):
    __tablename__ = "coordinator_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), unique=True, index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    classification: Mapped[str] = mapped_column(String(32))
    classification_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    classification_confidence: Mapped[str] = mapped_column(String(16), default="NONE")
    classified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Qualification(Base):
    __tablename__ = "qualifications"
    __table_args__ = (UniqueConstraint("company_id", "campaign_id", name="uq_qualification_company_campaign"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    disqualification_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    tier: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    coordinator_count: Mapped[int] = mapped_column(Integer, default=0)
    qualification_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qualification_confidence: Mapped[str] = mapped_column(String(16), default="NONE")
    qualified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BuyingSignalRecord(Base):
    __tablename__ = "buying_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    signal_type: Mapped[str] = mapped_column(String(64))
    signal_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    evidence_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), default="NONE")
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DecisionMakerStatusRecord(Base):
    __tablename__ = "decision_maker_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), unique=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    title_priority_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    employment_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    employment_source: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    employment_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_source: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    email_status: Mapped[str] = mapped_column(String(16), default="MISSING")


class Suppression(Base):
    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(500), index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CampaignRun(Base):
    __tablename__ = "campaign_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    target_count: Mapped[int] = mapped_column(Integer)
    buffer_pct: Mapped[float] = mapped_column(Float, default=15.0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    raw_companies: Mapped[int] = mapped_column(Integer, default=0)
    deduplicated: Mapped[int] = mapped_column(Integer, default=0)
    suppressed: Mapped[int] = mapped_column(Integer, default=0)
    firmographic_pass: Mapped[int] = mapped_column(Integer, default=0)
    research_candidates: Mapped[int] = mapped_column(Integer, default=0)
    research_completed: Mapped[int] = mapped_column(Integer, default=0)
    coordinator_candidates: Mapped[int] = mapped_column(Integer, default=0)
    coordinator_qualified: Mapped[int] = mapped_column(Integer, default=0)
    tier_a: Mapped[int] = mapped_column(Integer, default=0)
    tier_b: Mapped[int] = mapped_column(Integer, default=0)
    tier_c: Mapped[int] = mapped_column(Integer, default=0)
    decision_makers: Mapped[int] = mapped_column(Integer, default=0)
    employment_verified_count: Mapped[int] = mapped_column(Integer, default=0)
    usable_emails: Mapped[int] = mapped_column(Integer, default=0)
    final_exported: Mapped[int] = mapped_column(Integer, default=0)

    clay_estimated_usage: Mapped[int] = mapped_column(Integer, default=0)
    clay_actual_usage: Mapped[int] = mapped_column(Integer, default=0)
    clay_remaining_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="RUNNING")


class ProviderError(Base):
    __tablename__ = "provider_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaign_runs.id"), nullable=True, index=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64))
    error: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("campaign_runs.id"), index=True)
    export_type: Mapped[str] = mapped_column(String(32))
    file_path: Mapped[str] = mapped_column(String(1000))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QAReview(Base):
    __tablename__ = "qa_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("campaign_runs.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal: Optional[sessionmaker] = None


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///data/outbound_engine.db")


def get_engine():
    global _engine
    if _engine is None:
        url = get_database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
    return _engine


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()
