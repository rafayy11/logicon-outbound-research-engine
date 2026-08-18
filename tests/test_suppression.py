from types import SimpleNamespace

from backend.suppression.engine import SuppressionEngine


def _write_csv(path, rows):
    path.write_text(
        "type,value,reason,created_at\n"
        + "\n".join(",".join(row) for row in rows)
        + "\n"
    )


def test_email_suppression(tmp_path):
    csv_path = tmp_path / "suppressions.csv"
    _write_csv(csv_path, [("EMAIL", "bad@example.com", "unsubscribed", "2026-01-01")])
    engine = SuppressionEngine(path=csv_path)
    assert engine.check_email("bad@example.com") == "unsubscribed"
    assert engine.check_email("BAD@EXAMPLE.COM") == "unsubscribed"  # case-insensitive
    assert engine.check_email("good@example.com") is None


def test_domain_suppression_blocks_all_emails_at_domain(tmp_path):
    csv_path = tmp_path / "suppressions.csv"
    _write_csv(csv_path, [("DOMAIN", "blocked.com", "existing client", "2026-01-01")])
    engine = SuppressionEngine(path=csv_path)
    assert engine.check_email("anyone@blocked.com") == "existing client"

    company = SimpleNamespace(canonical_domain="blocked.com", company_name="Blocked Co")
    assert engine.check_company(company) == "existing client"


def test_company_name_suppression(tmp_path):
    csv_path = tmp_path / "suppressions.csv"
    _write_csv(csv_path, [("COMPANY", "Acme Reporting Inc", "do not contact", "2026-01-01")])
    engine = SuppressionEngine(path=csv_path)
    company = SimpleNamespace(canonical_domain=None, company_name="Acme Reporting, LLC")
    assert engine.check_company(company) == "do not contact"


def test_missing_file_returns_empty_engine(tmp_path):
    engine = SuppressionEngine(path=tmp_path / "does_not_exist.csv")
    assert engine.check_email("anyone@example.com") is None


def test_add_appends_and_reloads(tmp_path):
    csv_path = tmp_path / "suppressions.csv"
    engine = SuppressionEngine(path=csv_path)
    assert engine.check_email("new@example.com") is None
    engine.add("EMAIL", "new@example.com", "bounced")
    assert engine.check_email("new@example.com") == "bounced"
