from __future__ import annotations

import base64

import pytest

from mailforge.errors import MailForgeError
from mailforge.mime import compose_message, extract_attachment, parse_message
from mailforge.models import AttachmentInput


def test_compose_and_parse_multipart_message() -> None:
    message = compose_message(
        sender="Sender <sender@test>",
        to=["recipient@test"],
        cc=["copy@test"],
        subject="Unicode subject cafe",
        text_body="plain body",
        html_body="<p>HTML body</p>",
        attachments=[
            AttachmentInput(
                filename="report.txt",
                content_base64=base64.b64encode(b"report data").decode(),
                content_type="text/plain",
            )
        ],
    )

    parsed = parse_message(
        message.as_bytes(), mailbox="INBOX", uid_validity=9, uid=42, flags=["\\Seen"]
    )

    assert parsed.subject == "Unicode subject cafe"
    assert parsed.text_body is not None and parsed.text_body.strip() == "plain body"
    assert "HTML body" in (parsed.html_body or "")
    assert parsed.attachments[0].filename == "report.txt"
    info, content = extract_attachment(message.as_bytes(), parsed.attachments[0].part_id)
    assert info.content_type == "text/plain"
    assert content == b"report data"


def test_reply_headers_and_bcc_are_composed() -> None:
    message = compose_message(
        sender="sender@test",
        to=["to@test"],
        bcc=["hidden@test"],
        subject="Re: hello",
        text_body="reply",
        html_body=None,
        in_reply_to="<original@test>",
        references=["<root@test>", "<original@test>"],
    )
    assert message["In-Reply-To"] == "<original@test>"
    assert "hidden@test" in str(message["Bcc"])


@pytest.mark.parametrize("content", ["not base64", "%%%%"])
def test_invalid_attachment_base64_is_rejected(content: str) -> None:
    with pytest.raises(MailForgeError, match="base64"):
        compose_message(
            sender="sender@test",
            to=["to@test"],
            subject="bad",
            text_body="body",
            html_body=None,
            attachments=[AttachmentInput(filename="bad", content_base64=content)],
        )


def test_message_requires_recipient_and_body() -> None:
    with pytest.raises(MailForgeError, match="recipient"):
        compose_message(
            sender="sender@test", to=[], subject="none", text_body="body", html_body=None
        )
    with pytest.raises(MailForgeError, match="body"):
        compose_message(
            sender="sender@test", to=["to@test"], subject="none", text_body=None, html_body=None
        )
