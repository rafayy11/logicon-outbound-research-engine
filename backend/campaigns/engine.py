"""Single reusable pipeline. Campaign differences come entirely from
CampaignConfig -- this module never branches on campaign name.

Order matches the build instruction's cheap-first, hard-stop, tier-aware
rules: sources -> normalize/dedupe -> suppress -> firmographic filter
(hard stop here) -> research (priority order) -> coordinators -> tier
(Tier C stops here) -> decision maker -> employment -> email ->
personalization -> exports.
"""

from __future__ import annotations

import importlib
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.campaigns.configs.base import CampaignConfig
from backend.credit.batching import BatchController, chunk
from backend.credit.budget import CreditBudget
from backend.exports.csv import (
    gather_pipeline_records,
    is_ready,
    personalization_gap_reason,
    write_qa_sample_csv,
    write_ready_csv,
    write_rejected_csv,
    write_research_csv,
)
from backend.models.database import (
    Campaign,
    CampaignRun,
    Company,
    CompanySource,
    ProviderError,
    Qualification,
    get_session,
    init_db,
)
from backend.models.schemas import CampaignRunStats, CompanyStatus, RawCompany, RejectedCompany
from backend.providers.clay.accounts import active_account
from backend.providers.clay.client import ClayClient
from backend.providers.clay.routines import ClayRoutines
from backend.providers.clay.search import ClaySearch
from backend.qualification.coordinator import (
    fetch_people_at_company,
    persist_coordinators,
    qualified_coordinator_count,
)
from backend.qualification.firmographics import enrich_job_openings_for_company, filter_companies
from backend.qualification.prescreen import prescreen_batch
from backend.qualification.tiers import qualify_and_tier
from backend.contacts.employment import verify_employment
from backend.contacts.email import find_work_email
from backend.contacts.people import discover_decision_maker
from backend.research.engine import research_company
from backend.research.evidence import detect_signals
from backend.suppression.engine import SuppressionEngine
from backend.utils.dedup import import_raw_companies

logger = logging.getLogger(__name__)


def _run_one_source(source_ref, campaign: CampaignConfig) -> list[RawCompany]:
    try:
        module = importlib.import_module(source_ref.module)
    except ImportError as exc:
        logger.warning("Could not import source module %s: %s", source_ref.module, exc)
        return []

    collect_fn = getattr(module, "collect_for_campaign", None)
    if collect_fn is None:
        logger.warning("Source module %s has no collect_for_campaign()", source_ref.module)
        return []

    try:
        companies = collect_fn(campaign)
    except Exception as exc:  # one bad source must not kill the run
        level = logging.INFO if source_ref.optional else logging.WARNING
        logger.log(level, "Source %s failed/unavailable: %s", source_ref.module, exc)
        return []

    logger.info("Source %s -> %d raw companies", source_ref.module, len(companies))
    return companies


def collect_raw_companies(campaign: CampaignConfig) -> list[RawCompany]:
    """Sources are independent, I/O-bound network calls to unrelated
    services (NCRA, TCRA, Clay, Google) -- fetched concurrently rather
    than one after another, since none of them depend on another's
    output. Cuts collection wall-clock time to roughly the slowest single
    source instead of the sum of all of them."""
    enabled = [s for s in campaign.sources if s.enabled]
    if not enabled:
        return []

    all_companies: list[RawCompany] = []
    with ThreadPoolExecutor(max_workers=len(enabled)) as pool:
        futures = {pool.submit(_run_one_source, source_ref, campaign): source_ref for source_ref in enabled}
        for future in as_completed(futures):
            all_companies.extend(future.result())

    return all_companies


# Clay ICP search pre-filters to company_size 11-50/51-200; the manual
# ICP list is pre-screened for revenue + a 20-200 employee sweet spot
# (spot-checked live against real company sites on 2026-08-19). Both are
# tightly ICP-matched. The other sources (NCRA PROLink, state
# associations, the user's raw curated list) skew heavily toward 1-3
# person solo operators who structurally can't have a distinct
# coordination role. Companies from these sources get processed first so
# Clay spend concentrates where the coordinator hit-rate is naturally higher.
_HIGH_PRIORITY_SOURCES = {"clay_icp_search", "manual_icp_list"}


