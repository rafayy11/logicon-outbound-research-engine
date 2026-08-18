"""Named, normalized wrappers around Clay routines.

Everything above this module works with EnrichmentResult / raw research
dicts -- never with Clay's raw JSON response shape directly.

Confirmed live against a real routine run on 2026-08-17:
  GET /routines/run/{id}/results ->
    {"routine_run_id", "status", "total", "finished",
     "data": [{"id": "row-1", "status": "complete", "result": {...}}]}

`result` is keyed by whatever output columns the function's author
selected when publishing it in Clay's UI -- an empty `result: {}` on a
`status: "complete"` item means the function ran but no output column is
mapped, not that Clay looked and found nothing. `_extract_output` treats
that as an error (actionable: check the function's output mapping), not
as a NONE research outcome.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from backend.providers.clay.client import ClayAPIError, ClayClient, ClayError

logger = logging.getLogger(__name__)

_MISSING_FIELD_RE = re.compile(r'Missing required field:\s*([^;"]+)')

# Once a routine's real input-key spelling is learned from a live 400, every
# later call to the SAME routine_id needs the identical correction -- Clay's
# UI-assigned column names don't change mid-run. Without this, every single
# call paid for two HTTP round trips (a guaranteed-fail attempt with our
# generic key, then the corrected retry) for the life of the process --
# confirmed live: 200+ calls to the job_openings and research routines each
# repeated the exact same "domain" -> "Domain" / "prompt" -> "Research
# Question" correction from scratch, every time.
_LEARNED_KEY_CORRECTIONS: dict[str, dict[str, str]] = {}

# Semantically-equivalent names a function author might have used for each
# input we send. Substring matching alone catches "domain" -> "Company
# Domain" but not "prompt" -> "Research Question" -- those aren't
# spelling variants, they're synonyms, so they need to be listed.
_FIELD_ALIASES: dict[str, list[str]] = {
    "domain": ["domain", "website", "company domain", "url", "company website"],
    "prompt": ["prompt", "question", "research question", "instruction", "instructions", "query"],
    "first_name": ["first name", "firstname", "given name"],
    "last_name": ["last name", "lastname", "surname", "family name"],
    "full_name": ["full name", "fullname", "name", "person name"],
    "company_name": ["company name", "companyname", "organization name", "employer"],
    "company_social_url": ["company social url", "company linkedin", "company linkedin url", "linkedin url", "social url"],
}


def _chunk_items(items: list[dict[str, Any]], size: int = 100):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _run_batch_sync_autocorrect(
    client: ClayClient, routine_id: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Batch version of _run_sync_autocorrect: submits up to 100 items in
    ONE Clay call instead of one call per item -- this is what turns a
    25-company firmographic pass or a 7-field company research from N
    sequential round trips into 1. The wrong-input-key problem is
    structural (whoever built the function in Clay's UI named a column a
    certain way), so it's identical for every row in the batch -- a single
    400 on the batch submission is enough to learn the correction for the
    whole batch, same as the single-item version. Larger batches get more
    wall-clock room to finish server-side."""
    learned = _LEARNED_KEY_CORRECTIONS.get(routine_id)
    timeout = max(180.0, 8.0 * len(items))
    if learned:
        pre_corrected = [
            {"id": it["id"], "inputs": {learned.get(k, k): v for k, v in it["inputs"].items()}}
            for it in items
        ]
        try:
            return client.run_routine_sync(routine_id, pre_corrected, timeout=timeout)
        except ClayAPIError as exc:
            if exc.status_code != 400:
                raise
            # The learned mapping no longer applies (function reconfigured
            # in Clay's UI) -- fall through and re-learn from scratch below.

    try:
        return client.run_routine_sync(routine_id, items, timeout=timeout)
    except ClayAPIError as exc:
        if exc.status_code != 400:
            raise
        expected_keys = [k.strip() for k in _MISSING_FIELD_RE.findall(str(exc))]
        if not expected_keys:
            raise

        sample_inputs = items[0]["inputs"]
        corrected_sample = dict(sample_inputs)
        used_inputs: set[str] = set()
        renames: dict[str, str] = {}
        for expected in expected_keys:
            if expected in corrected_sample:
                continue
            expected_l = expected.lower()

            def _matches(k: str) -> bool:
                k_l = k.lower()
                if k_l == expected_l or k_l in expected_l or expected_l in k_l:
                    return True
                return expected_l in _FIELD_ALIASES.get(k_l, [])

            candidate = next((k for k in sample_inputs if k not in used_inputs and _matches(k)), None)
            if candidate is None:
                continue
            used_inputs.add(candidate)
            corrected_sample[expected] = corrected_sample.pop(candidate)
            renames[candidate] = expected

        if not renames:
            raise
        logger.info(
            "Routine %s input keys auto-corrected (batch of %d): %s -> %s",
            routine_id, len(items), list(sample_inputs), list(corrected_sample),
        )
        _LEARNED_KEY_CORRECTIONS[routine_id] = renames
        corrected_items = [
            {"id": it["id"], "inputs": {renames.get(k, k): v for k, v in it["inputs"].items()}}
            for it in items
        ]
        return client.run_routine_sync(routine_id, corrected_items, timeout=timeout)


