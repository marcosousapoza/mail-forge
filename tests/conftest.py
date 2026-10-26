from __future__ import annotations

from pathlib import Path

import pytest

from mailforge.config import CacheConfig, ImapConfig, Settings, SmtpConfig


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        imap=ImapConfig("imap.test", 993, "user@test", "secret", True, 5),
        smtp=SmtpConfig("smtp.test", 587, "user@test", "secret", "starttls", 5),
        cache=CacheConfig(True, tmp_path / "cache.sqlite3", 1024 * 1024, 86400, 60),
        account_id="test-account",
        default_from="user@test",
        archive_mailbox="Archive",
        drafts_mailbox="Drafts",
        sent_mailbox=None,
        log_level="INFO",
    )
