"""Official MCP SDK tool registration for stdio transport."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Annotated, Any, overload

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mailforge.config import Settings
from mailforge.errors import UnsafeOperationError
from mailforge.models import AttachmentInput, SearchQuery
from mailforge.service import MailService

mcp = FastMCP(
    "MailForge MCP",
    instructions=(
        "Provider-independent email tools over IMAP and SMTP. Message UIDs are only stable "
        "within a mailbox and UIDVALIDITY. Confirm mailbox and UIDVALIDITY before mutations."
    ),
    json_response=True,
)

_service: MailService | None = None
_service_lock = asyncio.Lock()

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


async def get_service() -> MailService:
    global _service
    if _service is None:
        async with _service_lock:
            if _service is None:
                service = MailService(Settings.from_env())
                await service.initialize()
                _service = service
    return _service


@overload
def _dump(value: BaseModel) -> dict[str, Any]: ...


@overload
def _dump(value: Sequence[BaseModel]) -> list[dict[str, Any]]: ...


def _dump(value: BaseModel | Sequence[BaseModel]) -> dict[str, Any] | list[dict[str, Any]]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return [item.model_dump(mode="json") for item in value]


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def list_emails(
    mailbox: str = "INBOX",
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    """List newest emails in one mailbox. Results contain mailbox UIDVALIDITY and message UIDs."""
    service = await get_service()
    messages = await service.search(SearchQuery(mailbox=mailbox, limit=limit))
    return {"mailbox": mailbox, "messages": _dump(messages), "count": len(messages)}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def search_emails(
    mailbox: str = "INBOX",
    text: str | None = None,
    from_address: str | None = None,
    to_address: str | None = None,
    subject: str | None = None,
    since: str | None = None,
    before: str | None = None,
    unread: bool | None = None,
    flagged: bool | None = None,
    has_attachment: bool | None = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    """Search one mailbox using portable IMAP criteria; dates use ISO YYYY-MM-DD."""
    query = SearchQuery.model_validate(
        {
            "mailbox": mailbox,
            "text": text,
            "from_address": from_address,
            "to_address": to_address,
            "subject": subject,
            "since": since,
            "before": before,
            "unread": unread,
            "flagged": flagged,
            "has_attachment": has_attachment,
            "limit": limit,
        }
    )
    messages = await (await get_service()).search(query)
    return {"mailbox": mailbox, "messages": _dump(messages), "count": len(messages)}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def read_email(mailbox: str, uid: int) -> dict[str, Any]:
    """Read an email by IMAP mailbox and UID, returning bodies, headers, flags, and attachments."""
    return _dump(await (await get_service()).get_message(mailbox, uid))


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def list_attachments(mailbox: str, uid: int) -> dict[str, Any]:
    """List attachment metadata for an email without returning attachment bytes."""
    message = await (await get_service()).get_message(mailbox, uid)
    return {
        "mailbox": mailbox,
        "uid_validity": message.uid_validity,
        "uid": uid,
        "attachments": _dump(message.attachments),
    }


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def download_attachment(mailbox: str, uid: int, part_id: str) -> dict[str, Any]:
    """Return one MIME attachment as base64. Attachment bytes are not persisted in the cache."""
    return await (await get_service()).get_attachment(mailbox, uid, part_id)


@mcp.tool(annotations=MUTATING, structured_output=True)
async def send_email(
    to: list[str],
    subject: str,
    text_body: str | None = None,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    attachments: list[AttachmentInput] | None = None,
) -> dict[str, Any]:
    """Send an email over SMTP with text/HTML bodies and optional base64 attachments."""
    result = await (await get_service()).send(
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        attachments=attachments,
    )
    return _dump(result)


@mcp.tool(annotations=MUTATING, structured_output=True)
async def reply_to_email(
    mailbox: str,
    uid: int,
    text_body: str | None = None,
    html_body: str | None = None,
    reply_all: bool = False,
    attachments: list[AttachmentInput] | None = None,
) -> dict[str, Any]:
    """Reply to an email, preserving Message-ID reference headers; optionally reply to all."""
    result = await (await get_service()).reply(
        mailbox=mailbox,
        uid=uid,
        text_body=text_body,
        html_body=html_body,
        reply_all=reply_all,
        attachments=attachments,
    )
    return _dump(result)


@mcp.tool(annotations=MUTATING, structured_output=True)
async def forward_email(
    mailbox: str,
    uid: int,
    to: list[str],
    text_body: str | None = None,
    html_body: str | None = None,
) -> dict[str, Any]:
    """Forward an email as an attached RFC 822 message to preserve its original MIME content."""
    result = await (await get_service()).forward(
        mailbox=mailbox, uid=uid, to=to, text_body=text_body, html_body=html_body
    )
    return _dump(result)


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def list_drafts(limit: Annotated[int, Field(ge=1, le=500)] = 50) -> dict[str, Any]:
    """List messages in the configured or SPECIAL-USE Drafts mailbox."""
    service = await get_service()
    mailbox = await service._special_mailbox("\\Drafts", service.settings.drafts_mailbox)
    messages = await service.search(SearchQuery(mailbox=mailbox, limit=limit))
    return {"mailbox": mailbox, "messages": _dump(messages), "count": len(messages)}


@mcp.tool(annotations=MUTATING, structured_output=True)
async def create_draft(
    to: list[str],
    subject: str,
    text_body: str | None = None,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[AttachmentInput] | None = None,
) -> dict[str, Any]:
    """Create a draft with IMAP APPEND in the Drafts mailbox."""
    result = await (await get_service()).save_draft(
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        cc=cc,
        bcc=bcc,
        attachments=attachments,
    )
    return _dump(result)


@mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
async def update_draft(
    mailbox: str,
    uid: int,
    to: list[str],
    subject: str,
    text_body: str | None = None,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[AttachmentInput] | None = None,
    confirm_replace: bool = False,
) -> dict[str, Any]:
    """Replace a draft by appending a new draft then deleting the old UID."""
    if not confirm_replace:
        raise UnsafeOperationError("Set confirm_replace=true after confirming draft replacement")
    result = await (await get_service()).update_draft(
        mailbox,
        uid,
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        cc=cc,
        bcc=bcc,
        attachments=attachments,
    )
    return _dump(result)


@mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
async def delete_draft(mailbox: str, uid: int, confirm: bool = False) -> dict[str, Any]:
    """Delete a draft UID. Requires explicit confirmation."""
    if not confirm:
        raise UnsafeOperationError("Set confirm=true after confirming draft deletion")
    return _dump(await (await get_service()).delete(mailbox, [uid]))


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def list_folders() -> dict[str, Any]:
    """List IMAP folders, hierarchy delimiters, and SPECIAL-USE attributes."""
    folders = await (await get_service()).list_mailboxes()
    return {"folders": _dump(folders), "count": len(folders)}


@mcp.tool(annotations=MUTATING, structured_output=True)
async def create_folder(name: str) -> dict[str, Any]:
    """Create an IMAP folder."""
    return _dump(await (await get_service()).create_mailbox(name))


@mcp.tool(annotations=MUTATING, structured_output=True)
async def rename_folder(name: str, new_name: str) -> dict[str, Any]:
    """Rename an IMAP folder."""
    return _dump(await (await get_service()).rename_mailbox(name, new_name))


@mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
async def delete_folder(name: str, confirm: bool = False) -> dict[str, Any]:
    """Delete an IMAP folder. Behavior for contained mail varies; confirmation is required."""
    if not confirm:
        raise UnsafeOperationError("Set confirm=true after confirming folder deletion")
    return _dump(await (await get_service()).delete_mailbox(name))


@mcp.tool(annotations=MUTATING, structured_output=True)
async def copy_emails(mailbox: str, uids: list[int], destination: str) -> dict[str, Any]:
    """Copy message UIDs to another folder, leaving originals unchanged."""
    return _dump(await (await get_service()).copy(mailbox, uids, destination))


@mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
async def move_emails(
    mailbox: str, uids: list[int], destination: str, confirm: bool = False
) -> dict[str, Any]:
    """Move UIDs. Without MOVE, use copy and UID-scoped deletion. Confirmation is required."""
    if not confirm:
        raise UnsafeOperationError("Set confirm=true after confirming message movement")
    return _dump(await (await get_service()).move(mailbox, uids, destination))


@mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
async def archive_emails(mailbox: str, uids: list[int], confirm: bool = False) -> dict[str, Any]:
    """Move message UIDs to the configured or SPECIAL-USE Archive folder."""
    if not confirm:
        raise UnsafeOperationError("Set confirm=true after confirming archival")
    return _dump(await (await get_service()).archive(mailbox, uids))


@mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
async def delete_emails(mailbox: str, uids: list[int], confirm: bool = False) -> dict[str, Any]:
    """Delete and UID-expunge only requested UIDs when supported. Never run global EXPUNGE."""
    if not confirm:
        raise UnsafeOperationError("Set confirm=true after confirming message deletion")
    return _dump(await (await get_service()).delete(mailbox, uids))


async def _flag(mailbox: str, uids: list[int], flag: str, add: bool, action: str) -> dict[str, Any]:
    return _dump(
        await (await get_service()).set_flags(mailbox, uids, [flag], add=add, action=action)
    )


@mcp.tool(annotations=MUTATING, structured_output=True)
async def mark_emails_read(mailbox: str, uids: list[int]) -> dict[str, Any]:
    """Add the IMAP Seen flag to message UIDs."""
    return await _flag(mailbox, uids, "\\Seen", True, "mark_read")


@mcp.tool(annotations=MUTATING, structured_output=True)
async def mark_emails_unread(mailbox: str, uids: list[int]) -> dict[str, Any]:
    """Remove the IMAP Seen flag from message UIDs."""
    return await _flag(mailbox, uids, "\\Seen", False, "mark_unread")


@mcp.tool(annotations=MUTATING, structured_output=True)
async def flag_emails(mailbox: str, uids: list[int]) -> dict[str, Any]:
    """Add the IMAP Flagged flag to message UIDs."""
    return await _flag(mailbox, uids, "\\Flagged", True, "flag")


@mcp.tool(annotations=MUTATING, structured_output=True)
async def unflag_emails(mailbox: str, uids: list[int]) -> dict[str, Any]:
    """Remove the IMAP Flagged flag from message UIDs."""
    return await _flag(mailbox, uids, "\\Flagged", False, "unflag")


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_mailbox_info(mailbox: str = "INBOX") -> dict[str, Any]:
    """Return counts, UIDVALIDITY, UIDNEXT, MODSEQ when available, and server capabilities."""
    service = await get_service()
    status, capabilities = await asyncio.gather(
        service.mailbox_status(mailbox), service.capabilities()
    )
    return {"status": _dump(status), "capabilities": capabilities}


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def cache_status() -> dict[str, Any]:
    """Report local cache state and parsed-message storage usage. Credentials are never cached."""
    return _dump(await (await get_service()).cache_status())


@mcp.tool(annotations=DESTRUCTIVE, structured_output=True)
async def clear_cache(confirm: bool = False) -> dict[str, Any]:
    """Clear only local cached data. This never changes or deletes remote email."""
    if not confirm:
        raise UnsafeOperationError("Set confirm=true to clear local cache data")
    return _dump(await (await get_service()).clear_cache())
