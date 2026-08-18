"""Clay's own GTM database as a raw-company source for court reporting.

This is a deliberate, explicit exception to the architecture principle
that Clay is enrichment/research, not the raw company source -- added at
the user's request after NCRA + state associations proved too low-volume
(3-10 real companies total) to reach a meaningful final export. Clay's
company search is used here ONLY as an additional candidate source; it
still goes through the exact same firmographic filter, coordinator
qualification, and tiering as every other source -- Clay tells us "here
is a company that might be a court reporting agency," the application
still decides whether it qualifies.

Query built against Clay's documented search grammar
(GET /search/query-mode/reference), confirmed live 2026-08-18:
  select from companies where
    industry = "Legal Services"
    and products_and_services is_similar_to ("court reporting", "deposition services")
    and company_size in ("11-50", "51-200")
    and locations.any(country_name = "United States")

A products_and_services-only query (no industry filter) was tested first
and returned mostly irrelevant video-production companies (the semantic
match on "legal videography" drifts toward general video agencies).
Requiring industry = "Legal Services" alongside the semantic match fixed
this -- live results include real, verifiable agencies (Barkley Court
Reporters, Atkinson-Baker/Veritext, TSG Reporting, NAEGELI Deposition &
Trial, Capital Reporting Company, ...).

Pagination note: confirmed live that Clay's query-mode search has no
page_token in its response -- calling run_query_search again with the
SAME search_id advances a server-side cursor (see client.py). This
adapter relies on that via ClaySearch/ClayClient.iter_query_search.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.models.schemas import RawCompany
from backend.providers.clay.client import ClayClient, ClayError, ClayQuotaExhaustedError
from backend.utils.normalize import normalize_domain

logger = logging.getLogger(__name__)

SOURCE_NAME = "clay_icp_search"

QUERY = (
    'select from companies where '
    'industry = "Legal Services" '
    'and products_and_services is_similar_to ("court reporting", "deposition services") '
    'and company_size in ("11-50", "51-200") '
    'and locations.any(country_name = "United States")'
)


def _parse_company(raw: dict[str, Any]) -> Optional[RawCompany]:
    domain = normalize_domain(raw.get("domain"))
    name = (raw.get("name") or "").strip()
    if not domain or not name:
        return None

    return RawCompany(
        company_name=name,
        website=f"https://{domain}",
        domain=domain,
        phone=None,
        address=raw.get("location"),
        city=(raw.get("location") or "").split(",")[0].strip() or None,
        state=None,
        country="US",
        source=SOURCE_NAME,
        source_url=raw.get("linkedin_url"),
        source_identifier=str(raw.get("clay_company_id")) if raw.get("clay_company_id") else None,
    )


def collect(api_key: str, max_results: int = 150) -> list[RawCompany]:
    client = ClayClient(api_key=api_key)
    companies: list[RawCompany] = []
    seen_domains: set[str] = set()
    try:
        for raw in client.iter_query_search(QUERY, "companies", max_results=max_results):
            company = _parse_company(raw)
            if company is None or company.domain in seen_domains:
                continue
            seen_domains.add(company.domain)
            companies.append(company)
    except ClayQuotaExhaustedError:
        logger.warning("Clay search quota exhausted during ICP company search")
    except ClayError as exc:
        logger.warning("Clay ICP company search failed: %s", exc)
    finally:
        client.close()
    return companies


def collect_for_campaign(campaign) -> list[RawCompany]:
    """Uniform entry point the engine's source dispatcher calls."""
    import os

    api_key = os.environ.get("CLAY_API_KEY")
    if not api_key:
        return []
    return collect(api_key)
