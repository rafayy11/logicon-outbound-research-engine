"""User-provided ICP-screened company list (Court Reporting sheet only).

Separate from manual_ncra_list.py -- this comes from a spreadsheet the
user produced outside this pipeline (Downloads/Logicon_ICP_Company_List
_1.xlsx, "Court Reporting" sheet: 18 companies, revenue/headcount
pre-screened, spot-checked live against 2 real company websites on
2026-08-19 -- both checked out accurately against real page content).

Deliberately imports ONLY company_name/website/city/state -- not the
sheet's own "Clay Verified" / contact-level pilot data (decision maker,
email, reporter_count, etc.). This pipeline re-discovers and re-verifies
every one of those facts itself via real Clay calls; a spreadsheet
produced by a different process, however well-researched, isn't a
substitute for this project's own verification. The sheet only tells us
WHICH companies are worth checking, same role as any other raw source.

Manual import file: data/manual_imports/court_reporting_icp_list.xlsx
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from backend.models.schemas import RawCompany
from backend.utils.normalize import normalize_domain

logger = logging.getLogger(__name__)

MANUAL_IMPORT_PATH = Path("data/manual_imports/court_reporting_icp_list.xlsx")
SHEET_NAME = "Court Reporting"
SOURCE_NAME = "manual_icp_list"


class ManualICPListSource:
    source_name = SOURCE_NAME

    def __init__(self, manual_import_path: Path = MANUAL_IMPORT_PATH):
        self.manual_import_path = manual_import_path

    def load(self) -> list[RawCompany]:
        if not self.manual_import_path.exists():
            logger.info("No manual ICP list at %s -- source will return 0 companies.", self.manual_import_path)
            return []

        try:
            df = pd.read_excel(self.manual_import_path, sheet_name=SHEET_NAME, skiprows=3)
        except Exception as exc:
            logger.warning("Could not parse manual ICP list %s: %s", self.manual_import_path, exc)
            return []

        # Fixed layout confirmed against the real file on 2026-08-19:
        # Company Name, Website, City, State, LinkedIn Employees,
        # Annual Revenue (Clay), Multi-Location?, Source, Fit Notes, Clay Verified.
        if df.shape[1] < 4:
            logger.warning("Manual ICP list %s has unexpected shape %s", self.manual_import_path, df.shape)
            return []
        df.columns = ["company_name", "website", "city", "state"] + list(df.columns[4:])

        companies: list[RawCompany] = []
        for _, row in df.iterrows():
            name = str(row.get("company_name") or "").strip()
            if not name or name.lower() == "nan":
                continue
            website_raw = str(row.get("website") or "").strip()
            domain = normalize_domain(website_raw) if website_raw and website_raw.lower() != "nan" else None
            city = str(row.get("city") or "").strip() or None
            state = str(row.get("state") or "").strip() or None

            companies.append(
                RawCompany(
                    company_name=name,
                    website=f"https://{domain}" if domain else None,
                    domain=domain,
                    city=city if city and city.lower() != "nan" else None,
                    state=state if state and state.lower() != "nan" else None,
                    source=self.source_name,
                    source_url=None,
                    source_identifier=None,
                )
            )
        return companies


def collect_for_campaign(campaign) -> list[RawCompany]:
    """Uniform entry point the engine's source dispatcher calls."""
    return ManualICPListSource().load()
