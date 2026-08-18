"""Raw HTTP client for Clay's Public API (https://api.clay.com/public/v0).

Confirmed against Clay's published OpenAPI spec (developers.clay.com) on
2026-08-17. This talks to Clay directly over HTTP -- it does not use the
conversational Clay MCP tools, so a campaign run costs zero chat tokens.

Endpoints used:
  GET  /me
  POST /routines/{routine_id}/run
  GET  /routines/run/{routine_run_id}/results
  POST /routines/{routine_id}/run-batch/upload-url
  PUT  {presigned_url}                                  (raw JSONL upload)
  POST /routines/{routine_id}/run-batch/start
  GET  /routines/run-batch/{routine_run_id}/results
  POST /search/query-mode
  GET  /search/query-mode/reference
  POST /search/query-mode/{search_id}/run

Note on credits: the Public API exposes no endpoint to read Clay's actual
account credit balance. "Usage" tracked by this client is a local counter
of API calls/items we issued, not an authoritative balance from Clay --
see backend/credit/budget.py.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

CLAY_BASE_URL = "https://api.clay.com/public/v0"


class ClayError(Exception):
    """Base class for Clay Public API errors."""


class ClayAuthError(ClayError):
    pass


class ClayRateLimitError(ClayError):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after}s")


class ClayQuotaExhaustedError(ClayError):
    """HTTP 402 -- workspace search/result quota exhausted. Retrying will not help."""


class ClayNotFoundError(ClayError):
    pass


class ClayAPIError(ClayError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Clay API error {status_code}: {message}")


class ClayClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = CLAY_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 5,
    ):
        if not api_key:
            raise ClayAuthError("CLAY_API_KEY is not configured")
        self.api_key = api_key
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=base_url,
            headers={"clay-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ClayClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level request with retry/backoff on 429/5xx --------------------

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # A network-level failure (read timeout, connection reset,
                # DNS blip, ...) is not an httpx.Response at all, so none
                # of the status-code handling below ever runs -- left
                # unhandled, this propagates as a raw httpx exception and
                # crashes the entire campaign run instead of just this one
                # call. Confirmed live: a single ReadTimeout during a
                # multi-hour run took down the whole process. Retry with
                # backoff like a 5xx, then surface as ClayAPIError so
                # every caller's existing ClayError handling covers it.
                if attempt >= self.max_retries:
                    raise ClayAPIError(0, f"Network error calling {path}: {exc}") from exc
                time.sleep(min(2 ** attempt, 30))
                continue
            if resp.status_code in (401, 403):
                raise ClayAuthError(f"Authentication failed calling {path}: {resp.text[:300]}")
            if resp.status_code == 402:
                raise ClayQuotaExhaustedError(f"Quota exhausted calling {path}: {resp.text[:300]}")
            if resp.status_code == 404:
                raise ClayNotFoundError(f"Not found: {path}")
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 5))
                if attempt >= self.max_retries:
                    raise ClayRateLimitError(retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise ClayAPIError(resp.status_code, resp.text[:300])
                time.sleep(min(2 ** attempt, 30))
                continue
            if resp.status_code >= 400:
                raise ClayAPIError(resp.status_code, resp.text[:500])
            return resp

    # -- identity -------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/me").json()

    # -- routines ---------------------------------------------------------

    def run_routine(self, routine_id: str, items: list[dict[str, Any]]) -> str:
        """Submit 1-100 items to a routine (Clay-managed or function:t_... custom).
        Returns routine_run_id."""
        if not (1 <= len(items) <= 100):
            raise ValueError("run_routine accepts 1-100 items per call")
        body = {"items": items}
        resp = self._request("POST", f"/routines/{routine_id}/run", json=body)
        data = resp.json()
        run_id = data.get("routine_run_id") or data.get("id")
        if not run_id:
            raise ClayAPIError(resp.status_code, f"No routine_run_id in response: {data}")
        return run_id

    def get_routine_results(self, routine_run_id: str) -> dict[str, Any]:
        resp = self._request("GET", f"/routines/run/{routine_run_id}/results")
        return resp.json()

    def run_routine_sync(
        self,
        routine_id: str,
        items: list[dict[str, Any]],
        poll_interval: float = 2.0,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Submit then poll until every submitted item has a terminal result,
        or timeout.

        For a single-item call, "data" appearing at all used to be an
        adequate completeness check. That stopped being true once callers
        started submitting multiple items in one call (up to 100) to cut
        round trips: Clay's results endpoint can return a "data" array
        containing only the items that have finished SO FAR while others
        are still processing, so checking merely `"data" in data` would
        return a partially-finished batch as if it were done, silently
        dropping the still-pending items. Wait for all submitted ids to
        appear (or the run's own finished/total counters to agree, or the
        top-level status to go terminal) before returning.
        """
        run_id = self.run_routine(routine_id, items)
        expected_ids = {it["id"] for it in items}
        deadline = time.monotonic() + timeout
        while True:
            data = self.get_routine_results(run_id)
            status = data.get("status")
            total = data.get("total")
            finished = data.get("finished")
            entries = data.get("data") or []
            got_ids = {e.get("id") for e in entries}

            terminal_status = status not in ("pending", "running", "in_progress", None)
            counts_agree = finished is not None and total is not None and finished >= total
            all_ids_present = bool(expected_ids) and expected_ids.issubset(got_ids)

            if terminal_status or counts_agree or all_ids_present:
                return data
            if time.monotonic() > deadline:
                raise ClayError(f"Timed out waiting for routine run {run_id}")
            time.sleep(poll_interval)

    # -- batch routines (>100 items) -------------------------------------

    def get_batch_upload_url(self, routine_id: str) -> dict[str, Any]:
        resp = self._request("POST", f"/routines/{routine_id}/run-batch/upload-url", json={})
        return resp.json()

    def upload_batch_jsonl(self, presigned_url: str, jsonl_body: bytes) -> None:
        r = httpx.put(
            presigned_url,
            content=jsonl_body,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=600.0,
        )
        r.raise_for_status()

    def start_batch_run(self, routine_id: str, file_id: str) -> str:
        resp = self._request(
            "POST", f"/routines/{routine_id}/run-batch/start", json={"file_id": file_id}
        )
        data = resp.json()
        return data.get("routine_run_id") or data.get("id")

    def get_batch_results(self, routine_run_id: str) -> dict[str, Any]:
        resp = self._request("GET", f"/routines/run-batch/{routine_run_id}/results")
        return resp.json()

    # -- search (Clay's own GTM database) --------------------------------

    def search_query_reference(self) -> str:
        resp = self._request("GET", "/search/query-mode/reference")
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text

    def create_query_search(self, query: str, source_type: str) -> str:
        body = {"query": query, "source_type": source_type}
        resp = self._request("POST", "/search/query-mode", json=body)
        data = resp.json()
        search_id = data.get("search_id") or data.get("id")
        if not search_id:
            raise ClayAPIError(resp.status_code, f"No search_id in response: {data}")
        return search_id

    def run_query_search(self, search_id: str, page_token: Optional[str] = None) -> dict[str, Any]:
        body = {"page_token": page_token} if page_token else {}
        resp = self._request("POST", f"/search/query-mode/{search_id}/run", json=body)
        return resp.json()

    def iter_query_search(self, query: str, source_type: str, max_results: int = 50):
        """Confirmed live response shape (2026-08-18):
        {"data": [...], "has_more": bool, "source_type", "exhaustion_reason",
         "period_quota": {...}}. No page_token/cursor field exists anywhere
        in the response -- confirmed live that pagination is server-side
        state keyed by search_id itself: calling run_query_search with the
        SAME search_id again returns the next page (verified: two calls
        against an identical search_id returned zero overlapping
        companies). The original version of this method looked for a
        token that doesn't exist and silently stopped after page 1 --
        this was capping every company/people search at ~20 results."""
        search_id = self.create_query_search(query, source_type)
        fetched = 0
        while fetched < max_results:
            page = self.run_query_search(search_id)
            results = page.get("data", [])
            if not results:
                return
            for r in results:
                if fetched >= max_results:
                    return
                fetched += 1
                yield r
            if not page.get("has_more"):
                return
