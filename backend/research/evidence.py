"""Read-side helpers over research_results/research_evidence, and buying
signal detection derived from them. A signal is only ever recorded when a
research field actually produced evidence for it -- never inferred from
absence of data beyond the specific NO_CLIENT_PORTAL case, where "the
company was researched and no portal was found" is itself the evidence.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.campaigns.configs.base import CampaignConfig
from backend.models.database import BuyingSignalRecord, ResearchResult
from backend.models.schemas import Confidence, SignalType

# Which research fields, with which value pattern, count as evidence for
# which signal. Kept out of campaign config because it's a fixed mapping
# of playbook rules, not something that varies per vertical beyond which
# field name is being read.
_HIRING_SIGNAL_TYPES = {
    "court_reporting": SignalType.HIRING_SCHEDULER,
    "process_serving": SignalType.HIRING_DISPATCHER,
    "ia_ime": SignalType.HIRING_COORDINATOR,
}


def get_research_map(session: Session, company_id: int, campaign_id: str) -> dict[str, ResearchResult]:
    rows = (
        session.query(ResearchResult)
        .filter(ResearchResult.company_id == company_id, ResearchResult.campaign_id == campaign_id)
        .all()
    )
    return {r.field_name: r for r in rows}


def get_value(research_map: dict[str, ResearchResult], field_name: str) -> str | None:
    row = research_map.get(field_name)
    return row.value if row else None


def _evidence_url_and_text(session: Session, result: ResearchResult) -> tuple[str | None, str | None]:
    ev = result.evidence[0] if result.evidence else None
    return (ev.source_url if ev else None, ev.evidence_text if ev else None)


def detect_signals(
    session: Session,
    company_id: int,
    campaign: CampaignConfig,
) -> list[BuyingSignalRecord]:
    research_map = get_research_map(session, company_id, campaign.key)
    signals: list[BuyingSignalRecord] = []
    now = datetime.utcnow()

    hiring = research_map.get("hiring_signal")
    if hiring and hiring.value:
        url, text = _evidence_url_and_text(session, hiring)
        signal_type = _HIRING_SIGNAL_TYPES.get(campaign.key, SignalType.HIRING_COORDINATOR)
        signals.append(
            BuyingSignalRecord(
                company_id=company_id,
                campaign_id=campaign.key,
                signal_type=signal_type.value,
                signal_value=hiring.value,
                source_url=url,
                evidence_text=text,
                confidence=hiring.confidence,
                detected_at=now,
            )
        )

    open_roles = research_map.get("open_scheduler_roles") or research_map.get("open_dispatch_roles") or research_map.get("open_coordinator_roles")
    if open_roles and open_roles.value:
        digits = "".join(ch for ch in open_roles.value if ch.isdigit())
        if digits and int(digits) >= 3:
            url, text = _evidence_url_and_text(session, open_roles)
            signals.append(
                BuyingSignalRecord(
                    company_id=company_id,
                    campaign_id=campaign.key,
                    signal_type=SignalType.MULTIPLE_OPERATIONS_ROLES.value,
                    signal_value=open_roles.value,
                    source_url=url,
                    evidence_text=text,
                    confidence=open_roles.confidence,
                    detected_at=now,
                )
            )

    portal = research_map.get("client_portal") or research_map.get("status_tracking")
    if portal and portal.value and portal.value.strip().upper().startswith("NO"):
        url, text = _evidence_url_and_text(session, portal)
        signals.append(
            BuyingSignalRecord(
                company_id=company_id,
                campaign_id=campaign.key,
                signal_type=SignalType.NO_CLIENT_PORTAL.value,
                signal_value=portal.value,
                source_url=url,
                evidence_text=text,
                confidence=portal.confidence,
                detected_at=now,
            )
        )

    for row in signals:
        session.add(row)
    session.commit()
    return signals
