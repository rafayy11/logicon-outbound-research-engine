import csv
from pathlib import Path

from backend.sources.court_reporting.manual_ncra_list import ManualNCRAListSource

# Trimmed real rows from the user's curated Google Sheet export (fetched
# 2026-08-18): a normal row, a row with a trailing-space website (real
# formatting quirk in the sheet), and a row with no email at all.
ROWS = [
    {
        "Company Name": "Behmke Reporting and Video Services",
        "Website": "behmke.com ",
        "Email": "depos@behmke.com",
        "NCRA Link": "https://www.ncra.org/detail-pages/prolink-detail/behmke",
    },
    {
        "Company Name": "Jonnell Agnew & Associates",
        "Website": "https://www.jonnellagnewcourtreporters.com/",
        "Email": "",
        "NCRA Link": "https://www.ncra.org/detail-pages/prolink-detail/agnew",
    },
]


def _write_fixture(tmp_path: Path) -> Path:
    csv_path = tmp_path / "manual_ncra.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Company Name", "Website", "Email", "NCRA Link"])
        writer.writeheader()
        writer.writerows(ROWS)
    return csv_path


def test_parses_real_curated_rows(tmp_path):
    csv_path = _write_fixture(tmp_path)
    companies = ManualNCRAListSource(manual_import_path=csv_path).load()

    assert len(companies) == 2

    behmke = companies[0]
    assert behmke.company_name == "Behmke Reporting and Video Services"
    assert behmke.domain == "behmke.com"  # trailing space + no scheme handled
    assert behmke.source == "manual_ncra_user_curated_list"
    assert behmke.source_identifier == "depos@behmke.com"

    jonnell = companies[1]
    assert jonnell.domain == "jonnellagnewcourtreporters.com"
    assert jonnell.source_identifier == "https://www.ncra.org/detail-pages/prolink-detail/agnew"


def test_missing_file_returns_empty(tmp_path):
    companies = ManualNCRAListSource(manual_import_path=tmp_path / "does_not_exist.csv").load()
    assert companies == []


def test_blank_company_name_is_skipped(tmp_path):
    csv_path = tmp_path / "manual_ncra.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Company Name", "Website", "Email", "NCRA Link"])
        writer.writeheader()
        writer.writerow({"Company Name": "  ", "Website": "example.com", "Email": "", "NCRA Link": ""})
    companies = ManualNCRAListSource(manual_import_path=csv_path).load()
    assert companies == []