def _build_priority_map(session: Session, companies: list[Company], campaign: CampaignConfig) -> dict[int, tuple[int, int]]:
    """Lower tuple = processed earlier. (source_rank, prescreen_rank):
    source_rank 0 = Clay-ICP-sourced, 1 = everything else. prescreen_rank
    0 = homepage shows coordinator-shaped language, 1 = miss/inconclusive.
    Neither signal disqualifies -- a company at the back of the queue
    still gets processed once budget/time allows, just not first."""
    company_ids = [c.id for c in companies]
    sources_by_company: dict[int, set[str]] = {}
    if company_ids:
        for cs in session.query(CompanySource).filter(CompanySource.company_id.in_(company_ids)).all():
            sources_by_company.setdefault(cs.company_id, set()).add(cs.source)

    prescreen_items = [(c.id, c.website) for c in companies]
    prescreen_results = prescreen_batch(prescreen_items, campaign.coordinator_title_candidates)

    priority: dict[int, tuple[int, int]] = {}
    for c in companies:
        source_rank = 0 if sources_by_company.get(c.id, set()) & _HIGH_PRIORITY_SOURCES else 1
        prescreen_rank = 0 if prescreen_results.get(c.id) is True else 1
        priority[c.id] = (source_rank, prescreen_rank)
    return priority


def _ensure_campaign_row(session: Session, campaign: CampaignConfig) -> Campaign:
    row = session.query(Campaign).filter(Campaign.key == campaign.key).one_or_none()
    if row is None:
        row = Campaign(key=campaign.key, name=campaign.name, created_at=datetime.utcnow())
        session.add(row)
        session.commit()
    return row


def _current_ready_count(session: Session, campaign_key: str, coverage_field: str, tiers: tuple[str, ...] = ("A", "B")) -> int:
    """Must mirror exports/csv.py's personalization_gap_reason() exactly,
    or this undercounts remaining work: confirmed live, without the
    coverage_field check this returned 10 (Tier A/B + verified + email
    found) while only 6 of those 10 actually had a non-NONE coverage_field
    value and could pass personalization_gap_reason at export time -- the
    run stopped sourcing new batches 4 real prospects short of the
    requested target because this didn't check the same gate the export
    step does."""
    from backend.models.database import DecisionMakerStatusRecord
    from backend.models.database import ResearchResult as ResearchResultRow

    return (
        session.query(Qualification)
        .join(DecisionMakerStatusRecord, DecisionMakerStatusRecord.company_id == Qualification.company_id)
        .join(
            ResearchResultRow,
            (ResearchResultRow.company_id == Qualification.company_id)
            & (ResearchResultRow.campaign_id == campaign_key)
            & (ResearchResultRow.field_name == coverage_field),
        )
        .filter(
            Qualification.campaign_id == campaign_key,
            Qualification.tier.in_(list(tiers)),
            DecisionMakerStatusRecord.campaign_id == campaign_key,
            DecisionMakerStatusRecord.employment_verified == True,  # noqa: E712
            DecisionMakerStatusRecord.email_status == "FOUND",
            ResearchResultRow.value.isnot(None),
        )
        .count()
    )


