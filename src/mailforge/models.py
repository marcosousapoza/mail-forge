"""Typed public data contracts used by protocol and MCP layers."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Uid = Annotated[int, Field(ge=1, description="Stable IMAP UID within the mailbox UIDVALIDITY")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Address(Model):
    name: str | None = None
    email: str


class AttachmentInfo(Model):
    part_id: str
    filename: str
    content_type: str
    size: int
    disposition: str | None = None
    content_id: str | None = None


class AttachmentInput(Model):
    filename: str
    content_base64: str = Field(description="Base64-encoded attachment bytes")
    content_type: str = "application/octet-stream"
    content_id: str | None = None


class MailboxInfo(Model):
    name: str
    delimiter: str | None
    flags: list[str]
    special_use: str | None = None


class MailboxStatus(Model):
    mailbox: str
    messages: int
    unseen: int | None = None
    recent: int | None = None
    uid_next: int | None = None
    uid_validity: int
    highest_modseq: int | None = None


class MessageSummary(Model):
    mailbox: str
    uid_validity: int
    uid: Uid
    message_id: str | None = None
    thread_id: str | None = Field(
        default=None,
        description="RFC References-based thread hint; not a provider-native thread identifier",
    )
    subject: str
    sender: Address | None = None
    to: list[Address] = Field(default_factory=list)
    cc: list[Address] = Field(default_factory=list)
    date: str | None = None
    flags: list[str] = Field(default_factory=list)
    size: int = 0


class MessageDetail(MessageSummary):
    reply_to: list[Address] = Field(default_factory=list)
    bcc: list[Address] = Field(default_factory=list)
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)
    text_body: str | None = None
    html_body: str | None = None
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    cached: bool = False

    def summary(self) -> MessageSummary:
        return MessageSummary(
            mailbox=self.mailbox,
            uid_validity=self.uid_validity,
            uid=self.uid,
            message_id=self.message_id,
            thread_id=self.thread_id,
            subject=self.subject,
            sender=self.sender,
            to=self.to,
            cc=self.cc,
            date=self.date,
            flags=self.flags,
            size=self.size,
        )


class SearchQuery(Model):
    mailbox: str = "INBOX"
    text: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    subject: str | None = None
    since: date | None = None
    before: date | None = None
    unread: bool | None = None
    flagged: bool | None = None
    has_attachment: bool | None = None
    limit: int = Field(default=50, ge=1, le=500)


class SendResult(Model):
    message_id: str
    accepted_recipients: list[str]
    rejected_recipients: dict[str, str]
    saved_to_sent: bool


class MutationResult(Model):
    mailbox: str
    uid_validity: int
    uids: list[Uid]
    action: str
    destination: str | None = None
    expunged: bool = False
    pending_expunge: bool = False
    warning: str | None = None


class DraftResult(Model):
    mailbox: str
    uid_validity: int
    uid: Uid | None = None
    message_id: str


class CacheStatus(Model):
    enabled: bool
    path: str
    entries: int
    state_entries: int
    approximate_bytes: int
    max_bytes: int
    oldest_accessed_at: str | None = None


class OperationResult(Model):
    success: bool = True
    action: str
    detail: str | None = None


class SearchScope(Model):
    mode: Literal["selected_mailbox"] = "selected_mailbox"
    note: str = "Generic IMAP searches one mailbox at a time"
