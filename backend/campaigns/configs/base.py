"""Shared campaign-configuration schema. One engine, three configs.

Every vertical difference (sources, keywords, research fields, coordinator
titles, decision-maker priority, disqualifiers, personalization mapping)
lives here as data -- backend/campaigns/engine.py never branches on
campaign name.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from backend.models.schemas import SourceType, SignalType


class ResearchFieldConfig(BaseModel):
    """One deterministic Claygent-style research operation.

    Mirrors the playbook's Part 8 rule: one specific fact, one prompt,
    explicit NONE fallback, raw value not a sentence.
    """

    name: str
    prompt: str
    priority: int                      # lower = researched first; ICP-stopping fields go first
    value_type: str = "text"           # "count" | "text" | "boolean" | "phrase"
    expires_days: int = 30
    source_type: SourceType = SourceType.COMPANY_WEBSITE
    stops_pipeline_if_zero: bool = False  # e.g. reporter_count == 0 -> hard stop


class DecisionMakerTitle(BaseModel):
    title: str
    rank: int                          # lower = higher priority


class SignalRule(BaseModel):
    signal_type: SignalType
    description: str
    # which research field(s)/heuristic feed this signal; interpreted by
    # research/evidence.py + qualification layer, not executed from config
    derived_from: list[str] = []


class SourceRef(BaseModel):
    module: str                        # dotted path under backend.sources.*
    enabled: bool = True
    optional: bool = False             # e.g. google_places requires an API key


class CampaignConfig(BaseModel):
    key: str
    name: str
    keywords: list[str]

    sources: list[SourceRef]

    coordinator_title_candidates: list[str]   # loose net cast before classification
    decision_maker_titles: list[DecisionMakerTitle]

    research_fields: list[ResearchFieldConfig]
    volume_field: str                  # research field name feeding VOLUME_VAR
    coverage_field: str                # research field name feeding COVERAGE_VAR

    signal_rules: list[SignalRule]

    extra_disqualifiers: list[str] = []  # keys appended to the global disqualifier set

    known_software_vendors: list[str] = []  # for software_mentioned matching

    target_cities: list[str] = []  # "City, ST" -- used only by the optional Google Places source

    def research_field(self, name: str) -> Optional[ResearchFieldConfig]:
        for f in self.research_fields:
            if f.name == name:
                return f
        return None

    def sorted_research_fields(self) -> list[ResearchFieldConfig]:
        return sorted(self.research_fields, key=lambda f: f.priority)
