from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from mailforge.config import ImapConfig
from mailforge.imap import ImapClient, decode_mailbox, encode_mailbox
from mailforge.models import SearchQuery


class FakeImap:
    capabilities = ("IMAP4REV1", "UIDPLUS")

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def response(self, name: str) -> tuple[str, list[bytes] | None]:
        values = {
            "UIDVALIDITY": [b"77"],
            "UIDNEXT": [b"11"],
            "HIGHESTMODSEQ": [b"900"],
            "UNSEEN": [b"2"],
            "RECENT": [b"0"],
        }
        return name, values.get(name)

    def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"4 8 10"]
        if command == "FETCH":
            requested = str(args[0]).split(",")
            rows: list[object] = []
            for uid in requested:
                raw = (
                    b"From: sender@test\r\nTo: user@test\r\nSubject: Test "
                    + uid.encode()
                    + b"\r\nMessage-ID: <m"
                    + uid.encode()
                    + b"@test>\r\n\r\nbody"
                )
                rows.append((f"1 (UID {uid} FLAGS (\\Seen) RFC822.SIZE {len(raw)})".encode(), raw))
            return "OK", rows
        return "OK", []


class ClientWithFake(ImapClient):
    def __init__(self, fake: FakeImap) -> None:
        super().__init__(ImapConfig("test", 993, "u", "p", True, 1))
        self.fake = fake

    @contextmanager
    def connection(self) -> Generator[Any, None, None]:
        yield self.fake


def test_modified_utf7_round_trip() -> None:
    for mailbox in ["INBOX", "Projects & Plans", "Envoyes", "日本語/受信"]:
        assert decode_mailbox(encode_mailbox(mailbox)) == mailbox


def test_search_uses_uids_and_returns_newest_first() -> None:
    fake = FakeImap()
    status, messages = ClientWithFake(fake).search(
        SearchQuery(mailbox="INBOX", subject="Test", unread=True, limit=2)
    )
    assert status.uid_validity == 77
    assert [message.uid for message in messages] == [10, 8]
    search_call = next(call for call in fake.calls if call[0] == "SEARCH")
    assert "SUBJECT" in search_call and "UNSEEN" in search_call


def test_delete_uses_uid_expunge() -> None:
    fake = FakeImap()
    status, expunged = ClientWithFake(fake).delete_messages("INBOX", [8, 4])
    assert status.uid_validity == 77 and expunged
    assert ("STORE", "4,8", "+FLAGS.SILENT", "(\\Deleted)") in fake.calls
    assert ("EXPUNGE", "4,8") in fake.calls


def test_move_fallback_without_uidplus_never_global_expunges() -> None:
    fake = FakeImap()
    fake.capabilities = ("IMAP4REV1",)
    status, expunged = ClientWithFake(fake).move("INBOX", [4], "Archive")
    assert status.uid_validity == 77 and not expunged
    assert ("COPY", "4", "Archive") in fake.calls
    assert not any(call[0] == "EXPUNGE" for call in fake.calls)
