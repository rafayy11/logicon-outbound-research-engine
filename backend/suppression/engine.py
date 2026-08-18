"""Local suppression list -- checked before expensive Clay research and
again immediately before export, per the credit-control rules.

data/suppressions.csv columns: type,value,reason,created_at
type in {EMAIL, DOMAIN, COMPANY}
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.models.database import Company, Suppression
from backend.utils.normalize import normalize_company_name, normalize_domain

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/suppressions.csv")
CSV_COLUMNS = ["type", "value", "reason", "created_at"]


class SuppressionEngine:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self.emails: dict[str, str] = {}
        self.domains: dict[str, str] = {}
        self.companies: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            logger.info("No suppression file at %s -- starting with an empty suppression list", self.path)
            return
        with open(self.path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                type_ = (row.get("type") or "").strip().upper()
                value = (row.get("value") or "").strip()
                reason = (row.get("reason") or "").strip() or "suppressed"
                if not value:
                    continue
                if type_ == "EMAIL":
                    self.emails[value.lower()] = reason
                elif type_ == "DOMAIN":
                    dom = normalize_domain(value) or value.lower()
                    self.domains[dom] = reason
                elif type_ == "COMPANY":
                    name = normalize_company_name(value) or value.lower()
                    self.companies[name] = reason

    def check_company(self, company: Company) -> Optional[str]:
        if company.canonical_domain and company.canonical_domain in self.domains:
            return self.domains[company.canonical_domain]
        name_key = normalize_company_name(company.company_name)
        if name_key and name_key in self.companies:
            return self.companies[name_key]
        return None

    def check_email(self, email: str) -> Optional[str]:
        if not email:
            return None
        email_l = email.strip().lower()
        if email_l in self.emails:
            return self.emails[email_l]
        domain = email_l.split("@")[-1] if "@" in email_l else None
        if domain and domain in self.domains:
            return self.domains[domain]
        return None

    def add(self, type_: str, value: str, reason: str) -> None:
        """Append a new suppression entry to the CSV (e.g. after a reply/
        bounce/unsubscribe is recorded externally)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.path.exists()
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(CSV_COLUMNS)
            writer.writerow([type_.upper(), value, reason, datetime.utcnow().isoformat()])
        self._load()
