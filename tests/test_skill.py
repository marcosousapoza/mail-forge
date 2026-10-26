from __future__ import annotations

from pathlib import Path

import pytest

from mailforge.server import mcp

SKILL_PATH = Path(__file__).parents[1] / "skills" / "mailforge-email" / "SKILL.md"


def test_skill_has_portable_frontmatter() -> None:
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert content.startswith("---\nname: mailforge-email\n")
    assert "description: Use when reading, searching, drafting, sending" in content
    assert "repository: https://github.com/marcosousapoza/mail-forge" in content


@pytest.mark.asyncio
async def test_skill_references_registered_mailforge_tools() -> None:
    documented = {
        "list_emails",
        "search_emails",
        "read_email",
        "list_attachments",
        "download_attachment",
        "send_email",
        "reply_to_email",
        "forward_email",
        "list_drafts",
        "create_draft",
        "update_draft",
        "delete_draft",
        "list_folders",
        "create_folder",
        "rename_folder",
        "delete_folder",
        "copy_emails",
        "move_emails",
        "archive_emails",
        "delete_emails",
        "mark_emails_read",
        "mark_emails_unread",
        "flag_emails",
        "unflag_emails",
        "get_mailbox_info",
        "cache_status",
        "clear_cache",
    }
    content = SKILL_PATH.read_text(encoding="utf-8")
    registered = {tool.name for tool in await mcp.list_tools()}

    assert documented == registered
    assert all(f"`{name}`" in content for name in documented)