def _run_sync_autocorrect(
    client: ClayClient, routine_id: str, item_id: str, inputs: dict[str, str]
) -> dict[str, Any]:
    """Each Clay function's input keys are whatever the table columns were
    named by whoever built it in Clay's UI -- naming varies per function
    ("domain" vs "Domain" vs "Company Domain" vs "Research Question" for
    what we call "prompt"). Thin single-item wrapper around
    _run_batch_sync_autocorrect, which does the actual fuzzy/alias
    correction and retry-once; kept separate so single-item call sites
    don't need to build a one-element list themselves."""
    return _run_batch_sync_autocorrect(client, routine_id, [{"id": item_id, "inputs": inputs}])

# Clay-managed function routine ids, read from env. Missing = unavailable,
# never guessed.
MANAGED_ROUTINES = {
    "work_email": "CLAY_ROUTINE_WORK_EMAIL",
    "employee_count": "CLAY_ROUTINE_EMPLOYEE_COUNT",
    "revenue": "CLAY_ROUTINE_REVENUE",
    "techstack": "CLAY_ROUTINE_TECHSTACK",
    "job_openings": "CLAY_ROUTINE_JOB_OPENINGS",
    "company_news": "CLAY_ROUTINE_COMPANY_NEWS",
}

GENERIC_RESEARCH_ROUTINE_ENV = "CLAY_ROUTINE_RESEARCH_GENERIC"
PER_FIELD_RESEARCH_ROUTINE_PREFIX = "CLAY_ROUTINE_RESEARCH_"


def configured_routine_ids() -> dict[str, Optional[str]]:
    ids = {name: os.environ.get(env_var) or None for name, env_var in MANAGED_ROUTINES.items()}
    ids["research_generic"] = os.environ.get(GENERIC_RESEARCH_ROUTINE_ENV) or None
    return ids


def per_field_research_routine_id(field_name: str) -> Optional[str]:
    env_var = f"{PER_FIELD_RESEARCH_ROUTINE_PREFIX}{field_name.upper()}"
    return os.environ.get(env_var) or None


@dataclass
class RoutineOutcome:
    ok: bool
    value: Optional[str] = None
    source_url: Optional[str] = None
    evidence_text: Optional[str] = None
    confidence: str = "NONE"
    raw: Optional[dict] = None
    error: Optional[str] = None


# Confirmed live (2026-08-17) against the Employee Count function: its
# result is a full firmographic record keyed by human-readable column
# names, not a generic "value" field:
#   {"Name", "Domain", "Country Name", "Revenue Band", "Industry Name",
#    "Revenue Total", "Employees Band", "Employees Total"}
# Each managed field looks for its own likely key names (case-insensitive)
# before falling back to the generic value/output/answer/text/single-key
# heuristics, since result shape varies by what the function's author
# named their columns.
MANAGED_FIELD_VALUE_KEYS: dict[str, list[str]] = {
    "employee_count": ["employees total", "employee count", "employees", "headcount"],
    "revenue": ["revenue total", "annual revenue", "revenue"],
    "techstack": ["tech stack", "technologies", "techstack"],
    # Confirmed live 2026-08-18: result is {"summaryOfFindings": "<text>",
    # "breakdownByDepartment": [{"department", "jobs": [...]}]} -- a real,
    # structured job-board result, not a simple scalar. `enrich_field`
    # returns the summary text as `value`; callers that need the raw
    # per-department job title list read it from `RoutineOutcome.raw`.
    "job_openings": ["summaryoffindings", "open jobs", "job openings", "jobs"],
    "company_news": ["news", "recent news"],
}


