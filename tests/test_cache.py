from __future__ import annotations

from pathlib import Path

from mailforge.cache import MessageCache
from mailforge.config import CacheConfig
from mailforge.models import Address, MessageDetail


def _message(uid_validity: int = 1, uid: int = 7, body: str = "body") -> MessageDetail:
    return MessageDetail(
        mailbox="INBOX",
        uid_validity=uid_validity,
        uid=uid,
        message_id="<message@test>",
        subject="Subject",
        sender=Address(email="sender@test"),
        flags=["\\Seen"],
        text_body=body,
    )


def test_cache_hit_state_update_and_clear(tmp_path: Path) -> None:
    cache = MessageCache(CacheConfig(True, tmp_path / "cache.db", 10000, 86400, 60), "acct")
    cache.initialize()
    cache.update_mailbox("INBOX", 1, 8, None)
    cache.put(_message())

    hit = cache.get("INBOX", 1, 7)
    assert hit is not None and hit.cached
    cache.update_state("INBOX", 1, 7, ["\\Flagged"])
    assert cache.get("INBOX", 1, 7).flags == ["\\Flagged"]  # type: ignore[union-attr]
    assert cache.status().entries == 1
    assert cache.clear() == 1
    assert cache.status().entries == 0


def test_uidvalidity_change_invalidates_messages(tmp_path: Path) -> None:
    cache = MessageCache(CacheConfig(True, tmp_path / "cache.db", 10000, 86400, 60), "acct")
    cache.initialize()
    cache.update_mailbox("INBOX", 1, 8, None)
    cache.put(_message())

    cache.update_mailbox("INBOX", 2, 1, None)

    assert cache.get("INBOX", 1, 7) is None
    assert cache.status().entries == 0


def test_lru_size_eviction(tmp_path: Path) -> None:
    cache = MessageCache(CacheConfig(True, tmp_path / "cache.db", 300, 86400, 60), "acct")
    cache.initialize()
    cache.update_mailbox("INBOX", 1, 9, None)
    cache.put(_message(uid=7, body="a" * 500))
    assert cache.status().approximate_bytes <= 300


def test_disabled_cache_never_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    cache = MessageCache(CacheConfig(False, path, 1000, 86400, 60), "acct")
    cache.initialize()
    cache.put(_message())
    assert cache.get("INBOX", 1, 7) is None
    assert not path.exists()