def _qualify_company(
    session: Session,
    company: Company,
    campaign: CampaignConfig,
    clay_routines: ClayRoutines,
    budget: CreditBudget,
    stats: CampaignRunStats,
    rejected: list[RejectedCompany],
    run_row: CampaignRun,
    people_results: Optional[list],
) -> Optional[Qualification]:
    """Stage 1 (the cheap, ICP-fit-determining stage): coordinator search
    (a free Clay *search* call, not enrichment credit) -> tier -> (Tier
    A/B only) job openings. Returns the Qualification row if the company
    reached Tier A or B (i.e. is worth spending stage-2 enrichment credit
    on), None otherwise -- disqualified, Tier C (parked), or a provider
    error sent to manual review.

    Spends NOTHING from Clay's scarce enrichment-credit pool for the
    ~86-89% of companies that don't reach Tier A/B on this dataset --
    coordinator search is billed
    against Clay's separate, effectively-unlimited search quota
    (confirmed live 2026-08-19: 1,000,000/year, ~7k used). This is what
    makes it safe to run this stage across every collected company
    without worrying about the enrichment budget at all; _enrich_company
    (job openings, research, decision maker, email) is the stage that
    actually spends real credit, and only ever runs on what this
    function returns.

    people_results is pre-fetched by the caller (fetch_people_at_company,
    run concurrently across the whole batch) -- only the DB-writing half
    (persist_coordinators) happens here, since SQLAlchemy sessions aren't
    safe to write to from multiple threads."""
    stats.coordinator_candidates += 1

    existing_qualification = (
        session.query(Qualification)
        .filter(Qualification.company_id == company.id, Qualification.campaign_id == campaign.key)
        .one_or_none()
    )

    if existing_qualification is not None:
        # Already coordinator-searched and tiered in a prior run --
        # reusing this avoids both a wasted Clay credit and duplicate
        # Person/CoordinatorClassification rows for the same real people
        # (persist_coordinators has no idempotency key, unlike every
        # other Clay-touching stage in this pipeline -- confirmed live:
        # CompanyStatus.EXPORTED is defined but never actually set
        # anywhere, so nothing ever marked a qualified company "done" and
        # every re-run was re-searching and re-spending on it).
        #
        # Also corrects a stale company.status: a company can have a
        # perfectly valid Qualification from an earlier run but still
        # show status=MANUAL_REVIEW because a LATER, unrelated step
        # (e.g. a research-field timeout) overwrote it -- confirmed
        # live, 26 of 41 "manual review" companies already had a real
        # tiered Qualification and just never had their status corrected
        # back, since this reuse path never used to touch company.status
        # at all.
        qualification = existing_qualification
        if qualification.coordinator_count > 0:
            stats.coordinator_qualified += 1
        company.status = (
            CompanyStatus.DISQUALIFIED.value if qualification.tier is None else CompanyStatus.QUALIFIED.value
        )
        session.commit()
    else:
        if people_results is None:
            # Provider failure (rate limit, quota, network), not a genuine
            # zero-result search -- must never be treated as "0 qualified
            # coordinators found" (that would silently disqualify a company
            # we never actually got an answer for). MANUAL_REVIEW leaves it
            # eligible for retry on the next run instead of a permanent call.
            session.add(
                ProviderError(
                    run_id=run_row.id,
                    company_id=company.id,
                    operation="coordinator_search",
                    provider="clay",
                    error="Coordinator search failed (rate-limited or provider error) -- not counted as zero",
                    occurred_at=datetime.utcnow(),
                )
            )
            company.status = CompanyStatus.MANUAL_REVIEW.value
            session.commit()
            rejected.append(
                RejectedCompany(
                    company=company.company_name,
                    domain=company.canonical_domain,
                    source=None,
                    rejection_stage="coordinator_search",
                    rejection_reason="Coordinator search failed (provider error) -- will retry on next run, not disqualified",
                )
            )
            return None

        persist_coordinators(session, company, campaign, people_results)
        budget.record("coordinator_search", 1)

        count = qualified_coordinator_count(session, company.id, campaign.key)
        if count > 0:
            stats.coordinator_qualified += 1
        qualification = qualify_and_tier(session, company, campaign.key, count)

    if qualification.tier == "A":
        stats.tier_a += 1
    elif qualification.tier == "B":
        stats.tier_b += 1
    elif qualification.tier == "C":
        stats.tier_c += 1
    else:
        rejected.append(
            RejectedCompany(
                company=company.company_name,
                domain=company.canonical_domain,
                source=None,
                rejection_stage="coordinator_qualification",
                rejection_reason=qualification.disqualification_reason or "No qualified coordinators found",
            )
        )
        return None  # disqualified -- no coordinators, stop here -- no enrichment spend

    if qualification.tier not in ("A", "B"):
        rejected.append(
            RejectedCompany(
                company=company.company_name,
                domain=company.canonical_domain,
                source=None,
                rejection_stage="tiering",
                rejection_reason=f"Tier C ({qualification.coordinator_count} qualified coordinator(s)) -- parked, not sequenced",
            )
        )
        return None  # Tier C stops here -- no enrichment spend

    # job_openings is grouped with stage 1 (qualify), not stage 2 -- per
    # explicit direction, "worth it" is determined by employee_count +
    # coordinator_search + job_openings together, all in the same early
    # pass, before any of the expensive per-field research/decision-
    # maker/email spend. Still gated on Tier A/B (not run for Tier C or
    # disqualified companies) so this doesn't multiply spend across the
    # whole firmographically-passed pool -- only ~11-14% of that pool
    # ever reaches Tier A/B on this dataset.
    try:
        enrich_job_openings_for_company(session, company, campaign.key, clay_routines)
        budget.record("job_openings", 1)
    except Exception as exc:  # optional evidence -- never blocks qualification
        logger.warning("job_openings enrichment failed for %s: %s", company.company_name, exc)

    return qualification  # Tier A/B -- worth spending enrichment credit on


