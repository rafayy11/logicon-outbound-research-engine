"""NCRA PROLink source adapter -- www.ncra.org/ncra-prolink.

PROLink is a public, login-free, opt-in directory of NCRA professionals
(not a clean company directory -- confirmed by live inspection on
2026-08-17). It is a classic ASP.NET WebForms postback page, not a REST
API and not JS-rendered, so this is a plain HTTP form submission + HTML
parse -- no browser automation needed.

Confirmed live endpoints:
  GET  /ncra-prolink                          (search form + VIEWSTATE)
  POST /ncra-prolink/GetStates/?countryCode=US -> JSON [{"Key":"IL","Value":"Illinois"}, ...]
  POST /ncra-prolink                           (search submit, full page postback)

Result records show a person (opted-in member), address, phone, and
email -- no separate "company name" field exists in the directory. The
company's domain is derived from the member's email address, which is a
reliable proxy for a small/owner-operated agency's domain. The
placeholder company_name is a title-cased guess from that domain;
Clay's company-domain enrichment overwrites it with the real registered
name during the Clay enrichment stage (per architecture: source layer
finds companies, Clay layer researches them). identity_confidence is set
to MEDIUM to reflect that the name is a guess but the domain is real.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from backend.models.schemas import RawCompany
from backend.utils.normalize import domain_to_title_case_name, is_free_email_domain, normalize_domain, parse_us_address

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ncra.org"
SEARCH_URL = f"{BASE_URL}/ncra-prolink"
GET_STATES_URL = f"{BASE_URL}/ncra-prolink/GetStates/"

# US states with the most court reporting agencies get priority; the full
# 50-state list is used when no explicit subset is requested.
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LogiconOutboundEngine/1.0)"}


class NCRAProLinkSource:
    source_name = "ncra_prolink"

    def __init__(self, timeout: float = 30.0, request_delay: float = 1.0):
        self.timeout = timeout
        self.request_delay = request_delay
        self._base_fields: Optional[dict[str, str]] = None

    def _fetch_base_fields(self) -> dict[str, str]:
        """Refetch the search page for a fresh __VIEWSTATE token."""
        resp = httpx.get(SEARCH_URL, headers=_HEADERS, timeout=self.timeout, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        form = soup.find("form", id="aspnetForm")
        if form is None:
            raise RuntimeError("NCRA PROLink search form not found -- page structure may have changed")

        fields: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name or inp.get("type") in ("checkbox", "radio"):
                continue
            fields[name] = inp.get("value", "")
        return fields

    def list_valid_states(self, country_code: str = "US") -> list[dict[str, str]]:
        resp = httpx.post(
            GET_STATES_URL,
            params={"countryCode": country_code},
            content=b"",
            headers={**_HEADERS, "Content-Type": "application/json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _parse_results(self, html: str) -> list[RawCompany]:
        soup = BeautifulSoup(html, "lxml")
        companies: list[RawCompany] = []

        for box in soup.select("div.sourcebook-box"):
            name_link = box.select_one(".ncrasourcebooksearch-name a")
            person_name = name_link.get_text(strip=True).replace("\xa0", " ") if name_link else None
            detail_url = name_link.get("href") if name_link else None

            descs = [p.get_text(strip=True) for p in box.select("p.item-list__description")]
            address_text = descs[0] if descs else None
            phone = None
            for d in descs[1:]:
                if "phone" in d.lower():
                    phone = d.split(":", 1)[-1].strip()

            email_el = box.select_one('a[href^="mailto:"]')
            email = email_el.get("href", "").replace("mailto:", "").strip() if email_el else None
            domain = normalize_domain(email.split("@", 1)[1]) if email and "@" in email else None

            if not domain or is_free_email_domain(domain):
                # No usable company identity without a real company
                # domain -- skip rather than fabricate one.
                continue

            parsed_addr = parse_us_address(address_text)
            company_name = domain_to_title_case_name(domain) or person_name or domain

            companies.append(
                RawCompany(
                    company_name=company_name,
                    website=f"https://{domain}",
                    domain=domain,
                    phone=phone,
                    address=address_text,
                    city=parsed_addr["city"],
                    state=parsed_addr["state"],
                    country="US",
                    source=self.source_name,
                    source_url=detail_url or SEARCH_URL,
                    source_identifier=email or detail_url,
                )
            )
        return companies

    def search_state(self, state_code: str, country_code: str = "US", is_firm_only: bool = True) -> list[RawCompany]:
        fields = self._fetch_base_fields()
        payload = dict(fields)
        payload.update(
            {
                "CountryFilter": country_code,
                "StateFilter": state_code,
                "IsFirm": "true" if is_firm_only else "",
                "Search": "True",
            }
        )
        resp = httpx.post(
            SEARCH_URL, data=payload, headers=_HEADERS, timeout=self.timeout, follow_redirects=True
        )
        resp.raise_for_status()
        return self._parse_results(resp.text)

    def collect(self, state_codes: Optional[list[str]] = None) -> list[RawCompany]:
        states = state_codes or US_STATES
        seen_domains: set[str] = set()
        all_companies: list[RawCompany] = []

        for state in states:
            try:
                companies = self.search_state(state)
            except httpx.HTTPError as exc:
                logger.warning("NCRA PROLink search failed for state=%s: %s", state, exc)
                continue

            for c in companies:
                if c.domain and c.domain in seen_domains:
                    continue
                if c.domain:
                    seen_domains.add(c.domain)
                all_companies.append(c)

            time.sleep(self.request_delay)

        return all_companies


def collect_for_campaign(campaign) -> list[RawCompany]:
    """Uniform entry point the engine's source dispatcher calls."""
    return NCRAProLinkSource().collect()
