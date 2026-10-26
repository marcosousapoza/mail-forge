from __future__ import annotations

from pathlib import Path

import pytest

from mailforge.config import Settings
from mailforge.errors import ConfigurationError


def _secret(tmp_path: Path, name: str, value: str) -> str:
    path = tmp_path / name
    path.write_text(value, encoding="utf-8")
    return str(path)


def test_settings_load_credentials_only_from_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAILFORGE_IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("MAILFORGE_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("MAILFORGE_IMAP_USERNAME_FILE", _secret(tmp_path, "iu", "alice@test\n"))
    monkeypatch.setenv("MAILFORGE_IMAP_PASSWORD_FILE", _secret(tmp_path, "ip", "imap-pass"))
    monkeypatch.setenv("MAILFORGE_SMTP_USERNAME_FILE", _secret(tmp_path, "su", "sender@test"))
    monkeypatch.setenv("MAILFORGE_SMTP_PASSWORD_FILE", _secret(tmp_path, "sp", "smtp-pass"))
    monkeypatch.setenv("MAILFORGE_CACHE_PATH", str(tmp_path / "db.sqlite3"))

    settings = Settings.from_env()

    assert settings.imap.username == "alice@test"
    assert settings.smtp.password == "smtp-pass"
    assert settings.account_id
    assert settings.cache.path == tmp_path / "db.sqlite3"


def test_missing_secret_has_safe_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILFORGE_IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("MAILFORGE_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("MAILFORGE_IMAP_USERNAME_FILE", str(tmp_path / "missing"))

    with pytest.raises(ConfigurationError, match="Required secret") as error:
        Settings.from_env()

    assert "password" not in str(error.value).lower()


def test_invalid_boolean_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILFORGE_IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("MAILFORGE_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("MAILFORGE_IMAP_USERNAME_FILE", _secret(tmp_path, "u", "user"))
    monkeypatch.setenv("MAILFORGE_IMAP_PASSWORD_FILE", _secret(tmp_path, "p", "pass"))
    monkeypatch.setenv("MAILFORGE_CACHE_ENABLED", "maybe")
    with pytest.raises(ConfigurationError, match="boolean"):
        Settings.from_env()
