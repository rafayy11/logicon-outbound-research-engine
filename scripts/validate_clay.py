#!/usr/bin/env python3
"""Clay capability validation -- run this BEFORE the real pipeline.

Confirms, against the live workspace:
  1. Authentication
  2. Company enrichment (a Clay-managed function, if configured)
  3. Company research (the generic {domain, prompt} custom function, if configured)
  4. People search (Clay's GTM database, query-mode)
  5. Person enrichment / decision-maker fields (via the same people search)
  6. Work-email discovery (Clay-managed function, if configured)
  7. Custom research routine capability (generic vs. per-field)

Never invents an endpoint or a routine id. Anything not configured in
.env is reported as UNAVAILABLE, not guessed.

Usage:
    python scripts/validate_clay.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from backend.providers.clay.client import ClayClient, ClayError  # noqa: E402
from backend.providers.clay.routines import (  # noqa: E402
    MANAGED_ROUTINES,
    ClayRoutines,
    configured_routine_ids,
)
from backend.providers.clay.search import ClaySearch  # noqa: E402

TEST_DOMAIN = os.environ.get("CLAY_VALIDATE_TEST_DOMAIN", "clay.com")
TEST_TITLES = ["sales", "operations", "marketing"]  # broad, just to prove search returns rows


def line(title: str) -> None:
    print(f"\n=== {title} ===")


def result(label: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "UNAVAILABLE"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def main() -> int:
    api_key = os.environ.get("CLAY_API_KEY")
    report: dict[str, bool] = {}

    line("1. Authentication")
    if not api_key:
        result("CLAY_API_KEY", False, "not set in .env")
        print("\nCannot proceed further without an API key.")
        print("Create one: Clay -> Settings -> Account -> API keys (beta)")
        return 1

    client = ClayClient(api_key=api_key)
    try:
        me = client.get_me()
        result("GET /me", True, json.dumps(me)[:200])
        report["auth"] = True
    except ClayError as exc:
        result("GET /me", False, str(exc))
        print("\nAuthentication failed -- stopping here.")
        return 1

    line("Configured routine ids (from .env)")
    ids = configured_routine_ids()
    for name, routine_id in ids.items():
        print(f"  {name:20s} {'set: ' + routine_id if routine_id else '(not configured)'}")

    routines = ClayRoutines(client)

    line("2. Company enrichment (Clay-managed functions)")
    any_managed_ok = False
    for name in MANAGED_ROUTINES:
        if not routines.is_managed_function_available(name):
            result(f"managed function: {name}", False, "no routine id in .env")
            continue
        outcome = routines.enrich_field(name, TEST_DOMAIN)
        ok = outcome.ok
        any_managed_ok = any_managed_ok or ok
        detail = outcome.error or f"value={outcome.value!r} raw_keys={list((outcome.raw or {}).keys())}"
        result(f"managed function: {name} (domain={TEST_DOMAIN})", ok, detail)
    report["company_enrichment"] = any_managed_ok

    line("3 & 7. Company research (custom Claygent function)")
    test_prompt = (
        "Visit this company's website. Return the word TEST followed by "
        "today's understanding of what the company does, in one sentence. "
        "If the website cannot be determined, return NONE."
    )
    if ids.get("research_generic"):
        outcome = routines.research_generic(TEST_DOMAIN, test_prompt)
        result("generic {domain, prompt} custom function", outcome.ok, outcome.error or f"value={outcome.value!r}")
        report["company_research"] = outcome.ok
        report["research_mode"] = "generic" if outcome.ok else "unavailable"
    else:
        result("generic {domain, prompt} custom function", False, "CLAY_ROUTINE_RESEARCH_GENERIC not set")
        print("  Falling back to checking per-field research routines (CLAY_ROUTINE_RESEARCH_*)...")
        from backend.providers.clay.routines import per_field_research_routine_id

        any_field_ok = False
        for field in ["reporter_count", "metro_count", "open_scheduler_roles", "client_portal"]:
            rid = per_field_research_routine_id(field)
            if not rid:
                result(f"per-field routine: {field}", False, "not configured")
                continue
            outcome = routines.research_field(field, TEST_DOMAIN, test_prompt)
            any_field_ok = any_field_ok or outcome.ok
            result(f"per-field routine: {field}", outcome.ok, outcome.error or f"value={outcome.value!r}")
        report["company_research"] = any_field_ok
        report["research_mode"] = "per_field" if any_field_ok else "unavailable"

    line("4 & 5. People search / person data (Clay GTM database, query-mode)")
    search = ClaySearch(client)
    people: list = []
    try:
        people = search.find_people_at_company(TEST_DOMAIN, TEST_TITLES, max_results=5)
        result(
            "search/query-mode people search",
            True,
            f"{len(people)} people returned for domain={TEST_DOMAIN}",
        )
        if people:
            print(f"  sample: {people[0].model_dump()}")
        report["people_search"] = True
    except ClayError as exc:
        result("search/query-mode people search", False, str(exc))
        report["people_search"] = False

    line("6. Work-email discovery")
    if routines.is_managed_function_available("work_email") and people:
        p = people[0]
        outcome = routines.find_work_email(p.first_name or "Test", p.last_name or "Person", TEST_DOMAIN)
        result("work_email managed function", outcome.ok, outcome.error or f"value={outcome.value!r}")
        report["work_email"] = outcome.ok
    else:
        result("work_email managed function", False, "CLAY_ROUTINE_WORK_EMAIL not set, or no test person available")
        report["work_email"] = False

    line("Summary")
    for k, v in report.items():
        print(f"  {k}: {v}")

    unavailable = [k for k, v in report.items() if v is False]
    if unavailable:
        print(f"\nUnavailable capabilities: {', '.join(unavailable)}")
        print("These are NOT invented/worked around -- see .env.example for the routine ids to add,")
        print("and README.md for the Clay-UI custom function setup steps.")
    else:
        print("\nAll checked capabilities available.")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
