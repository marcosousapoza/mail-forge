"""Safe MIME construction and parsing helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid

from mailforge.errors import MailForgeError
from mailforge.models import Address, AttachmentInfo, AttachmentInput, MessageDetail

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def _addresses(message: Message, header: str) -> list[Address]:
    values = message.get_all(header, [])
    return [
        Address(name=name or None, email=email) for name, email in getaddresses(values) if email
    ]


def _safe_text(part: Message) -> str:
    try:
        return part.get_content()  # type: ignore[no-any-return, attr-defined]
    except (LookupError, UnicodeError):
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""


def _parts(message: Message) -> list[tuple[str, Message]]:
    found: list[tuple[str, Message]] = []

    def visit(part: Message, part_id: str) -> None:
        if part.is_multipart():
            payload = part.get_payload()
            if isinstance(payload, list):
                for index, child in enumerate(payload, 1):
                    visit(child, f"{part_id}.{index}" if part_id else str(index))
            return
        found.append((part_id or "1", part))

    visit(message, "")
    return found


def parse_message(
    raw: bytes,
    *,
    mailbox: str,
    uid_validity: int,
    uid: int,
    flags: list[str],
    size: int | None = None,
    include_body: bool = True,
) -> MessageDetail:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    text_body: str | None = None
    html_body: str | None = None
    attachments: list[AttachmentInfo] = []
    for part_id, part in _parts(message):
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        decoded = part.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) else b""
        if filename or disposition == "attachment":
            attachments.append(
                AttachmentInfo(
                    part_id=part_id,
                    filename=filename or f"attachment-{part_id}",
                    content_type=content_type,
                    size=len(payload),
                    disposition=disposition,
                    content_id=part.get("Content-ID", "").strip("<>") or None,
                )
            )
        elif include_body and content_type == "text/plain" and text_body is None:
            text_body = _safe_text(part)
        elif include_body and content_type == "text/html" and html_body is None:
            html_body = _safe_text(part)

    sender = _addresses(message, "From")
    references = re.findall(r"<[^>]+>", str(message.get("References", "")))
    message_id = str(message.get("Message-ID", "")).strip() or None
    thread_source = references[0] if references else str(message.get("In-Reply-To", ""))
    thread_id = hashlib.sha256(thread_source.encode()).hexdigest()[:24] if thread_source else None
    return MessageDetail(
        mailbox=mailbox,
        uid_validity=uid_validity,
        uid=uid,
        message_id=message_id,
        thread_id=thread_id,
        subject=str(message.get("Subject", "")),
        sender=sender[0] if sender else None,
        to=_addresses(message, "To"),
        cc=_addresses(message, "Cc"),
        bcc=_addresses(message, "Bcc"),
        reply_to=_addresses(message, "Reply-To"),
        date=str(message.get("Date", "")) or None,
        in_reply_to=str(message.get("In-Reply-To", "")).strip() or None,
        references=references,
        flags=sorted(flags),
        size=size if size is not None else len(raw),
        text_body=text_body,
        html_body=html_body,
        attachments=attachments,
    )


def extract_attachment(raw: bytes, part_id: str) -> tuple[AttachmentInfo, bytes]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    for candidate_id, part in _parts(message):
        if candidate_id != part_id:
            continue
        decoded = part.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) else b""
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if not filename and disposition != "attachment" and not part.get("Content-ID"):
            raise MailForgeError(f"MIME part {part_id} is not an attachment")
        return (
            AttachmentInfo(
                part_id=part_id,
                filename=filename or f"attachment-{part_id}",
                content_type=part.get_content_type(),
                size=len(payload),
                disposition=disposition,
                content_id=part.get("Content-ID", "").strip("<>") or None,
            ),
            payload,
        )
    raise MailForgeError(f"Attachment part {part_id} does not exist")


def compose_message(
    *,
    sender: str,
    to: list[str],
    subject: str,
    text_body: str | None,
    html_body: str | None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    attachments: list[AttachmentInput] | None = None,
) -> EmailMessage:
    if not to and not cc and not bcc:
        raise MailForgeError("At least one recipient is required")
    if text_body is None and html_body is None:
        raise MailForgeError("A text or HTML body is required")
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = " ".join(references)
    if text_body is not None:
        message.set_content(text_body)
        if html_body is not None:
            message.add_alternative(html_body, subtype="html")
    else:
        message.set_content(html_body or "", subtype="html")
    for attachment in attachments or []:
        try:
            content = base64.b64decode(attachment.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MailForgeError(f"Attachment {attachment.filename!r} is not valid base64") from exc
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise MailForgeError(
                f"Attachment {attachment.filename!r} exceeds the {MAX_ATTACHMENT_BYTES} byte limit"
            )
        maintype, separator, subtype = attachment.content_type.partition("/")
        if not separator or not maintype or not subtype:
            raise MailForgeError(f"Invalid content type: {attachment.content_type!r}")
        if attachment.content_id:
            message.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename,
                cid=f"<{attachment.content_id}>",
                disposition="inline",
            )
        else:
            message.add_attachment(
                content, maintype=maintype, subtype=subtype, filename=attachment.filename
            )
    return message


def recipient_addresses(message: EmailMessage) -> list[str]:
    result: list[str] = []
    for header in ("To", "Cc", "Bcc"):
        for _, address in getaddresses(message.get_all(header, [])):
            if address:
                result.append(address)
    return result


def envelope_sender(message: EmailMessage) -> str:
    addresses = getaddresses(message.get_all("From", []))
    if not addresses or not addresses[0][1]:
        raise MailForgeError("Message has no valid From address")
    return addresses[0][1]