def _enrich_qualified_company(
    session: Session,
    company: Company,
    campaign: CampaignConfig,
    clay_routines: ClayRoutines,
    clay_search: ClaySearch,
    budget: CreditBudget,
    stats: CampaignRunStats,
    rejected: list[RejectedCompany],
    run_row: CampaignRun,
    research_run_id: str,
) -> None:
    """Stage 2 (the expensive stage): the 7-field research batch +
    decision maker + employment verification + email -- only ever called
    for a company _qualify_company already confirmed Tier A/B (job
    openings already ran as part of that stage 1 call). This is
    deliberately a separate function (not just a later part of the same
    call) so it can be invoked on its own, in a later run, against
    companies already sitting at Tier A/B in the database -- the --stage
    enrich CLI mode does exactly that, skipping collection/firmographics/
    coordinator-search/job-openings entirely and spending real
    enrichment credit only on companies already confirmed worth it."""
    stats.research_candidates += 1
    research_company(session, company, campaign, clay_routines, run_row.id, research_run_id)
    stats.research_completed += 1
    budget.record("research", len(campaign.research_fields))
    detect_signals(session, company.id, campaign)

    dm, dm_reject_reason = discover_decision_maker(session, company, campaign, clay_search)
    budget.record("decision_maker_search", 1)
    if dm is None:
        if dm_reject_reason:
            rejected.append(RejectedCompany(company=company.company_name, domain=company.canonical_domain, source=None, rejection_stage="decision_maker", rejection_reason=dm_reject_reason))
        return
    stats.decision_makers += 1

    verified = verify_employment(session, dm, company)
    if not verified:
        rejected.append(RejectedCompany(company=company.company_name, domain=company.canonical_domain, source=None, rejection_stage="employment_verification", rejection_reason="current_company_domain did not match canonical_domain"))
        return
    stats.employment_verified_count += 1

    if not company.linkedin_url:
        company.linkedin_url = clay_search.find_company_linkedin_url(
            company.canonical_domain, prefer_name=company.company_name
        )
        budget.record("company_lookup", 1)
        session.commit()

    email, email_status = find_work_email(
        session, dm, clay_routines, company.company_name, company.linkedin_url
    )
    budget.record("email_lookup", 1)
    if email_status == "FOUND":
        stats.usable_emails += 1
    else:
        rejected.append(RejectedCompany(company=company.company_name, domain=company.canonical_domain, source=None, rejection_stage="email_discovery", rejection_reason="No usable work email found"))


