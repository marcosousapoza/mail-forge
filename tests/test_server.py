from __future__ import annotations

import pytest

from mailforge.errors import UnsafeOperationError
from mailforge.server import delete_emails, mcp


@pytest.mark.asyncio
async def test_tool_contracts_are_registered() -> None:
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    expected = {
        "list_emails",
        "read_email",
        "search_emails",
        "send_email",
        "reply_to_email",
        "forward_email",
        "create_draft",
        "download_attachment",
        "move_emails",
        "delete_emails",
        "get_mailbox_info",
        "cache_status",
        "clear_cache",
    }
    assert expected <= by_name.keys()
    assert by_name["delete_emails"].annotations.destructiveHint is True
    assert by_name["read_email"].annotations.readOnlyHint is True
    assert "uid" in by_name["read_email"].inputSchema["properties"]
    assert by_name["send_email"].outputSchema is not None


@pytest.mark.asyncio
async def test_destructive_tools_require_confirmation() -> None:
    with pytest.raises(UnsafeOperationError, match="confirm=true"):
        await delete_emails("INBOX", [5], confirm=False)
