"""Coordinator/scheduler/dispatcher discovery + classification.

Per the build instruction: this is NOT "count everyone with the word
coordinator in their title." A person's title only counts once it's been
classified as genuine operational/provider coordination. Classification
is deterministic and rule-based (not an extra Clay/LLM call) so it's
explainable via classification_reason and doesn't spend credits per
person.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from backend.campaigns.configs.base import CampaignConfig
from backend.models.database import CoordinatorClassification, Person
from backend.models.schemas import CompanyStatus, Confidence, CoordinatorClassificationLabel, RoleCategory
from backend.providers.clay.search import ClaySearch

logger = logging.getLogger(__name__)

# Functions that disqualify a "coordinator"-adjacent title regardless of
# campaign, per the playbook's NOT_QUALIFIED examples (marketing, HR,
# recruiting, unrelated customer success / project coordination).
_DISQUALIFYING_PATTERNS = [
    r"\bmarketing\b",
    r"\b(hr|human resources)\b",
    r"\brecruit(ing|er|ment)?\b",
    r"\btalent\b",
    r"\bcustomer success\b",
    r"\bsales\b",
    r"\baccounting\b",
    r"\bfinance\b",
    r"\bevent(s)? coordinator\b",
    r"\bproject coordinator\b",  # generic PM-style coordination, not provider dispatch
]

# Titles that clearly ARE genuine operational/provider coordination --
# specific enough that they don't need human review. Matches the "schedul"
# and "dispatch" word stems broadly (not just "scheduler"/"dispatcher"
# exactly) because real titles vary the suffix -- confirmed against live
# Clay search results for a real court reporting agency, which returned
# "Scheduling Specialist" and "Senior Scheduling Coordinator" alongside
# "Scheduler": all clearly the same function, different noun.
_STRONG_QUALIFYING_PATTERNS = [
    r"\bschedul\w*\b",          # scheduler, scheduling coordinator/manager/specialist
    r"\bdispatch\w*\b",         # dispatcher, dispatch/dispatching coordinator
    r"\boperations coordinator\b",
    r"\bclaims coordinator\b",
    r"\bnetwork coordinator\b",
]

_BARE_COORDINATOR_PATTERN = re.compile(r"\bcoordinator\b", re.IGNORECASE)


def classify_title(raw_title: str | None) -> tuple[CoordinatorClassificationLabel, str, Confidence]:
    if not raw_title or not raw_title.strip():
        return CoordinatorClassificationLabel.NOT_QUALIFIED, "No title available", Confidence.LOW

    title = raw_title.lower()

    for pattern in _DISQUALIFYING_PATTERNS:
        if re.search(pattern, title):
            return (
                CoordinatorClassificationLabel.NOT_QUALIFIED,
                f"Title indicates an unrelated function ('{raw_title}')",
                Confidence.HIGH,
            )

    for pattern in _STRONG_QUALIFYING_PATTERNS:
        if re.search(pattern, title):
            return (
                CoordinatorClassificationLabel.QUALIFIED_COORDINATION,
                f"Title matches operational/provider coordination ('{raw_title}')",
                Confidence.HIGH,
            )

    if _BARE_COORDINATOR_PATTERN.search(title):
        return (
            CoordinatorClassificationLabel.REVIEW,
            f"Generic 'coordinator' title without clear operational context ('{raw_title}') -- needs human review",
            Confidence.MEDIUM,
        )

    return (
        CoordinatorClassificationLabel.NOT_QUALIFIED,
        f"Title does not match any coordination role pattern ('{raw_title}')",
        Confidence.MEDIUM,
    )


def discover_and_classify_coordinators(
    session: Session,
    company,
    campaign: CampaignConfig,
    clay_search: ClaySearch,
) -> list[Person]:
    people_results = clay_search.find_people_at_company(
        company.canonical_domain, campaign.coordinator_title_candidates, max_results=25
    )

    people: list[Person] = []
    for pr in people_results:
        person = Person(
            company_id=company.id,
            first_name=pr.first_name,
            last_name=pr.last_name,
            raw_title=pr.raw_title,
            normalized_title=(pr.raw_title or "").strip().title() or None,
            linkedin_url=pr.linkedin_url,
            current_company=pr.current_company,
            current_company_domain=pr.current_company_domain,
            role_category=RoleCategory.COORDINATOR.value,
            source="clay_search",
            discovered_at=datetime.utcnow(),
        )
        session.add(person)
        session.flush()

        label, reason, confidence = classify_title(pr.raw_title)
        session.add(
            CoordinatorClassification(
                person_id=person.id,
                campaign_id=campaign.key,
                classification=label.value,
                classification_reason=reason,
                classification_confidence=confidence.value,
                classified_at=datetime.utcnow(),
            )
        )
        people.append(person)

    company.status = CompanyStatus.COORDINATORS_FOUND.value
    session.commit()
    return people


def qualified_coordinator_count(session: Session, company_id: int, campaign_id: str) -> int:
    return (
        session.query(CoordinatorClassification)
        .join(Person, Person.id == CoordinatorClassification.person_id)
        .filter(
            Person.company_id == company_id,
            CoordinatorClassification.campaign_id == campaign_id,
            CoordinatorClassification.classification == CoordinatorClassificationLabel.QUALIFIED_COORDINATION.value,
        )
        .count()
    )