def _find_by_key_names(result: dict[str, Any], candidate_names: list[str]) -> Any:
    lowered = {k.lower(): v for k, v in result.items()}
    for name in candidate_names:
        if name in lowered and lowered[name] not in (None, ""):
            return lowered[name]
    return None


def _extract_output(item: dict[str, Any], preferred_keys: Optional[list[str]] = None) -> RoutineOutcome:
    """Normalizes one item from the routine-results `data` array:
    {"id", "status", "result": {...}}.

    If the value looks like an explicit "NONE"/"none"/null, that is
    preserved as a real NONE outcome, not an error -- per playbook rule,
    NONE is a valid, expected result.
    """
    item_status = (item.get("status") or "").lower()
    if item_status and item_status not in ("complete", "success", "completed"):
        return RoutineOutcome(
            ok=False,
            error=f"Clay item status={item_status}: {item.get('error') or item}",
            raw=item,
        )

    result = item.get("result")
    if not isinstance(result, dict):
        return RoutineOutcome(ok=False, error=f"Unexpected result shape: {result!r}", raw=item)

    if not result:
        return RoutineOutcome(
            ok=False,
            error=(
                "Clay returned an empty result object -- the function ran but no "
                "output column is mapped. In Clay: open the function -> Outputs "
                "-> select the enrichment column's output field(s)."
            ),
            raw=item,
        )

    value = _find_by_key_names(result, preferred_keys) if preferred_keys else None
    if value is None:
        value = result.get("value")
    if value is None:
        for key in ("output", "answer", "text"):
            if key in result:
                value = result[key]
                break
    if value is None and len(result) == 1:
        value = next(iter(result.values()))  # single unlabeled output field

    source_url = result.get("source_url") or result.get("sourceUrl") or result.get("url")
    evidence_text = result.get("evidence_text") or result.get("evidence") or result.get("snippet")
    confidence = (result.get("confidence") or "").upper() or None

    if value is None or (isinstance(value, str) and value.strip().upper() in ("NONE", "N/A", "")):
        return RoutineOutcome(ok=True, value=None, confidence="NONE", raw=item)

    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        confidence = "MEDIUM" if source_url or evidence_text else "LOW"

    return RoutineOutcome(
        ok=True,
        value=str(value),
        source_url=source_url,
        evidence_text=evidence_text,
        confidence=confidence,
        raw=item,
    )


# Item-level failures (submission succeeds, but a specific row fails
# during processing) don't go through the HTTP-level 429/5xx retry in
# client.py at all. Confirmed live: Clay's "Enrich company" provider can
# fail transiently ("An unexpected error occurred. Please contact
# customer support.") on a call that succeeds moments later with
# identical inputs -- that's provider-side flakiness, not a permanent
# config problem, so it's worth retrying. Permanent/config errors
# ("Missing input", "Missing required field") are never retried -- more
# attempts won't fix a wrong column mapping.
_TRANSIENT_ERROR_MARKERS = [
    "unexpected error", "contact customer support", "internal error",
    "timeout", "timed out", "temporarily unavailable", "try again",
]
_PERMANENT_ERROR_MARKERS = ["missing input", "missing required field", "not configured"]


def _looks_transient(error: str | None) -> bool:
    if not error:
        return False
    err_l = error.lower()
    if any(m in err_l for m in _PERMANENT_ERROR_MARKERS):
        return False
    return any(m in err_l for m in _TRANSIENT_ERROR_MARKERS)


