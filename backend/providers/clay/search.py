"""Wraps Clay's Searches API (query-mode) -- Clay's own GTM database of
companies and people. This is what powers coordinator discovery and
decision-maker discovery: no custom function needed, just a query.

Query grammar per developers.clay.com/use-cases/search-gtm-database:
  select from people where experiences.any(is_current = true and
    job_title is_similar_to ("scheduler", "coordinator") and
    company.domain = "example.com")

Confirmed live shape (2026-08-18) for a `people` query-mode result:
  {"clay_profile_id", "name", "first_name", "last_name",
   "location": {"name", "city", "state_or_province"},
   "matched_experiences": [{"company", "title", "location",
                             "start_date", "end_date"}, ...]}
No top-level title/company/domain/linkedin_url field -- title and current
company live inside matched_experiences[0] (the experience that matched
the query's filters). There is no LinkedIn URL in this response at all;
left None rather than fabricated. current_company_domain isn't returned
either, but the query itself filters on `company.domain = "<domain>"
and is_current = true`, so any person returned is -- by the query's own
guarantee -- currently at that domain; `_parse_person` is given that
domain and uses it directly rather than guessing at a missing field.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from backend.models.schemas import ClayPersonResult
from backend.providers.clay.client import ClayClient, ClayError, ClayQuotaExhaustedError

logger = logging.getLogger(__name__)


def _quote(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def build_people_at_company_query(domain: str, title_candidates: list[str]) -> str:
    titles = ", ".join(_quote(t) for t in title_candidates)
    return (
        "select from people where experiences.any(is_current = true and "
        f"job_title is_similar_to ({titles}) and company.domain = {_quote(domain)})"
    )


def _parse_person(raw: dict[str, Any], searched_domain: str) -> ClayPersonResult:
    first_name = raw.get("first_name") or raw.get("firstName")
    last_name = raw.get("last_name") or raw.get("lastName")
    if not first_name and raw.get("name"):
        parts = str(raw["name"]).split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else None

    experiences = raw.get("matched_experiences") or []
    top_experience = experiences[0] if experiences else {}

    raw_title = (
        raw.get("job_title") or raw.get("title") or raw.get("headline")
        or top_experience.get("title")
    )
    linkedin_url = raw.get("linkedin_url") or raw.get("linkedinUrl") or raw.get("linkedin")

    company = raw.get("company") or {}
    current_company = (
        raw.get("current_company")
        or company.get("name")
        or raw.get("company_name")
        or top_experience.get("company")
    )
    # Not present in the response -- the query itself filtered on
    # `company.domain = searched_domain and is_current = true`, so any
    # returned person is guaranteed to currently be at that domain.
    current_company_domain = (
        raw.get("current_company_domain")
        or company.get("domain")
        or raw.get("company_domain")
        or searched_domain
    )

    return ClayPersonResult(
        first_name=first_name,
        last_name=last_name,
        raw_title=raw_title,
        linkedin_url=linkedin_url,
        current_company=current_company,
        current_company_domain=current_company_domain,
    )


class ClaySearch:
    def __init__(self, client: ClayClient):
        self.client = client

    def find_people_at_company(
        self, domain: str, title_candidates: list[str], max_results: int = 25
    ) -> Optional[list[ClayPersonResult]]:
        """Returns None on a provider failure (rate limit, quota exhausted,
        network error) -- distinct from an empty list, which means the
        search genuinely succeeded and found nobody. Conflating the two
        used to silently turn a failed search into "0 qualified
        coordinators" and disqualify the company outright -- confirmed
        live: concurrent coordinator search across a batch triggered
        sustained rate limiting, and ~33% of one run's candidates (86 of
        263) got silently zeroed out this way instead of being flagged
        for retry. Callers must check for None explicitly."""
        query = build_people_at_company_query(domain, title_candidates)
        people: list[ClayPersonResult] = []
        try:
            for raw in self.client.iter_query_search(query, "people", max_results=max_results):
                people.append(_parse_person(raw, domain))
        except ClayQuotaExhaustedError:
            logger.warning("Clay search quota exhausted while searching people at %s", domain)
            return None
        except ClayError as exc:
            logger.warning("Clay search failed for %s: %s", domain, exc)
            return None
        return people

    def find_decision_makers(
        self, domain: str, title_priority: list[str], max_results: int = 10
    ) -> Optional[list[ClayPersonResult]]:
        return self.find_people_at_company(domain, title_priority, max_results=max_results)

    def find_company_record(self, domain: str, prefer_name: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Looks up a company by domain in Clay's own GTM database.
        Confirmed live shape: {"name", "size", "type", "domain", "country",
        "industry", "location", "description", "linkedin_url",
        "annual_revenue", "total_funding_amount_range_usd"}. Multiple
        records can share a domain (parent/sub-brand entities, e.g. a
        staffing-arm brand sharing the parent's domain) -- confirmed live
        against a real company. When `prefer_name` is given, picks the
        record whose name matches (case-insensitive substring either
        direction); otherwise falls back to the largest company by
        employee-count upper bound, on the theory that the primary entity
        for a domain is usually the biggest one."""
        query = f'select from companies where domain = {_quote(domain)}'
        records: list[dict[str, Any]] = []
        try:
            for raw in self.client.iter_query_search(query, "companies", max_results=5):
                records.append(raw)
        except ClayQuotaExhaustedError:
            logger.warning("Clay search quota exhausted while looking up company %s", domain)
        except ClayError as exc:
            logger.warning("Clay company lookup failed for %s: %s", domain, exc)
        if not records:
            return None
        if len(records) == 1:
            return records[0]

        if prefer_name:
            target = prefer_name.strip().lower()
            for r in records:
                name = (r.get("name") or "").strip().lower()
                if name and (name == target or name in target or target in name):
                    return r

        def _size_upper_bound(r: dict[str, Any]) -> int:
            size = (r.get("size") or "").replace(",", "")
            m = re.findall(r"\d+", size)
            return int(m[-1]) if m else 0

        return max(records, key=_size_upper_bound)

    def find_company_linkedin_url(self, domain: str, prefer_name: Optional[str] = None) -> Optional[str]:
        record = self.find_company_record(domain, prefer_name=prefer_name)
        return record.get("linkedin_url") if record else None
