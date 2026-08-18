"""Texas Court Reporters Association -- www.tcra-online.com.

The second of the playbook's top-two court reporting sources (NCRA
PROLink + state associations). Confirmed live 2026-08-18: a session-based
CFML (Lucee) member directory, not a REST API and not JS-rendered. The
search form POSTs to the same URL and gets a 302 with a `seed` (search-
session id) in the Location header; the actual results render on a plain
GET of that Location, which is scriptable with two requests and a shared
cookie jar -- no browser automation.

Confirmed live endpoints:
  GET  /?pg=members                                    (search form)
  POST /?pg=members&dirAction=SearchResults             (submits search, 302 -> seeded results URL)
  GET  /?pg=members&dirAction=SearchResults&seed=<id>&memPageNum=<n>  (results page N)

Like NCRA, this is an individual-reporter directory, not a company
directory -- results show name, city/county, and (when the member has
one on file) phone/email. Company identity is derived from the
reporter's email domain, same approach and same caveats as the NCRA
adapter. Searches filter on Type of Reporter = Freelance, since freelance
reporters are the ones working through an agency roster (the playbook's
actual target), rather than Official (court-employed) or Student.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from backend.models.schemas import RawCompany
from backend.sources.court_reporting.ncra import US_STATES
from backend.utils.normalize import domain_to_title_case_name, is_free_email_domain, normalize_domain

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tcra-online.com"
FORM_URL = f"{BASE_URL}/?pg=members"
SEARCH_URL = f"{BASE_URL}/?pg=members&dirAction=SearchResults"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LogiconOutboundEngine/1.0)"}

# mg_gid value for "Freelance" reporter type, read off the live search
# form's <select name="mg_gid"> options on 2026-08-18.
FREELANCE_TYPE_ID = "73731"

_PHONE_RE = re.compile(r"Work Phone:\s*(\d[\d\-\s()]*)")
_NEXT_PAGE_RE = re.compile(r'href="([^"]*memPageNum=\d+[^"]*)"')

MAX_PAGES_PER_STATE = 5


class TCRASource:
    source_name = "tcra_state_association"

    def __init__(self, timeout: float = 30.0, request_delay: float = 1.0):
        self.timeout = timeout
        self.request_delay = request_delay

    def _parse_results(self, html: str, state_code: str) -> tuple[list[RawCompany], Optional[str]]:
        soup = BeautifulSoup(html, "lxml")
        companies: list[RawCompany] = []

        for name_div in soup.select("div.tsAppMemberDirectoryUnlinkedName"):
            row = name_div.find_parent("tr")
            if row is None:
                continue

            email_el = row.select_one('a[href^="mailto:"]')
            email = email_el.get_text(strip=True) if email_el else None
            domain = normalize_domain(email.split("@", 1)[1]) if email and "@" in email else None
            if not domain or is_free_email_domain(domain):
                continue

            locality_el = row.select_one('[itemprop="addressLocality"]')
            city = locality_el.get_text(strip=True) if locality_el else None

            phone_match = _PHONE_RE.search(row.get_text())
            phone = phone_match.group(1).strip() if phone_match else None

            company_name = domain_to_title_case_name(domain)

            companies.append(
                RawCompany(
                    company_name=company_name,
                    website=f"https://{domain}",
                    domain=domain,
                    phone=phone,
                    address=None,
                    city=city,
                    state=state_code,
                    country="US",
                    source=self.source_name,
                    source_url=SEARCH_URL,
                    source_identifier=email,
                )
            )

        next_match = _NEXT_PAGE_RE.search(html)
        next_href = f"{BASE_URL}{next_match.group(1)}" if next_match else None
        return companies, next_href

    def search_state(self, state_code: str) -> list[RawCompany]:
        all_companies: list[RawCompany] = []
        seen_domains: set[str] = set()

        with httpx.Client(headers=_HEADERS, timeout=self.timeout) as client:
            client.get(FORM_URL)  # establishes session cookies the search relies on

            resp = client.post(
                SEARCH_URL,
                data={
                    "m_firstname": "",
                    "m_lastname": "",
                    "m_company": "",
                    "ma_170_city": "",
                    "ma_170_stateprov": state_code,
                    "ma_170_postalcode_radius": "",
                    "ma_170_postalcode": "",
                    "ma_170_county": "",
                    "mg_gid": FREELANCE_TYPE_ID,
                    "fs_match": "c",
                },
            )
            location = resp.headers.get("location")
            if not location:
                return []

            next_url: Optional[str] = f"{BASE_URL}{location}"
            for _ in range(MAX_PAGES_PER_STATE):
                if not next_url:
                    break
                page_resp = client.get(next_url)
                companies, next_url = self._parse_results(page_resp.text, state_code)
                for c in companies:
                    if c.domain in seen_domains:
                        continue
                    seen_domains.add(c.domain)
                    all_companies.append(c)
                time.sleep(self.request_delay)

        return all_companies

    def collect(self, state_codes: Optional[list[str]] = None) -> list[RawCompany]:
        states = state_codes or US_STATES
        seen_domains: set[str] = set()
        all_companies: list[RawCompany] = []

        for state in states:
            try:
                companies = self.search_state(state)
            except httpx.HTTPError as exc:
                logger.warning("TCRA search failed for state=%s: %s", state, exc)
                continue

            for c in companies:
                if c.domain in seen_domains:
                    continue
                seen_domains.add(c.domain)
                all_companies.append(c)

            time.sleep(self.request_delay)

        return all_companies
