from pathlib import Path

import pandas as pd

from backend.sources.court_reporting.manual_icp_list import ManualICPListSource


def _write_fixture(tmp_path: Path) -> Path:
    xlsx_path = tmp_path / "icp_list.xlsx"
    header_block = pd.DataFrame(
        [
            ["Vertical 1 -- Court Reporting Agencies", None, None, None, None, None, None, None, None, None],
            ["18 companies confirmed against ICP", None, None, None, None, None, None, None, None, None],
            [None] * 10,
            [
                "Company Name", "Website", "City", "State", "LinkedIn Employees",
                "Annual Revenue (Clay)", "Multi-Location?", "Source", "Fit Notes / Rationale", "Clay Verified",
            ],
        ]
    )
    data_block = pd.DataFrame(
        [
            [
                "Executive Reporting Service", "executivereporting.com", "Clearwater", "FL",
                17, "$5M-10M", "Yes", "Google Maps by metro", "Established Florida agency.", "Yes",
            ],
            [
                "  ", None, None, None, None, None, None, None, None, None,
            ],
        ]
    )
    full = pd.concat([header_block, data_block], ignore_index=True)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        full.to_excel(writer, sheet_name="Court Reporting", index=False, header=False)
    return xlsx_path


def test_parses_real_layout(tmp_path):
    xlsx_path = _write_fixture(tmp_path)
    companies = ManualICPListSource(manual_import_path=xlsx_path).load()

    assert len(companies) == 1
    c = companies[0]
    assert c.company_name == "Executive Reporting Service"
    assert c.domain == "executivereporting.com"
    assert c.city == "Clearwater"
    assert c.state == "FL"
    assert c.source == "manual_icp_list"


def test_missing_file_returns_empty(tmp_path):
    companies = ManualICPListSource(manual_import_path=tmp_path / "does_not_exist.xlsx").load()
    assert companies == []
