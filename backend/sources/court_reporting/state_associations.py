"""State court reporter association directories.

The playbook (Part 4) says "almost every state has one with a member
directory" without listing them, and per the source-adapter architecture
rules: prefer structured data, support CSV import when a live automated
adapter isn't verified yet, and don't build fragile scraping against
sites that haven't been confirmed reachable/parseable.

STATE_REGISTRY below tracks what's actually been verified. The original
seed URLs guessed for CA/TX/FL/NY/IL during initial scaffolding were
WRONG (ccra.org, txcra.org, fcrr.org, nyscra.org, ilcra.org don't
resolve to the real associations) -- corrected below against real search
results on 2026-08-18. TX is live via backend.sources.court_reporting.tcra
(a real, confirmed-working adapter, same pattern as NCRA).

CA, FL, and NY all run the same MemberClicks Angular directory-search
widget. Its real backend endpoints WERE found by reading the bundled JS
(POST /ui-directory-search/v2/search-directory/ with a
{"form": {"directory_search_id", "elements"}} body) -- but calling it
returns 401 Unauthorized, and the auth token/mechanism isn't visible
anywhere in the static page or bundle, so cracking it would need live
browser network inspection this session doesn't have tooling for. All
three stay manual-import.

NJ (Certified Court Reporters Association of NJ, ccranj.memberclicks.net)
and AZ (Arizona Court Reporters Association, acraonline.org) were checked
directly: neither exposes ANY public reporter directory at all (member-
services-only sites) -- these aren't "not yet automated", they have no
live source to automate against, so manual-import is the permanent
answer, not a placeholder.

IL is still an unverified guess pending research.

Manual import file: data/manual_imports/court_reporting_state_associations.csv
Columns: company_name,website,domain,phone,address,city,state,country,
         source_state,source_url,source_identifier
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

from backend.models.schemas import RawCompany
from backend.utils.normalize import normalize_domain

logger = logging.getLogger(__name__)

MANUAL_IMPORT_PATH = Path("data/manual_imports/court_reporting_state_associations.csv")

# name / real URL kept for operator reference; status drives behavior.
STATE_REGISTRY: dict[str, dict[str, str]] = {
    "TX": {"name": "Texas Court Reporters Association", "url": "https://www.tcra-online.com", "status": "live"},
    "FL": {"name": "Florida Court Reporters Association", "url": "https://www.fcraonline.org/professional-directory", "status": "manual_import"},
    "CA": {"name": "Deposition Reporters Association of California", "url": "https://www.caldra.org", "status": "manual_import"},
    "NY": {"name": "New York State Court Reporters Association", "url": "https://www.nyscra.org", "status": "manual_import"},
    "NJ": {"name": "Certified Court Reporters Association of New Jersey", "url": "https://ccranj.memberclicks.net", "status": "manual_import"},
    "AZ": {"name": "Arizona Court Reporters Association", "url": "https://www.acraonline.org", "status": "manual_import"},
    "IL": {"name": "Illinois Court Reporters Association", "url": "https://www.ilcra.org", "status": "manual_import"},
}

CSV_COLUMNS = [
    "company_name", "website", "domain", "phone", "address", "city",
    "state", "country", "source_state", "source_url", "source_identifier",
]


class StateAssociationsSource:
    source_name = "state_court_reporter_association"

    def __init__(self, manual_import_path: Path = MANUAL_IMPORT_PATH):
        self.manual_import_path = manual_import_path

    def load_manual_import(self) -> list[RawCompany]:
        if not self.manual_import_path.exists():
            logger.info(
                "No manual import file at %s -- state association source will "
                "return 0 companies until one is provided. See CSV_COLUMNS in "
                "this module for the expected format.",
                self.manual_import_path,
            )
            return []

        companies: list[RawCompany] = []
        with open(self.manual_import_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("company_name") or "").strip()
                if not name:
                    continue
                website = (row.get("website") or "").strip() or None
                domain = normalize_domain(row.get("domain") or website)
                source_state = (row.get("source_state") or row.get("state") or "").strip() or None
                companies.append(
                    RawCompany(
                        company_name=name,
                        website=website,
                        domain=domain,
                        phone=(row.get("phone") or "").strip() or None,
                        address=(row.get("address") or "").strip() or None,
                        city=(row.get("city") or "").strip() or None,
                        state=(row.get("state") or "").strip() or None,
                        country=(row.get("country") or "US").strip() or "US",
                        source=self.source_name,
                        source_url=(row.get("source_url") or "").strip()
                        or STATE_REGISTRY.get(source_state or "", {}).get("url"),
                        source_identifier=(row.get("source_identifier") or "").strip() or None,
                    )
                )
        return companies

    def collect_live(self, state_codes: Optional[list[str]] = None) -> list[RawCompany]:
        """States with a confirmed-working scriptable adapter."""
        live_states = [s for s, info in STATE_REGISTRY.items() if info.get("status") == "live"]
        if state_codes:
            live_states = [s for s in live_states if s in state_codes]

        companies: list[RawCompany] = []
        for state in live_states:
            if state == "TX":
                from backend.sources.court_reporting.tcra import TCRASource

                try:
                    companies.extend(TCRASource().search_state("TX"))
                except Exception as exc:  # one live source failing must not kill collection
                    logger.warning("Live TCRA adapter failed: %s", exc)
        return companies

    def collect(self, state_codes: Optional[list[str]] = None) -> list[RawCompany]:
        companies = self.load_manual_import()
        if state_codes:
            companies = [c for c in companies if c.state in state_codes]
        companies.extend(self.collect_live(state_codes))
        return companies


def collect_for_campaign(campaign) -> list[RawCompany]:
    """Uniform entry point the engine's source dispatcher calls."""
    return StateAssociationsSource().collect()
