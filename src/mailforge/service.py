"""Async application service coordinating protocols and cache."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from email.message import EmailMessage
from typing import ParamSpec, TypeVar

from mailforge.cache import MessageCache
from mailforge.config import Settings
from mailforge.errors import MailForgeError
from mailforge.imap import ImapClient
from mailforge.mime import compose_message
from mailforge.models import (
    AttachmentInput,
    CacheStatus,
    DraftResult,
    MailboxInfo,
    MailboxStatus,
    MessageDetail,
    MessageSummary,
    MutationResult,
    OperationResult,
    SearchQuery,
    SendResult,
)
from mailforge.smtp import SmtpClient

logger = logging.getLogger(__name__)
P = ParamSpec("P")
T = TypeVar("T")


class MailService:
    def __init__(
        self,
        settings: Settings,
        *,
        imap: ImapClient | None = None,
        smtp: SmtpClient | None = None,
        cache: MessageCache | None = None,
    ) -> None:
        self.settings = settings
        self.imap = imap or ImapClient(settings.imap)
        self.smtp = smtp or SmtpClient(settings.smtp)
        self.cache = cache or MessageCache(settings.cache, settings.account_id)
        self._protocol_slots = asyncio.Semaphore(8)

    async def initialize(self) -> None:
        await asyncio.to_thread(self.cache.initialize)

    async def _call(self, function: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
        async with self._protocol_slots:
            return await asyncio.to_thread(function, *args, **kwargs)

    async def capabilities(self) -> list[str]:
        return await self._call(self.imap.capabilities)

    async def list_mailboxes(self) -> list[MailboxInfo]:
        return await self._call(self.imap.list_mailboxes)

    async def mailbox_status(self, mailbox: str) -> MailboxStatus:
        status = await self._call(self.imap.mailbox_status, mailbox)
        assert isinstance(status, MailboxStatus)
        await asyncio.to_thread(
            self.cache.update_mailbox,
            mailbox,
            status.uid_validity,
            status.uid_next,
            status.highest_modseq,
        )
        return status

    async def search(self, query: SearchQuery) -> list[MessageSummary]:
        status, messages = await self._call(self.imap.search, query)
        await asyncio.to_thread(
            self.cache.update_mailbox,
            query.mailbox,
            status.uid_validity,
            status.uid_next,
            status.highest_modseq,
        )
        return messages

    async def get_message(self, mailbox: str, uid: int) -> MessageDetail:
        status, flags = await self._call(self.imap.get_flags, mailbox, uid)
        await asyncio.to_thread(
            self.cache.update_mailbox,
            mailbox,
            status.uid_validity,
            status.uid_next,
            status.highest_modseq,
        )
        await asyncio.to_thread(self.cache.update_state, mailbox, status.uid_validity, uid, flags)
        cached = await asyncio.to_thread(self.cache.get, mailbox, status.uid_validity, uid)
        if cached is not None:
            return cached
        fetched_status, message = await self._call(self.imap.get_message, mailbox, uid)
        if fetched_status.uid_validity != status.uid_validity:
            await asyncio.to_thread(
                self.cache.update_mailbox,
                mailbox,
                fetched_status.uid_validity,
                fetched_status.uid_next,
                fetched_status.highest_modseq,
            )
        await asyncio.to_thread(self.cache.put, message)
        return message

    async def get_attachment(self, mailbox: str, uid: int, part_id: str) -> dict[str, object]:
        status, attachment, content = await self._call(
            self.imap.get_attachment, mailbox, uid, part_id
        )
        await asyncio.to_thread(
            self.cache.update_mailbox,
            mailbox,
            status.uid_validity,
            status.uid_next,
            status.highest_modseq,
        )
        return {
            "mailbox": mailbox,
            "uid_validity": status.uid_validity,
            "uid": uid,
            "attachment": attachment.model_dump(mode="json"),
            "content_base64": base64.b64encode(content).decode(),
        }

    async def _send_message(self, message: EmailMessage) -> SendResult:
        result = await self._call(self.smtp.send, message)
        assert isinstance(result, SendResult)
        if self.settings.sent_mailbox:
            try:
                await self._call(
                    self.imap.append_message, self.settings.sent_mailbox, message, "\\Seen"
                )
                result.saved_to_sent = True
            except MailForgeError:
                logger.warning("Message sent but could not be appended to configured Sent mailbox")
        return result

    async def send(
        self,
        *,
        to: list[str],
        subject: str,
        text_body: str | None,
        html_body: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        reply_to: str | None,
        attachments: list[AttachmentInput] | None,
    ) -> SendResult:
        message = compose_message(
            sender=self.settings.default_from,
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            attachments=attachments,
        )
        return await self._send_message(message)

    async def reply(
        self,
        *,
        mailbox: str,
        uid: int,
        text_body: str | None,
        html_body: str | None,
        reply_all: bool,
        attachments: list[AttachmentInput] | None,
    ) -> SendResult:
        original = await self.get_message(mailbox, uid)
        target = original.reply_to or ([original.sender] if original.sender else [])
        to = [address.email for address in target if address]
        if not to:
            raise MailForgeError("Original message has no reply address")
        cc: list[str] = []
        if reply_all:
            own = self.settings.default_from.casefold()
            seen = {address.casefold() for address in to}
            for address in [*original.to, *original.cc]:
                if address.email.casefold() != own and address.email.casefold() not in seen:
                    cc.append(address.email)
                    seen.add(address.email.casefold())
        subject = original.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        references = [*original.references]
        if original.message_id:
            references.append(original.message_id)
        message = compose_message(
            sender=self.settings.default_from,
            to=to,
            cc=cc,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            in_reply_to=original.message_id,
            references=references,
            attachments=attachments,
        )
        return await self._send_message(message)

    async def forward(
        self,
        *,
        mailbox: str,
        uid: int,
        to: list[str],
        text_body: str | None,
        html_body: str | None,
    ) -> SendResult:
        _, original, raw = await self._call(self.imap.get_raw_message, mailbox, uid)
        subject = original.subject
        if not subject.lower().startswith("fwd:"):
            subject = f"Fwd: {subject}"
        attachment = AttachmentInput(
            filename="forwarded-message.eml",
            content_base64=base64.b64encode(raw).decode(),
            content_type="message/rfc822",
        )
        message = compose_message(
            sender=self.settings.default_from,
            to=to,
            subject=subject,
            text_body=text_body or "Forwarded message attached.",
            html_body=html_body,
            attachments=[attachment],
        )
        return await self._send_message(message)

    async def save_draft(
        self,
        *,
        to: list[str],
        subject: str,
        text_body: str | None,
        html_body: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        attachments: list[AttachmentInput] | None,
    ) -> DraftResult:
        mailbox = await self._special_mailbox("\\Drafts", self.settings.drafts_mailbox)
        message = compose_message(
            sender=self.settings.default_from,
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
        )
        uid_validity, uid = await self._call(self.imap.append_message, mailbox, message, "\\Draft")
        return DraftResult(
            mailbox=mailbox,
            uid_validity=uid_validity,
            uid=uid,
            message_id=str(message["Message-ID"]),
        )

    async def update_draft(self, mailbox: str, uid: int, **content: object) -> DraftResult:
        replacement = await self.save_draft(**content)  # type: ignore[arg-type]
        await self.delete(mailbox, [uid])
        return replacement

    async def _special_mailbox(self, flag: str, configured: str | None) -> str:
        if configured:
            return configured
        mailboxes = await self.list_mailboxes()
        for mailbox in mailboxes:
            if mailbox.special_use and mailbox.special_use.casefold() == flag.casefold():
                return mailbox.name
        raise MailForgeError(
            f"Server did not advertise {flag}; configure the corresponding MAILFORGE_*_MAILBOX"
        )

    async def set_flags(
        self, mailbox: str, uids: list[int], flags: list[str], *, add: bool, action: str
    ) -> MutationResult:
        status, state = await self._call(self.imap.set_flags, mailbox, uids, flags, add=add)
        for uid, current_flags in state.items():
            await asyncio.to_thread(
                self.cache.update_state, mailbox, status.uid_validity, uid, current_flags
            )
        return MutationResult(
            mailbox=mailbox, uid_validity=status.uid_validity, uids=uids, action=action
        )

    async def copy(self, mailbox: str, uids: list[int], destination: str) -> MutationResult:
        status = await self._call(self.imap.copy, mailbox, uids, destination)
        assert isinstance(status, MailboxStatus)
        return MutationResult(
            mailbox=mailbox,
            uid_validity=status.uid_validity,
            uids=uids,
            action="copy",
            destination=destination,
        )

    async def move(self, mailbox: str, uids: list[int], destination: str) -> MutationResult:
        status, expunged = await self._call(self.imap.move, mailbox, uids, destination)
        await asyncio.to_thread(self.cache.invalidate, mailbox, uids)
        return MutationResult(
            mailbox=mailbox,
            uid_validity=status.uid_validity,
            uids=uids,
            action="move",
            destination=destination,
            expunged=expunged,
            pending_expunge=not expunged,
            warning=None
            if expunged
            else (
                "Copied and marked \\Deleted; server lacks UID EXPUNGE, "
                "so no global expunge was run"
            ),
        )

    async def archive(self, mailbox: str, uids: list[int]) -> MutationResult:
        destination = await self._special_mailbox("\\Archive", self.settings.archive_mailbox)
        result = await self.move(mailbox, uids, destination)
        result.action = "archive"
        return result

    async def delete(self, mailbox: str, uids: list[int]) -> MutationResult:
        status, expunged = await self._call(self.imap.delete_messages, mailbox, uids)
        await asyncio.to_thread(self.cache.invalidate, mailbox, uids)
        return MutationResult(
            mailbox=mailbox,
            uid_validity=status.uid_validity,
            uids=uids,
            action="delete",
            expunged=expunged,
            pending_expunge=not expunged,
            warning=None
            if expunged
            else (
                "Messages are marked \\Deleted; server lacks UID EXPUNGE, "
                "so no global expunge was run"
            ),
        )

    async def create_mailbox(self, mailbox: str) -> OperationResult:
        await self._call(self.imap.create_mailbox, mailbox)
        return OperationResult(action="create_folder", detail=mailbox)

    async def rename_mailbox(self, mailbox: str, new_name: str) -> OperationResult:
        await self._call(self.imap.rename_mailbox, mailbox, new_name)
        await asyncio.to_thread(self.cache.invalidate_mailbox, mailbox)
        return OperationResult(action="rename_folder", detail=f"{mailbox} -> {new_name}")

    async def delete_mailbox(self, mailbox: str) -> OperationResult:
        await self._call(self.imap.delete_mailbox, mailbox)
        await asyncio.to_thread(self.cache.invalidate_mailbox, mailbox)
        return OperationResult(action="delete_folder", detail=mailbox)

    async def cache_status(self) -> CacheStatus:
        return await asyncio.to_thread(self.cache.status)

    async def clear_cache(self) -> OperationResult:
        count = await asyncio.to_thread(self.cache.clear)
        return OperationResult(
            action="clear_cache", detail=f"Removed {count} cached message bodies"
        )
