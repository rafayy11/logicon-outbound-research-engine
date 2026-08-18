"""Free, zero-Clay-credit pre-screen used only to ORDER a batch, never to
disqualify. A single plain HTTP fetch of the company's own homepage,
scanned for coordinator-shaped language reused from the campaign's own
coordinator_title_candidates -- no new keyword list invented. A miss or
a fetch failure is inconclusive (None), not a negative: plenty of real
qualified companies just don't mention "scheduler" on their homepage
copy. Used to push likely-fits to the front of the Clay-spending queue,
never to hold anyone back permanently.

Must stay fast across a full batch (tens to low hundreds of companies),
so every fetch runs concurrently with a short per-request timeout --
same ThreadPoolExecutor/as_completed pattern already used for parallel
source collection in backend/campaigns/engine.py.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LogiconOutboundEngine/1.0)"}

# Coordinator titles are phrases ("scheduling coordinator"); a homepage
# is far more likely to contain a stem ("scheduling team", "our
# coordinators") than the exact phrase, so match on stems, not whole titles.
_STEM_RE = re.compile(r"[a-zA-Z]+")


def _stems(titles: list[str]) -> set[str]:
    stems: set[str] = set()
    for title in titles:
        words = _STEM_RE.findall(title.lower())
        stems.update(w for w in words if len(w) >= 5)  # drop noise like "of"/"and"
    return stems


def prescreen_company(
    website: Optional[str], coordinator_titles: list[str], timeout: float = 4.0
) -> Optional[bool]:
    """True = homepage text contains coordinator-shaped language. False =
    fetched cleanly, no hit. None = couldn't fetch (timeout, DNS, 4xx/5xx,
    no website at all) -- inconclusive, treated the same as False for
    ordering purposes but logged separately so a real negative isn't
    confused with "we never checked"."""
    if not website:
        return None
    try:
        resp = httpx.get(website, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None

    text = resp.text.lower()
    return any(stem in text for stem in _stems(coordinator_titles))


def prescreen_batch(
    items: list[tuple[int, Optional[str]]],
    coordinator_titles: list[str],
    max_workers: int = 20,
    timeout: float = 4.0,
) -> dict[int, Optional[bool]]:
    """items: list of (company_id, website). Returns company_id -> result.
    Runs the whole batch concurrently so this stays fast (a 4s timeout
    across 20 parallel workers means even ~100 companies resolve in well
    under a minute, not 100 * several seconds)."""
    results: dict[int, Optional[bool]] = {}
    if not items:
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(prescreen_company, website, coordinator_titles, timeout): company_id
            for company_id, website in items
        }
        for future in as_completed(futures):
            company_id = futures[future]
            try:
                results[company_id] = future.result()
            except Exception as exc:  # a pre-screen fetch failing must never break the run
                logger.warning("Pre-screen failed for company_id=%s: %s", company_id, exc)
                results[company_id] = None

    hits = sum(1 for v in results.values() if v is True)
    logger.info("Pre-screen: %d/%d companies show coordinator-shaped homepage language", hits, len(results))
    return results
