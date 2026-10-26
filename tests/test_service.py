from __future__ import annotations

import pytest

from mailforge.cache import MessageCache
from mailforge.config import Settings
from mailforge.errors import MessageNotFoundError
from mailforge.models import Address, MailboxStatus, MessageDetail, SendResult
from mailforge.service import MailService


def _status(uid_validity: int = 10) -> MailboxStatus:
    return MailboxStatus(mailbox="INBOX", messages=1, uid_next=8, uid_validity=uid_validity)


class FakeImap:
    fetches = 0
    deleted = False

    def get_flags(self, mailbox: str, uid: int) -> tuple[MailboxStatus, list[str]]:
        if self.deleted:
            raise MessageNotFoundError("gone")
        return _status(), ["\\Seen"]

    def get_message(self, mailbox: str, uid: int) -> tuple[MailboxStatus, MessageDetail]:
        self.fetches += 1
        return _status(), MessageDetail(
            mailbox=mailbox,
            uid_validity=10,
            uid=uid,
            subject="Cached",
            sender=Address(email="sender@test"),
            flags=["\\Seen"],
            text_body="body",
        )

    def delete_messages(self, mailbox: str, uids: list[int]) -> tuple[MailboxStatus, bool]:
        self.deleted = True
        return _status(), True


class FakeSmtp:
    def send(self, message: object) -> SendResult:
        return SendResult(
            message_id="<sent@test>",
            accepted_recipients=["to@test"],
            rejected_recipients={},
            saved_to_sent=False,
        )


@pytest.mark.asyncio
async def test_read_through_cache_revalidates_remote_existence(settings: Settings) -> None:
    imap = FakeImap()
    service = MailService(  # type: ignore[arg-type]
        settings,
        imap=imap,
        smtp=FakeSmtp(),
        cache=MessageCache(settings.cache, settings.account_id),
    )
    await service.initialize()

    first = await service.get_message("INBOX", 7)
    second = await service.get_message("INBOX", 7)

    assert not first.cached
    assert second.cached
    assert imap.fetches == 1
    imap.deleted = True
    with pytest.raises(MessageNotFoundError):
        await service.get_message("INBOX", 7)


@pytest.mark.asyncio
async def test_delete_invalidates_local_content(settings: Settings) -> None:
    imap = FakeImap()
    cache = MessageCache(settings.cache, settings.account_id)
    service = MailService(settings, imap=imap, smtp=FakeSmtp(), cache=cache)  # type: ignore[arg-type]
    await service.initialize()
    await service.get_message("INBOX", 7)

    result = await service.delete("INBOX", [7])

    assert result.expunged
    assert cache.status().entries == 0


@pytest.mark.asyncio
async def test_send_composes_and_delivers(settings: Settings) -> None:
    service = MailService(settings, imap=FakeImap(), smtp=FakeSmtp())  # type: ignore[arg-type]
    result = await service.send(
        to=["to@test"],
        subject="Subject",
        text_body="Body",
        html_body=None,
        cc=None,
        bcc=None,
        reply_to=None,
        attachments=None,
    )
    assert result.accepted_recipients == ["to@test"]