def _call_and_extract(
    client: ClayClient,
    routine_id: str,
    item_id: str,
    inputs: dict[str, str],
    preferred_keys: Optional[list[str]] = None,
    max_retries: int = 2,
    retry_delay: float = 4.0,
) -> RoutineOutcome:
    attempt = 0
    while True:
        attempt += 1
        try:
            data = _run_sync_autocorrect(client, routine_id, item_id, inputs)
        except ClayError as exc:
            return RoutineOutcome(ok=False, error=str(exc))
        results = data.get("data") or []
        if not results:
            outcome = RoutineOutcome(ok=False, error="empty results from Clay")
        else:
            outcome = _extract_output(results[0], preferred_keys=preferred_keys)

        if outcome.ok or attempt > max_retries or not _looks_transient(outcome.error):
            return outcome
        logger.info(
            "Routine %s: transient-looking failure (attempt %d/%d), retrying: %s",
            routine_id, attempt, max_retries + 1, outcome.error,
        )
        time.sleep(retry_delay)


def _call_and_extract_batch(
    client: ClayClient,
    routine_id: str,
    items: list[dict[str, Any]],
    preferred_keys: Optional[list[str]] = None,
    max_retries: int = 2,
    retry_delay: float = 4.0,
) -> dict[str, RoutineOutcome]:
    """Batch version of _call_and_extract: one Clay call for up to 100
    items instead of one call per item. Chunks anything larger than 100
    (Clay's per-call limit). Only items whose failure looks transient are
    retried, and only those items -- not the whole batch -- so one flaky
    row never costs the rest of the batch an extra round trip."""
    outcomes: dict[str, RoutineOutcome] = {}
    for chunk_items in _chunk_items(items, 100):
        pending = {it["id"]: it for it in chunk_items}
        attempt = 0
        while pending:
            attempt += 1
            try:
                data = _run_batch_sync_autocorrect(client, routine_id, list(pending.values()))
            except ClayError as exc:
                for item_id in pending:
                    outcomes[item_id] = RoutineOutcome(ok=False, error=str(exc))
                pending = {}
                break

            results_by_id = {r.get("id"): r for r in (data.get("data") or [])}
            retry_ids: list[str] = []
            for item_id in list(pending.keys()):
                result_item = results_by_id.get(item_id)
                if result_item is None:
                    outcome = RoutineOutcome(ok=False, error="no result returned for this item")
                else:
                    outcome = _extract_output(result_item, preferred_keys=preferred_keys)

                if not outcome.ok and attempt <= max_retries and _looks_transient(outcome.error):
                    retry_ids.append(item_id)
                else:
                    outcomes[item_id] = outcome

            if not retry_ids:
                break
            logger.info(
                "Routine %s: %d/%d item(s) transient-failed (attempt %d/%d), retrying",
                routine_id, len(retry_ids), len(pending), attempt, max_retries + 1,
            )
            time.sleep(retry_delay)
            pending = {i: pending[i] for i in retry_ids}
    return outcomes


