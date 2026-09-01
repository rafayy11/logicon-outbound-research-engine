from backend.providers.clay.accounts import active_account


def test_defaults_to_primary_with_bare_env_vars(monkeypatch):
    monkeypatch.delenv("CLAY_ACTIVE_ACCOUNT", raising=False)
    monkeypatch.setenv("CLAY_API_KEY", "primary-key")
    monkeypatch.setenv("CLAY_ROUTINE_WORK_EMAIL", "function:t_primary")

    account = active_account()
    assert account.name == "primary"
    assert account.api_key == "primary-key"
    assert account.env("ROUTINE_WORK_EMAIL") == "function:t_primary"


def test_named_account_reads_prefixed_vars(monkeypatch):
    monkeypatch.setenv("CLAY_ACTIVE_ACCOUNT", "2")
    monkeypatch.setenv("CLAY_API_KEY", "primary-key")  # must NOT leak into account "2"
    monkeypatch.setenv("CLAY_ACCOUNT_2_API_KEY", "second-account-key")
    monkeypatch.setenv("CLAY_ACCOUNT_2_ROUTINE_WORK_EMAIL", "function:t_second")

    account = active_account()
    assert account.name == "2"
    assert account.api_key == "second-account-key"
    assert account.env("ROUTINE_WORK_EMAIL") == "function:t_second"


def test_named_account_with_no_routine_configured_is_none(monkeypatch):
    monkeypatch.setenv("CLAY_ACTIVE_ACCOUNT", "2")
    monkeypatch.setenv("CLAY_ACCOUNT_2_API_KEY", "second-account-key")
    monkeypatch.delenv("CLAY_ACCOUNT_2_ROUTINE_WORK_EMAIL", raising=False)

    account = active_account()
    assert account.env("ROUTINE_WORK_EMAIL") is None  # never guessed


def test_missing_api_key_is_none_not_empty_string(monkeypatch):
    monkeypatch.delenv("CLAY_ACTIVE_ACCOUNT", raising=False)
    monkeypatch.delenv("CLAY_API_KEY", raising=False)
    account = active_account()
    assert account.api_key is None
