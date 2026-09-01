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
SAME search_id advances a server-side cursor (see client.py).

Confirmed live 2026-08-19: this query's real matching pool is 397
companies (paging to exhaustion returns has_more=False,
exhaustion_reason=no_more_results). Also confirmed live the same day:
Clay's *search* endpoint draws from a completely separate quota
(period_quota in the raw response: 1,000,000/year, ~7k used) from
whatever the enrichment/routine functions cost -- re-fetching this
source repeatedly does not touch the scarce enrichment credit pool.

That said, every run used to call create_query_search() fresh, which
starts Clay's server-side cursor over from the top each time -- so
repeated runs kept re-fetching roughly the same ~150-500 top-ranked
companies instead of reaching new ones, and re-inserted a
CompanySource row for each already-known company every time (1,456 such
rows accumulated for a query with only 397 real matches). Fixed by
persisting the search_id (keyed by a hash of QUERY, so an edited query
naturally starts a fresh search) in data/state/clay_icp_search_state.json
and reusing it -- each run now continues the SAME cursor forward,
fetching only companies not already seen, and once Clay reports
exhaustion (all 397 found) every future run short-circuits to 0 API
calls instead of re-fetching.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from backend.models.schemas import RawCompany
from backend.providers.clay.client import ClayClient, ClayError, ClayQuotaExhaustedError
from backend.utils.normalize import normalize_domain

logger = logging.getLogger(__name__)

SOURCE_NAME = "clay_icp_search"
STATE_PATH = Path("data/state/clay_icp_search_state.json")

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
        description=raw.get("description") or None,
        source=SOURCE_NAME,
        source_url=raw.get("linkedin_url"),
        source_identifier=str(raw.get("clay_company_id")) if raw.get("clay_company_id") else None,
    )


def _query_key(query: str, account_name: str) -> str:
    # search_id is workspace-specific -- keying only by query would try
    # to reuse account A's search_id on account B after a switch, which
    # fails (it's meaningless outside the workspace that created it).
    # Including the account name means switching accounts just starts a
    # fresh search on the new one, no manual state-file cleanup needed.
    return hashlib.sha256(f"{account_name}:{query}".encode()).hexdigest()[:16]


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def collect(api_key: str, max_results: int = 150, account_name: str = "primary") -> list[RawCompany]:
    state = _load_state()
    key = _query_key(QUERY, account_name)
    entry = state.get(key)

    if entry and entry.get("exhausted"):
        logger.info(
            "Clay ICP search already exhausted for this query (%d companies fetched total) -- "
            "0 new companies to pull, skipping.",
            entry.get("total_fetched", 0),
        )
        return []

    client = ClayClient(api_key=api_key)
    companies: list[RawCompany] = []
    seen_domains: set[str] = set()
    search_id = entry.get("search_id") if entry else None
    fetched = 0
    exhausted = False

    try:
        if search_id is None:
            search_id = client.create_query_search(QUERY, "companies")

        while fetched < max_results:
            page = client.run_query_search(search_id)
            results = page.get("data") or []
            if not results:
                exhausted = True
                break
            for raw in results:
                if fetched >= max_results:
                    break
                fetched += 1
                company = _parse_company(raw)
                if company is None or company.domain in seen_domains:
                    continue
                seen_domains.add(company.domain)
                companies.append(company)
            if not page.get("has_more"):
                exhausted = True
                break
    except ClayQuotaExhaustedError:
        logger.warning("Clay search quota exhausted during ICP company search")
    except ClayError as exc:
        logger.warning("Clay ICP company search failed: %s", exc)
    finally:
        client.close()

    total_fetched = (entry.get("total_fetched", 0) if entry else 0) + fetched
    state[key] = {"search_id": search_id, "total_fetched": total_fetched, "exhausted": exhausted, "query": QUERY}
    _save_state(state)
    logger.info(
        "Clay ICP search: %d new rows this call, %d total ever fetched via this search, exhausted=%s",
        fetched, total_fetched, exhausted,
    )
    return companies


def collect_for_campaign(campaign) -> list[RawCompany]:
    """Uniform entry point the engine's source dispatcher calls."""
    from backend.providers.clay.accounts import active_account

    account = active_account()
    if not account.api_key:
        return []
    return collect(account.api_key, account_name=account.name)
