"""Court reporting campaign config -- built first per the MVP instruction.

Sources, prompts, titles and vendor names are taken directly from
Logicon_Outbound_Playbook.docx Part 4 and Part 8. Where the playbook gives
an exact Claygent prompt, it is used verbatim.
"""

from __future__ import annotations

from backend.campaigns.configs.base import (
    CampaignConfig,
    DecisionMakerTitle,
    ResearchFieldConfig,
    SignalRule,
    SourceRef,
)
from backend.models.schemas import SignalType, SourceType

COURT_REPORTING = CampaignConfig(
    key="court_reporting",
    name="Court Reporting",
    keywords=[
        "court reporting",
        "court reporter",
        "deposition services",
        "litigation support",
    ],
    sources=[
        SourceRef(module="backend.sources.court_reporting.ncra"),
        SourceRef(module="backend.sources.court_reporting.state_associations"),
        SourceRef(module="backend.sources.court_reporting.manual_ncra_list", optional=True),
        SourceRef(module="backend.sources.court_reporting.manual_icp_list", optional=True),
        SourceRef(module="backend.sources.court_reporting.clay_icp_search", optional=True),
        SourceRef(module="backend.sources.google_places", optional=True),
    ],
    coordinator_title_candidates=[
        "scheduler",
        "scheduling coordinator",
        "scheduling manager",
        "coordinator",
        "operations coordinator",
        "dispatch coordinator",
    ],
    decision_maker_titles=[
        DecisionMakerTitle(title="Owner", rank=1),
        DecisionMakerTitle(title="President", rank=2),
        DecisionMakerTitle(title="Director of Operations", rank=3),
        DecisionMakerTitle(title="Scheduling Manager", rank=4),
        DecisionMakerTitle(title="Office Manager", rank=5),
    ],
    research_fields=[
        ResearchFieldConfig(
            name="reporter_count",
            prompt=(
                "Visit this company's website. Count how many court reporters are "
                "listed on their team, roster, or reporters page. Return only the "
                "number. If no such page exists or no individuals are listed, "
                "return NONE."
            ),
            priority=1,
            value_type="count",
            expires_days=30,
        ),
        ResearchFieldConfig(
            name="metro_count",
            prompt=(
                "Visit this company's website. Count how many distinct "
                "cities/metro areas are listed under locations served or "
                "'areas served'. Return only the number. If not stated, "
                "return NONE."
            ),
            priority=2,
            value_type="count",
            expires_days=30,
        ),
        ResearchFieldConfig(
            name="open_scheduler_roles",
            prompt=(
                "Search this company's careers page and job boards for "
                "currently open roles containing the words scheduler, "
                "coordinator, dispatcher, or scheduling. Return the number "
                "of open roles and the exact job title of the most recent. "
                "If none, return NONE."
            ),
            priority=3,
            value_type="text",
            expires_days=14,
        ),
        ResearchFieldConfig(
            name="hiring_signal",
            prompt=(
                "Search this company's careers page, Indeed, and LinkedIn "
                "Jobs for any currently open role with a title containing "
                "scheduler, coordinator, or dispatcher. Return YES plus the "
                "exact job title if a matching role is currently posted. "
                "If no matching role is posted, return NONE."
            ),
            priority=4,
            value_type="boolean",
            expires_days=14,
        ),
        ResearchFieldConfig(
            name="client_portal",
            prompt=(
                "Visit this company's website. Does it offer clients an "
                "online scheduling system or a status/login portal? Return "
                "YES or NO with the exact page name or link text found. If "
                "unclear, return NONE."
            ),
            priority=5,
            value_type="boolean",
            expires_days=60,
        ),
        ResearchFieldConfig(
            name="software_mentioned",
            prompt=(
                "Search this company's website and job postings for any "
                "named agency management or scheduling software (for "
                "example RepAgencyWorks, Acclaim Solaria, AccuLaw, Case "
                "CATalyst, Eclipse). Return the exact product name(s) found. "
                "If none is named, return NONE."
            ),
            priority=6,
            value_type="text",
            expires_days=60,
        ),
        ResearchFieldConfig(
            name="advertised_turnaround",
            prompt=(
                "Visit this company's website and find any stated "
                "turnaround time or service-speed promise for transcripts "
                "or scheduling confirmation, for example 'transcripts in 5 "
                "business days'. Return the exact phrase. If none is "
                "stated, return NONE."
            ),
            priority=7,
            value_type="phrase",
            expires_days=60,
        ),
    ],
    volume_field="reporter_count",
    coverage_field="metro_count",
    signal_rules=[
        SignalRule(
            signal_type=SignalType.HIRING_SCHEDULER,
            description="Actively hiring a scheduler/coordinator/dispatcher right now -- strongest signal.",
            derived_from=["hiring_signal"],
        ),
        SignalRule(
            signal_type=SignalType.MULTIPLE_OPERATIONS_ROLES,
            description="3+ operations roles posted in the last 90 days.",
            derived_from=["open_scheduler_roles"],
        ),
        SignalRule(
            signal_type=SignalType.NO_CLIENT_PORTAL,
            description="No online scheduling/status portal -- clients phone in for status.",
            derived_from=["client_portal"],
        ),
        SignalRule(
            signal_type=SignalType.ACQUISITION,
            description="Recent acquisition mentioned in company news.",
            derived_from=["company_news"],
        ),
        SignalRule(
            signal_type=SignalType.NEW_OFFICE,
            description="New office/location mentioned in company news.",
            derived_from=["company_news"],
        ),
        # HEADCOUNT_GROWTH and CALL_TO_SCHEDULE are defined by the playbook
        # but are intentionally left undetected in this MVP: no reliable,
        # evidence-backed data source is wired up for them yet. They are not
        # fabricated from proxies.
    ],
    known_software_vendors=[
        "RepAgencyWorks",
        "Acclaim Solaria",
        "AccuLaw",
        "Case CATalyst",
        "Eclipse",
    ],
    target_cities=[
        "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX",
        "Phoenix, AZ", "Philadelphia, PA", "San Antonio, TX", "San Diego, CA",
        "Dallas, TX", "Austin, TX", "Miami, FL", "Atlanta, GA",
        "Denver, CO", "Seattle, WA", "Boston, MA",
    ],
)
