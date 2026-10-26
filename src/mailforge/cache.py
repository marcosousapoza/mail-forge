"""Persistent SQLite read-through cache; IMAP remains authoritative."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from mailforge.config import CacheConfig
from mailforge.models import CacheStatus, MessageDetail

SCHEMA_VERSION = 1


class MessageCache:
    def __init__(self, config: CacheConfig, account_id: str) -> None:
        self.config = config
        self.account_id = account_id

    def initialize(self) -> None:
        if not self.config.enabled:
            return
        self.config.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mailbox_state (
                    account_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uid_validity INTEGER NOT NULL,
                    uid_next INTEGER,
                    highest_modseq INTEGER,
                    synchronized_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, mailbox)
                );
                CREATE TABLE IF NOT EXISTS message_content (
                    account_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uid_validity INTEGER NOT NULL,
                    uid INTEGER NOT NULL,
                    content_json TEXT NOT NULL,
                    content_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, mailbox, uid_validity, uid)
                );
                CREATE TABLE IF NOT EXISTS message_state (
                    account_id TEXT NOT NULL,
                    mailbox TEXT NOT NULL,
                    uid_validity INTEGER NOT NULL,
                    uid INTEGER NOT NULL,
                    flags_json TEXT NOT NULL,
                    exists_remote INTEGER NOT NULL,
                    verified_at TEXT NOT NULL,
                    modseq INTEGER,
                    PRIMARY KEY (account_id, mailbox, uid_validity, uid)
                );
                CREATE INDEX IF NOT EXISTS idx_content_accessed
                ON message_content(account_id, accessed_at);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO cache_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.config.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def update_mailbox(
        self,
        mailbox: str,
        uid_validity: int,
        uid_next: int | None,
        highest_modseq: int | None,
    ) -> None:
        if not self.config.enabled:
            return
        with closing(self._connect()) as connection:
            previous = connection.execute(
                "SELECT uid_validity FROM mailbox_state WHERE account_id=? AND mailbox=?",
                (self.account_id, mailbox),
            ).fetchone()
            if previous is not None and previous["uid_validity"] != uid_validity:
                connection.execute(
                    "DELETE FROM message_content WHERE account_id=? AND mailbox=?",
                    (self.account_id, mailbox),
                )
                connection.execute(
                    "DELETE FROM message_state WHERE account_id=? AND mailbox=?",
                    (self.account_id, mailbox),
                )
            connection.execute(
                """
                INSERT INTO mailbox_state VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, mailbox) DO UPDATE SET
                  uid_validity=excluded.uid_validity, uid_next=excluded.uid_next,
                  highest_modseq=excluded.highest_modseq, synchronized_at=excluded.synchronized_at
                """,
                (
                    self.account_id,
                    mailbox,
                    uid_validity,
                    uid_next,
                    highest_modseq,
                    self._now(),
                ),
            )
            connection.commit()

    def get(self, mailbox: str, uid_validity: int, uid: int) -> MessageDetail | None:
        if not self.config.enabled:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT c.content_json, s.flags_json, s.exists_remote
                FROM message_content c JOIN message_state s USING
                  (account_id, mailbox, uid_validity, uid)
                WHERE c.account_id=? AND c.mailbox=? AND c.uid_validity=? AND c.uid=?
                """,
                (self.account_id, mailbox, uid_validity, uid),
            ).fetchone()
            if row is None or not row["exists_remote"]:
                return None
            connection.execute(
                """UPDATE message_content SET accessed_at=?
                WHERE account_id=? AND mailbox=? AND uid_validity=? AND uid=?""",
                (self._now(), self.account_id, mailbox, uid_validity, uid),
            )
            connection.commit()
            data = json.loads(row["content_json"])
            data["flags"] = json.loads(row["flags_json"])
            data["cached"] = True
            return MessageDetail.model_validate(data)

    def put(self, message: MessageDetail) -> None:
        if not self.config.enabled:
            return
        data = message.model_copy(update={"cached": False}).model_dump(mode="json")
        encoded = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
        now = self._now()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO message_content VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.account_id,
                    message.mailbox,
                    message.uid_validity,
                    message.uid,
                    encoded,
                    len(encoded.encode()),
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT OR REPLACE INTO message_state VALUES (?, ?, ?, ?, ?, 1, ?, NULL)",
                (
                    self.account_id,
                    message.mailbox,
                    message.uid_validity,
                    message.uid,
                    json.dumps(message.flags),
                    now,
                ),
            )
            connection.commit()
        self.evict()

    def update_state(
        self, mailbox: str, uid_validity: int, uid: int, flags: list[str], *, exists: bool = True
    ) -> None:
        if not self.config.enabled:
            return
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT OR REPLACE INTO message_state
                (account_id, mailbox, uid_validity, uid, flags_json,
                 exists_remote, verified_at, modseq)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    self.account_id,
                    mailbox,
                    uid_validity,
                    uid,
                    json.dumps(sorted(flags)),
                    int(exists),
                    self._now(),
                ),
            )
            connection.commit()

    def invalidate(self, mailbox: str, uids: list[int]) -> None:
        if not self.config.enabled or not uids:
            return
        placeholders = ",".join("?" for _ in uids)
        values: tuple[object, ...] = (self.account_id, mailbox, *uids)
        with closing(self._connect()) as connection:
            connection.execute(
                f"DELETE FROM message_content WHERE account_id=? AND mailbox=? "  # noqa: S608
                f"AND uid IN ({placeholders})",
                values,
            )
            connection.execute(
                f"DELETE FROM message_state WHERE account_id=? AND mailbox=? "  # noqa: S608
                f"AND uid IN ({placeholders})",
                values,
            )
            connection.commit()

    def invalidate_mailbox(self, mailbox: str) -> None:
        if not self.config.enabled:
            return
        with closing(self._connect()) as connection:
            values = (self.account_id, mailbox)
            connection.execute(
                "DELETE FROM message_content WHERE account_id=? AND mailbox=?", values
            )
            connection.execute("DELETE FROM message_state WHERE account_id=? AND mailbox=?", values)
            connection.execute("DELETE FROM mailbox_state WHERE account_id=? AND mailbox=?", values)
            connection.commit()

    def evict(self) -> None:
        if not self.config.enabled:
            return
        cutoff = (
            datetime.now(UTC) - timedelta(seconds=self.config.eviction_age_seconds)
        ).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM message_content WHERE account_id=? AND accessed_at < ?",
                (self.account_id, cutoff),
            )
            total = connection.execute(
                "SELECT COALESCE(SUM(content_bytes), 0) FROM message_content WHERE account_id=?",
                (self.account_id,),
            ).fetchone()[0]
            while total > self.config.max_bytes:
                row = connection.execute(
                    """SELECT mailbox, uid_validity, uid, content_bytes FROM message_content
                    WHERE account_id=? ORDER BY accessed_at LIMIT 1""",
                    (self.account_id,),
                ).fetchone()
                if row is None:
                    break
                connection.execute(
                    """DELETE FROM message_content
                    WHERE account_id=? AND mailbox=? AND uid_validity=? AND uid=?""",
                    (self.account_id, row["mailbox"], row["uid_validity"], row["uid"]),
                )
                total -= row["content_bytes"]
            connection.execute(
                """DELETE FROM message_state AS state
                WHERE state.account_id=? AND NOT EXISTS (
                  SELECT 1 FROM message_content AS content
                  WHERE content.account_id=state.account_id
                    AND content.mailbox=state.mailbox
                    AND content.uid_validity=state.uid_validity
                    AND content.uid=state.uid
                )""",
                (self.account_id,),
            )
            connection.commit()

    def status(self) -> CacheStatus:
        if not self.config.enabled or not self.config.path.exists():
            return CacheStatus(
                enabled=self.config.enabled,
                path=str(self.config.path),
                entries=0,
                state_entries=0,
                approximate_bytes=0,
                max_bytes=self.config.max_bytes,
            )
        with closing(self._connect()) as connection:
            content = connection.execute(
                """SELECT COUNT(*) AS count, COALESCE(SUM(content_bytes), 0) AS bytes,
                MIN(accessed_at) AS oldest FROM message_content WHERE account_id=?""",
                (self.account_id,),
            ).fetchone()
            states = connection.execute(
                "SELECT COUNT(*) FROM message_state WHERE account_id=?", (self.account_id,)
            ).fetchone()[0]
        return CacheStatus(
            enabled=True,
            path=str(self.config.path),
            entries=content["count"],
            state_entries=states,
            approximate_bytes=content["bytes"],
            max_bytes=self.config.max_bytes,
            oldest_accessed_at=content["oldest"],
        )

    def clear(self) -> int:
        if not self.config.enabled or not self.config.path.exists():
            return 0
        with closing(self._connect()) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM message_content WHERE account_id=?", (self.account_id,)
            ).fetchone()[0]
            connection.execute("DELETE FROM message_content WHERE account_id=?", (self.account_id,))
            connection.execute("DELETE FROM message_state WHERE account_id=?", (self.account_id,))
            connection.execute("DELETE FROM mailbox_state WHERE account_id=?", (self.account_id,))
            connection.commit()
        return int(count)
