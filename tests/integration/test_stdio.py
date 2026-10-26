from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.asyncio
@pytest.mark.integration
async def test_stdio_initialize_and_list_tools(tmp_path: Path) -> None:
    username = tmp_path / "username"
    password = tmp_path / "password"
    username.write_text("user@test", encoding="utf-8")
    password.write_text("not-a-real-password", encoding="utf-8")
    env = {
        **os.environ,
        "MAILFORGE_IMAP_HOST": "imap.invalid",
        "MAILFORGE_SMTP_HOST": "smtp.invalid",
        "MAILFORGE_IMAP_USERNAME_FILE": str(username),
        "MAILFORGE_IMAP_PASSWORD_FILE": str(password),
        "MAILFORGE_CACHE_PATH": str(tmp_path / "cache.sqlite3"),
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mailforge"],
        env=env,
        cwd=Path.cwd(),
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()

    assert initialized.serverInfo.name == "MailForge MCP"
    assert any(tool.name == "send_email" for tool in tools.tools)
