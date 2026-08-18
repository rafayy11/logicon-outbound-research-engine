"""Playbook Part 3 disqualifiers. Only the fewer-than-11-employees and
non-US rules are auto-detected from a hard number -- everything else the
playbook describes ("PE-owned with a mandated stack", "signed a
multi-year vertical SaaS contract", "staff augmentation / developer-hour
request") is a fact about how the company presents itself, so it is only
triggered when real evidence text contains it. No evidence -> not
triggered, never guessed. This keeps the disqualifier set configurable
rather than hard-coded speculation, per the build instruction.
"""

from __future__ import annotations

import re
from typing import Optional

from backend.models.database import Company

MIN_EMPLOYEES_HARD = 11

_PE_OWNED_PATTERNS = [
    r"\bportfolio company\b",
    r"\backed by [A-Z][\w& ]+ Capital\b",
    r"\bprivate equity\b.{0,40}\b(owned|backed|acquired)\b",
]

_SAAS_CONTRACT_PATTERNS = [
    r"\b(signed|entered into)\b.{0,40}\bmulti-?year\b.{0,40}\bcontract\b",
    r"\b\d\s*-?year\b.{0,20}\b(agreement|contract)\b.{0,40}\bplatform\b",
]

_STAFF_AUG_PATTERNS = [
    r"\bstaff augmentation\b",
    r"\bdevelopers? by the hour\b",
    r"\bdedicated developer(s)?\b",
    r"\bhourly (development|engineering) resources\b",
]


def check_hard_firmographic(company: Company) -> Optional[str]:
    """Automatic, number-based disqualifiers only."""
    if company.country and company.country.upper() not in ("US", "USA", "UNITED STATES"):
        return f"Non-US company (country={company.country})"
    if company.employee_count is not None and company.employee_count < MIN_EMPLOYEES_HARD:
        return f"Fewer than {MIN_EMPLOYEES_HARD} employees (employee_count={company.employee_count})"
    return None


def _match_any(patterns: list[str], text: str) -> Optional[str]:
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def check_text_based_disqualifiers(text: Optional[str]) -> Optional[str]:
    """Scans real enrichment/research text (company description, news,
    software_mentioned evidence) for explicit disqualifying language.
    Returns the disqualification_reason with the matched phrase, or None."""
    if not text:
        return None

    match = _match_any(_PE_OWNED_PATTERNS, text)
    if match:
        return f"PE-owned with mandated technology stack (evidence: '{match}')"

    match = _match_any(_SAAS_CONTRACT_PATTERNS, text)
    if match:
        return f"Recent multi-year vertical SaaS contract documented (evidence: '{match}')"

    match = _match_any(_STAFF_AUG_PATTERNS, text)
    if match:
        return f"Looking for staff augmentation / developer-hour work (evidence: '{match}')"

    return None


_IT_TITLE_PATTERN = re.compile(
    r"\b(IT|information technology|systems? administrator|network administrator|"
    r"help ?desk|CTO|CIO|IT director|IT manager)\b",
    re.IGNORECASE,
)
_NON_IT_HINT_PATTERN = re.compile(
    r"\b(operations|scheduling|dispatch|owner|president|coo|office manager)\b",
    re.IGNORECASE,
)


def check_it_only_contact(candidate_titles: list[str]) -> Optional[str]:
    """Applied at decision-maker discovery: if every reachable candidate
    title is IT-flavored and none look like an operations/business buyer,
    this is an IT-only-reachable disqualification."""
    if not candidate_titles:
        return None
    any_it = any(_IT_TITLE_PATTERN.search(t or "") for t in candidate_titles)
    any_non_it = any(_NON_IT_HINT_PATTERN.search(t or "") for t in candidate_titles)
    if any_it and not any_non_it:
        return "Only an IT contact reachable -- this is an operations sale, not a security review"
    return None
