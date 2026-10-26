from __future__ import annotations

import os

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.asyncio
@pytest.mark.integration
async def test_container_stdio_smoke() -> None:
    image = os.getenv("MAILFORGE_TEST_IMAGE")
    if not image:
        pytest.skip("MAILFORGE_TEST_IMAGE is not set")
    parameters = StdioServerParameters(
        command="podman",
        args=[
            "run",
            "--rm",
            "-i",
            "--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
            "--secret=mailforge_ci_user,target=mailforge_imap_username",
            "--secret=mailforge_ci_password,target=mailforge_imap_password",
            "--env=MAILFORGE_IMAP_HOST=imap.invalid",
            "--env=MAILFORGE_SMTP_HOST=smtp.invalid",
            "--env=MAILFORGE_CACHE_ENABLED=false",
            image,
        ],
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        result = await session.list_tools()
    assert initialized.serverInfo.name == "MailForge MCP"
    assert len(result.tools) >= 20
