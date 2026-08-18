"""User-curated NCRA PROLink company list.

Separate from backend.sources.court_reporting.ncra (the live PROLink
scraper) and from state_associations.py's manual-import file -- this is
a distinct list the user compiled by hand from PROLink search results
(Company Name, Website, Email, NCRA Link columns), provided via a Google
Sheet on 2026-08-18. Kept as its own source module rather than merged
into either existing manual-import file so scripts/source_report.py can
attribute results to it separately and so re-exporting/re-fetching the
sheet later is a straight file replace, no schema translation.

Unlike the live NCRA adapter (which has no real company-name field in
PROLink's results and has to guess one from the domain), every row here
has a real, human-confirmed company name -- so identity here is treated
as fully trusted, not a MEDIUM-confidence guess.

Manual import file: data/manual_imports/court_reporting_ncra_user_curated.csv
Columns (as exported from the sheet): Company Name,Website,Email,NCRA Link
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from backend.models.schemas import RawCompany
from backend.utils.normalize import is_free_email_domain, normalize_domain

logger = logging.getLogger(__name__)

MANUAL_IMPORT_PATH = Path("data/manual_imports/court_reporting_ncra_user_curated.csv")


class ManualNCRAListSource:
    source_name = "manual_ncra_user_curated_list"

    def __init__(self, manual_import_path: Path = MANUAL_IMPORT_PATH):
        self.manual_import_path = manual_import_path

    def load(self) -> list[RawCompany]:
        if not self.manual_import_path.exists():
            logger.info(
                "No manual NCRA list at %s -- source will return 0 companies.",
                self.manual_import_path,
            )
            return []

        companies: list[RawCompany] = []
        with open(self.manual_import_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("Company Name") or "").strip()
                if not name:
                    continue

                website = (row.get("Website") or "").strip() or None
                email = (row.get("Email") or "").strip() or None
                ncra_link = (row.get("NCRA Link") or "").strip() or None

                domain = normalize_domain(website)
                if not domain and email and "@" in email:
                    # No website but a real email -- same fallback the
                    # live NCRA adapter uses, minus the free-mailbox
                    # providers that tell us nothing about the company.
                    candidate = normalize_domain(email.split("@", 1)[1])
                    if candidate and not is_free_email_domain(candidate):
                        domain = candidate

                companies.append(
                    RawCompany(
                        company_name=name,
                        website=website,
                        domain=domain,
                        source=self.source_name,
                        source_url=ncra_link,
                        source_identifier=email or ncra_link,
                    )
                )
        return companies


def collect_for_campaign(campaign) -> list[RawCompany]:
    """Uniform entry point the engine's source dispatcher calls."""
    return ManualNCRAListSource().load()