def _process_qualified_company(
    session: Session,
    company: Company,
    campaign: CampaignConfig,
    clay_routines: ClayRoutines,
    clay_search: ClaySearch,
    budget: CreditBudget,
    stats: CampaignRunStats,
    rejected: list[RejectedCompany],
    run_row: CampaignRun,
    research_run_id: str,
    people_results: Optional[list],
) -> None:
    """--stage full: qualify then, only if Tier A/B, immediately enrich
    in the same pass. Kept as one call for the default/simple path;
    --stage qualify and --stage enrich call _qualify_company and
    _enrich_qualified_company separately instead."""
    qualification = _qualify_company(session, company, campaign, clay_routines, budget, stats, rejected, run_row, people_results)
    if qualification is not None:
        _enrich_qualified_company(session, company, campaign, clay_routines, clay_search, budget, stats, rejected, run_row, research_run_id)


def run_pipeline(campaign: CampaignConfig, target: int, stage: str = "full", enrich_tiers: tuple[str, ...] = ("A", "B")) -> CampaignRunStats:
    """stage:
      "qualify" -- sources through coordinator search + tiering only.
                   Spends nothing from Clay's scarce enrichment-credit
                   pool (coordinator search bills against the separate,
                   effectively-unlimited search quota). Use this to
                   build/refresh the Tier A/B/C list against existing or
                   newly-collected companies without committing any real
                   credit.
      "enrich"  -- skips collection/firmographics/coordinator-search
                   entirely and runs job openings + research + decision
                   maker + employment + email ONLY for companies already
                   at a tier in `enrich_tiers` (default Tier A/B; pass
                   ("A","B","C") to also spend on Tier C) in the database
                   from a prior "qualify" (or "full") run. This is the
                   stage that actually spends real enrichment credit --
                   run it deliberately, once you've reviewed the
                   qualify-stage results. Note this spends real credit
                   REGARDLESS of whether Claygent finds an answer --
                   there's no way to know in advance whether a Tier C
                   company's research fields will come back NONE, so
                   including Tier C is a real bet on hit rate, not free
                   upside.
      "full"    -- both, per company, in one pass (original behavior);
                   enrich_tiers has no effect here, Tier C is always
                   parked with no spend in this mode.
    """
    if stage not in ("qualify", "enrich", "full"):
        raise ValueError(f"stage must be 'qualify', 'enrich', or 'full', got {stage!r}")

    init_db()
    session = get_session()
    _ensure_campaign_row(session, campaign)

    stats = CampaignRunStats(campaign=campaign.key, target_count=target)
    run_row = CampaignRun(campaign_id=campaign.key, target_count=target, buffer_pct=15.0, status="RUNNING")
    session.add(run_row)
    session.commit()

    account = active_account()
    api_key = account.api_key
    clay_client = ClayClient(api_key=api_key) if api_key else None
    clay_routines = ClayRoutines(clay_client) if clay_client else None
    clay_search = ClaySearch(clay_client) if clay_client else None
    if clay_client is None:
        logger.error("No API key configured for Clay account %r -- cannot run Clay research/coordinator/decision-maker/email stages", account.name)
    else:
        logger.info("Using Clay account: %s", account.name)

    budget = CreditBudget.from_env()
    batcher = BatchController.from_env(target)
    suppression = SuppressionEngine()
    research_run_id = str(uuid.uuid4())

    rejected: list[RejectedCompany] = []

    if stage == "enrich":
        # Skip collection/firmographics/coordinator-search entirely --
        # work only from companies already tiered in the database.
        # "we have almost no credits, build a list with the existing
        # one": this is exactly that -- zero new raw companies, zero new
        # coordinator searches, spend only goes toward turning already-
        # confirmed companies into real contacts + emails.
        stats.raw_companies = 0
        stats.deduplicated = 0
        tiered = (
            session.query(Company)
            .join(Qualification, Qualification.company_id == Company.id)
            .filter(Qualification.campaign_id == campaign.key, Qualification.tier.in_(list(enrich_tiers)))
            .all()
        )
        logger.info("Enrich stage: %d Tier %s companies to process (from prior qualify runs)", len(tiered), "/".join(enrich_tiers))
        if clay_client is not None:
            for company in tiered:
                if not budget.has_budget_for(len(campaign.research_fields) + 3):
                    logger.warning("Clay budget insufficient to continue enrich stage (remaining %s) -- stopping", budget.remaining)
                    break
                try:
                    _enrich_qualified_company(session, company, campaign, clay_routines, clay_search, budget, stats, rejected, run_row, research_run_id)
                except Exception as exc:
                    logger.exception("Unhandled error enriching %s -- logging and continuing", company.company_name)
                    session.rollback()
                    session.add(ProviderError(run_id=run_row.id, company_id=company.id, operation="enrich_qualified_company", provider="pipeline", error=str(exc), occurred_at=datetime.utcnow()))
                    company.status = CompanyStatus.MANUAL_REVIEW.value
                    session.commit()
        companies = []  # nothing further to collect/filter below

    else:
        # -- 1-4: sources, normalize, dedupe --------------------------------
        raw_companies = collect_raw_companies(campaign)
        stats.raw_companies = len(raw_companies)

        companies = import_raw_companies(session, raw_companies)
        stats.deduplicated = len(companies)

        # -- 5: suppression check --------------------------------------------
        surviving: list[Company] = []
        for c in companies:
            reason = suppression.check_company(c)
            if reason:
                stats.suppressed += 1
                rejected.append(RejectedCompany(company=c.company_name, domain=c.canonical_domain, source=None, rejection_stage="suppression", rejection_reason=reason))
            else:
                surviving.append(c)
        companies = surviving

        # A company already DISQUALIFIED or EXPORTED in a prior run has a
        # final, real-business-reason answer -- re-running it would just
        # re-spend Clay credits to reach the same conclusion. MANUAL_REVIEW is
        # NOT skipped here: that status means a provider call failed
        # transiently last time (e.g. Clay's "unexpected error"), and it's
        # exactly what should get a fresh attempt on the next run.
        _TERMINAL_STATUSES = {CompanyStatus.DISQUALIFIED.value, CompanyStatus.EXPORTED.value}
        already_decided = [c for c in companies if c.status in _TERMINAL_STATUSES]
        if already_decided:
            logger.info(
                "Skipping %d compan(ies) already decided in a prior run: %s",
                len(already_decided), [c.company_name for c in already_decided],
            )
        companies = [c for c in companies if c.status not in _TERMINAL_STATUSES]

        # Order the queue so Clay spend concentrates on companies most likely
        # to qualify: Clay-ICP-sourced + coordinator-shaped homepage language
        # first, small-shop sources with no positive signal last. Both checks
        # are free (source is already-known metadata; the pre-screen is a
        # plain HTTP fetch, no Clay credit) and run once for the whole queue,
        # not per-batch.
        priority = _build_priority_map(session, companies, campaign)
        companies.sort(key=lambda c: priority[c.id])

    # -- 6-14: batched cheap-first pipeline (stage in "qualify"/"full" only) --
    if stage != "enrich" and clay_client is not None:
        for batch in chunk(companies, batcher.batch_size):
            ready_so_far = _current_ready_count(session, campaign.key, campaign.coverage_field)
            if not batcher.should_launch_next_batch(ready_so_far):
                logger.info("Target reached (%d ready >= target %d) -- stopping further batches", ready_so_far, target)
                break

            estimated_batch_cost = len(batch) * (len(campaign.research_fields) + 3)
            if not budget.has_budget_for(estimated_batch_cost):
                logger.warning(
                    "Clay budget insufficient for next batch (need ~%d, remaining %s) -- pausing run",
                    estimated_batch_cost, budget.remaining,
                )
                break

            passed, batch_rejected = filter_companies(session, batch, clay_routines, run_row.id, campaign.key)
            rejected.extend(batch_rejected)
            stats.firmographic_pass += len(passed)
            budget.record("firmographic_enrichment", len(batch))

            # Coordinator search is a Clay call per company with no
            # cross-company dependency -- fetch the whole batch
            # concurrently (pure network reads, no DB writes) instead of
            # one sequential call per company. Confirmed live this was
            # the dominant remaining per-company cost: ~12s/company,
            # ~4m52s for 24 companies processed one at a time.
            #
            # max_workers=10 confirmed live to trigger sustained Clay
            # rate-limiting: 86 of 263 companies (33%) in one run got a
            # ClayError back. fetch_people_at_company distinguishes that
            # (returns None) from a genuine zero-result search (returns
            # []) specifically so this doesn't get silently counted as
            # "0 coordinators found" and disqualified -- _process_qualified_company
            # below routes a None to MANUAL_REVIEW for retry instead.
            # Dropped to 5 workers to reduce how often this triggers at all.
            #
            # Companies that already have a Qualification row for this
            # campaign were already coordinator-searched in a prior run --
            # _process_qualified_company reuses that result, so fetching
            # again here would be a wasted Clay credit before that reuse
            # check even runs.
            already_qualified_ids = {
                q.company_id
                for q in session.query(Qualification.company_id).filter(
                    Qualification.campaign_id == campaign.key,
                    Qualification.company_id.in_([c.id for c in passed]),
                )
            }
            needs_coordinator_search = [c for c in passed if c.id not in already_qualified_ids]

            people_results_by_company: dict[int, Optional[list]] = {}
            if needs_coordinator_search:
                with ThreadPoolExecutor(max_workers=5) as pool:
                    futures = {
                        pool.submit(fetch_people_at_company, c, campaign, clay_search): c
                        for c in needs_coordinator_search
                    }
                    for future in as_completed(futures):
                        c = futures[future]
                        try:
                            people_results_by_company[c.id] = future.result()
                        except Exception as exc:
                            logger.warning("Coordinator search failed for %s: %s", c.company_name, exc)
                            people_results_by_company[c.id] = None

            for company in passed:
                try:
                    if stage == "qualify":
                        _qualify_company(
                            session, company, campaign, clay_routines, budget, stats, rejected, run_row,
                            people_results_by_company.get(company.id, []),
                        )
                    else:  # stage == "full"
                        _process_qualified_company(
                            session, company, campaign, clay_routines, clay_search,
                            budget, stats, rejected, run_row, research_run_id,
                            people_results_by_company.get(company.id, []),
                        )
                except Exception as exc:
                    # Defense in depth: every Clay call already has its own
                    # error handling, but this catches anything else
                    # (unexpected exceptions, edge cases not yet seen) so
                    # one bad company can NEVER take down the whole
                    # campaign run again -- confirmed live: an uncaught
                    # httpx.ReadTimeout crashed a ~1 hour run outright
                    # before this existed.
                    logger.exception("Unhandled error processing %s -- logging and continuing", company.company_name)
                    session.rollback()
                    session.add(
                        ProviderError(
                            run_id=run_row.id,
                            company_id=company.id,
                            operation="process_qualified_company",
                            provider="pipeline",
                            error=str(exc),
                            occurred_at=datetime.utcnow(),
                        )
                    )
                    company.status = CompanyStatus.MANUAL_REVIEW.value
                    session.commit()
    elif clay_client is None:
        stats.firmographic_pass = 0

    # -- 15: exports --------------------------------------------------------
    # all_records is the full cumulative history for this campaign (every
    # tier, winners and losers) -- research.csv is meant to be that full
    # master file. ready_records is the strict, trimmed subset that
    # actually goes to Woodpecker. Trimming must happen AFTER filtering to
    # is_ready(), not before: trimming the whole tier-sorted history to
    # pool size first could cut off a genuinely ready Tier A/B record
    # (missing only from the trimmed slice) while keeping an earlier
    # same-tier record that isn't actually export-eligible -- confirmed
    # live, this silently dropped 6 of 12 truly-ready prospects into
    # nothing (not even rejected.csv) because gap-detection below only
    # ran over the already-trimmed list too.
    all_records = gather_pipeline_records(session, campaign)

    for rec in all_records:
        gap_reason = personalization_gap_reason(rec, campaign)
        if gap_reason:
            rejected.append(
                RejectedCompany(
                    company=rec.company.company_name,
                    domain=rec.company.canonical_domain,
                    source=None,
                    rejection_stage="personalization",
                    rejection_reason=gap_reason,
                )
            )

    # rejected.csv is meant to be the current full picture, not just this
    # run's fresh rejections -- a company already DISQUALIFIED in a prior
    # run gets skipped this run (credit control) but its reason should
    # still show up here, not just silently disappear from the file.
    # Company.disqualification_reason is the durable source (set at
    # whichever stage made the call -- firmographic, coordinator, or
    # decision-maker/IT-only) since a company disqualified before ever
    # reaching tiering has no Qualification row to hold a reason.
    already_listed_domains = {r.domain for r in rejected if r.domain}
    for company in session.query(Company).filter(Company.status == CompanyStatus.DISQUALIFIED.value):
        if company.canonical_domain in already_listed_domains:
            continue
        rejected.append(
            RejectedCompany(
                company=company.company_name,
                domain=company.canonical_domain,
                source=None,
                rejection_stage="disqualified",
                rejection_reason=company.disqualification_reason or "Disqualified (reason not recorded)",
            )
        )

    ready_records = [r for r in all_records if is_ready(r, campaign)]
    ready_records.sort(key=lambda r: {"A": 0, "B": 1}.get(r.qualification.tier, 2))
    ready_records = batcher.trim_to_pool(ready_records)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    export_dir = Path("data/exports")
    ready_path = export_dir / f"{campaign.key}_{today}_ready.csv"
    research_path = export_dir / f"{campaign.key}_{today}_research.csv"
    qa_path = export_dir / f"{campaign.key}_{today}_qa_sample.csv"
    rejected_path = export_dir / f"{campaign.key}_{today}_rejected.csv"

    stats.final_exported = write_ready_csv(ready_records, campaign, ready_path)
    write_research_csv(all_records, campaign, research_path)
    write_qa_sample_csv(ready_records, campaign, qa_path)
    write_rejected_csv(rejected, rejected_path)

    # -- finalize run row / stats -----------------------------------------

    stats.provider_error_count = session.query(ProviderError).filter(ProviderError.run_id == run_row.id).count()
    stats.clay_actual_usage = budget.actual_usage
    stats.clay_estimated_usage = budget.actual_usage
    stats.clay_remaining_budget = budget.remaining
    stats.finished_at = datetime.utcnow()

    run_row.finished_at = stats.finished_at
    run_row.raw_companies = stats.raw_companies
    run_row.deduplicated = stats.deduplicated
    run_row.suppressed = stats.suppressed
    run_row.firmographic_pass = stats.firmographic_pass
    run_row.research_candidates = stats.research_candidates
    run_row.research_completed = stats.research_completed
    run_row.coordinator_candidates = stats.coordinator_candidates
    run_row.coordinator_qualified = stats.coordinator_qualified
    run_row.tier_a = stats.tier_a
    run_row.tier_b = stats.tier_b
    run_row.tier_c = stats.tier_c
    run_row.decision_makers = stats.decision_makers
    run_row.employment_verified_count = stats.employment_verified_count
    run_row.usable_emails = stats.usable_emails
    run_row.final_exported = stats.final_exported
    run_row.clay_actual_usage = stats.clay_actual_usage
    run_row.clay_estimated_usage = stats.clay_estimated_usage
    run_row.clay_remaining_budget = stats.clay_remaining_budget
    run_row.status = "COMPLETED"
    session.commit()

    if clay_client:
        clay_client.close()

    return stats