class ClayRoutines:
    def __init__(self, client: ClayClient):
        self.client = client
        self.routine_ids = configured_routine_ids()

    # -- firmographic / managed-function enrichment ----------------------

    def is_managed_function_available(self, name: str) -> bool:
        return bool(self.routine_ids.get(name))

    def enrich_field(self, managed_name: str, domain: str, item_id: str = "row-1") -> RoutineOutcome:
        routine_id = self.routine_ids.get(managed_name)
        if not routine_id:
            return RoutineOutcome(ok=False, error=f"routine '{managed_name}' not configured")
        return _call_and_extract(
            self.client, routine_id, item_id, {"domain": domain},
            preferred_keys=MANAGED_FIELD_VALUE_KEYS.get(managed_name),
        )

    def enrich_field_batch(self, managed_name: str, domains_by_id: dict[str, str]) -> dict[str, RoutineOutcome]:
        """Same as enrich_field, but for many companies in one Clay call.
        domains_by_id: item_id (str, typically str(company.id)) -> domain."""
        routine_id = self.routine_ids.get(managed_name)
        if not routine_id:
            error = f"routine '{managed_name}' not configured"
            return {item_id: RoutineOutcome(ok=False, error=error) for item_id in domains_by_id}
        items = [{"id": item_id, "inputs": {"domain": domain}} for item_id, domain in domains_by_id.items()]
        return _call_and_extract_batch(
            self.client, routine_id, items, preferred_keys=MANAGED_FIELD_VALUE_KEYS.get(managed_name)
        )

    def find_work_email(
        self,
        first_name: str,
        last_name: str,
        domain: str,
        company_name: Optional[str] = None,
        company_social_url: Optional[str] = None,
        item_id: str = "row-1",
    ) -> RoutineOutcome:
        routine_id = self.routine_ids.get("work_email")
        if not routine_id:
            return RoutineOutcome(ok=False, error="work_email routine not configured")
        full_name = f"{first_name} {last_name}".strip()
        # Item-level "Missing input" failures (a column inside the
        # function needing an exact key name) happen AFTER submission
        # succeeds, so the submit-time 400 auto-correct never sees them --
        # there's no retry opportunity. Send every plausible spelling of
        # each value up front instead of guessing one; Clay ignores
        # unrecognized extra keys (confirmed empirically), so this is safe.
        inputs = {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "full name": full_name,
            "name": full_name,
            "domain": domain,
        }
        if company_name:
            inputs["company_name"] = company_name
            inputs["Company Name"] = company_name
        if company_social_url:
            inputs["company_social_url"] = company_social_url
            inputs["Company Social url"] = company_social_url
            inputs["linkedin_url"] = company_social_url
        return _call_and_extract(
            self.client, routine_id, item_id, inputs, preferred_keys=["work email", "email"]
        )

    # -- Claygent-style deterministic research ---------------------------

    def research_generic(self, domain: str, prompt: str, item_id: str = "row-1") -> RoutineOutcome:
        routine_id = self.routine_ids.get("research_generic")
        if not routine_id:
            return RoutineOutcome(ok=False, error="generic research routine not configured")
        return _call_and_extract(self.client, routine_id, item_id, {"domain": domain, "prompt": prompt})

    def research_field(self, field_name: str, domain: str, prompt: str, item_id: str = "row-1") -> RoutineOutcome:
        """Try the generic {domain, prompt} function first; fall back to a
        per-field routine id if the generic one isn't configured/reliable."""
        if self.routine_ids.get("research_generic"):
            return self.research_generic(domain, prompt, item_id)
        per_field_id = per_field_research_routine_id(field_name)
        if not per_field_id:
            return RoutineOutcome(
                ok=False, error=f"no research routine configured for '{field_name}'"
            )
        return _call_and_extract(self.client, per_field_id, item_id, {"domain": domain, "prompt": prompt})

    def research_fields_for_company(
        self, domain: str, fields: list[tuple[str, str]]
    ) -> dict[str, RoutineOutcome]:
        """One company's several research fields (reporter_count,
        metro_count, hiring_signal, ...) share the same domain and differ
        only by prompt -- submitted as ONE batched Clay call (one item per
        field, item id = field name) instead of one sequential call per
        field. Confirmed live this was the dominant per-company cost: 7
        sequential round trips for a single Tier A/B company's research.
        fields: list of (field_name, prompt)."""
        routine_id = self.routine_ids.get("research_generic")
        if routine_id:
            items = [
                {"id": field_name, "inputs": {"domain": domain, "prompt": prompt}}
                for field_name, prompt in fields
            ]
            return _call_and_extract_batch(self.client, routine_id, items)

        # No generic function configured -- fall back to per-field routine
        # ids. These are different routines, so they can't be combined
        # into one call; stays sequential.
        out: dict[str, RoutineOutcome] = {}
        for field_name, prompt in fields:
            per_field_id = per_field_research_routine_id(field_name)
            if not per_field_id:
                out[field_name] = RoutineOutcome(
                    ok=False, error=f"no research routine configured for '{field_name}'"
                )
                continue
            out[field_name] = _call_and_extract(
                self.client, per_field_id, field_name, {"domain": domain, "prompt": prompt}
            )
        return out
