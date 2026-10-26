from __future__ import annotations

import smtplib
from typing import Any

import pytest

from mailforge.config import SmtpConfig
from mailforge.errors import AuthenticationError
from mailforge.mime import compose_message
from mailforge.smtp import SmtpClient


class FakeSmtp:
    sent: tuple[str, list[str]] | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def ehlo(self) -> None:
        pass

    def starttls(self, **kwargs: object) -> None:
        pass

    def login(self, username: str, password: str) -> None:
        pass

    def send_message(self, message: Any, from_addr: str, to_addrs: list[str]) -> dict[str, Any]:
        self.sent = (from_addr, to_addrs)
        return {"bad@test": (550, b"rejected")}

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_smtp_starttls_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSmtp()
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: fake)
    message = compose_message(
        sender="sender@test",
        to=["ok@test", "bad@test"],
        subject="hello",
        text_body="body",
        html_body=None,
    )
    result = SmtpClient(SmtpConfig("host", 587, "u", "p", "starttls", 2)).send(message)
    assert result.accepted_recipients == ["ok@test"]
    assert "bad@test" in result.rejected_recipients
    assert fake.sent == ("sender@test", ["ok@test", "bad@test"])


def test_authentication_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadSmtp(FakeSmtp):
        def login(self, username: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", BadSmtp)
    message = compose_message(
        sender="sender@test", to=["to@test"], subject="x", text_body="x", html_body=None
    )
    with pytest.raises(AuthenticationError, match="authentication failed") as error:
        SmtpClient(SmtpConfig("host", 25, "u", "top-secret", "plain", 2)).send(message)
    assert "top-secret" not in str(error.value)
