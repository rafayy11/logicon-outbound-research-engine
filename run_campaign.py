#!/usr/bin/env python3
"""Logicon Outbound Research Engine -- CLI entry point.

Usage:
    python run_campaign.py --campaign court_reporting --target 50
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv()

from backend.campaigns.configs import available_campaigns, get_config  # noqa: E402
from backend.campaigns.engine import run_pipeline  # noqa: E402


def _print_report(stats) -> None:
    print("\n" + "=" * 70)
    print(f"CAMPAIGN RUN REPORT -- {stats.campaign}")
    print("=" * 70)
    print(f"Raw companies:              {stats.raw_companies}")
    print(f"Deduplicated:               {stats.deduplicated}")
    print(f"Suppressed:                 {stats.suppressed}")
    print(f"Firmographic pass:          {stats.firmographic_pass}")
    print(f"Research candidates:        {stats.research_candidates}")
    print(f"Research completed:         {stats.research_completed}")
    print(f"Coordinator candidates:     {stats.coordinator_candidates}")
    print(f"Coordinator-qualified:      {stats.coordinator_qualified}")
    print(f"Tier A:                     {stats.tier_a}")
    print(f"Tier B:                     {stats.tier_b}")
    print(f"Tier C:                     {stats.tier_c}")
    print(f"Decision makers:            {stats.decision_makers}")
    print(f"Employment verified:        {stats.employment_verified_count}")
    print(f"Usable emails:              {stats.usable_emails}")
    print(f"Final exported:             {stats.final_exported}")
    print("-" * 70)
    print(f"Clay estimated usage:       {stats.clay_estimated_usage}")
    print(f"Clay actual usage:          {stats.clay_actual_usage}")
    print(f"Clay remaining budget:      {stats.clay_remaining_budget if stats.clay_remaining_budget is not None else 'unlimited (no CLAY_MAX_BUDGET set)'}")
    print(f"Credit efficiency:          {stats.credit_efficiency if stats.credit_efficiency is not None else 'n/a'} (final prospects / Clay usage)")
    print("-" * 70)
    print(f"Provider errors:            {stats.provider_error_count}")
    print("=" * 70)
    print(f"\nOutputs (data/exports/):")
    today = stats.finished_at.strftime("%Y-%m-%d") if stats.finished_at else "today"
    print(f"  {stats.campaign}_{today}_ready.csv")
    print(f"  {stats.campaign}_{today}_research.csv")
    print(f"  {stats.campaign}_{today}_qa_sample.csv")
    print(f"  {stats.campaign}_{today}_rejected.csv")
    print(f"\nRun another campaign with:")
    print(f"  python run_campaign.py --campaign {stats.campaign} --target {stats.target_count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Logicon Outbound Research Engine")
    parser.add_argument("--campaign", required=True, help=f"One of: {', '.join(available_campaigns())}")
    parser.add_argument("--target", type=int, default=50, help="Target number of FINAL campaign-ready prospects")
    parser.add_argument(
        "--stage", default="full", choices=["qualify", "enrich", "full"],
        help=(
            "qualify: coordinator search + tiering only, spends nothing from the "
            "scarce enrichment-credit pool. enrich: job openings/research/decision-"
            "maker/email for companies already at Tier A/B -- run this deliberately "
            "once you've reviewed the qualify-stage results. full: both in one pass "
            "(default, original behavior)."
        ),
    )
    parser.add_argument(
        "--enrich-tiers", default="A,B",
        help="Comma-separated tiers --stage enrich spends real credit on, e.g. A,B,C. Default A,B (Tier C stays parked, no spend).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        campaign = get_config(args.campaign)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    enrich_tiers = tuple(t.strip().upper() for t in args.enrich_tiers.split(",") if t.strip())
    stats = run_pipeline(campaign, args.target, stage=args.stage, enrich_tiers=enrich_tiers)
    _print_report(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
