"""Connection-scoped SMTP delivery."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from mailforge.config import SmtpConfig
from mailforge.errors import AuthenticationError, ProtocolError
from mailforge.mime import envelope_sender, recipient_addresses
from mailforge.models import SendResult


class SmtpClient:
    def __init__(self, config: SmtpConfig) -> None:
        self.config = config

    def send(self, message: EmailMessage) -> SendResult:
        client: smtplib.SMTP | smtplib.SMTP_SSL | None = None
        recipients = recipient_addresses(message)
        try:
            if self.config.security == "tls":
                client = smtplib.SMTP_SSL(
                    self.config.host,
                    self.config.port,
                    timeout=self.config.timeout,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(
                    self.config.host, self.config.port, timeout=self.config.timeout
                )
                client.ehlo()
                if self.config.security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
            client.login(self.config.username, self.config.password)
            refused = client.send_message(
                message,
                from_addr=envelope_sender(message),
                to_addrs=recipients,
            )
        except smtplib.SMTPAuthenticationError as exc:
            raise AuthenticationError("SMTP authentication failed") from exc
        except (smtplib.SMTPException, OSError, TimeoutError, ssl.SSLError) as exc:
            raise ProtocolError(f"SMTP delivery failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.quit()
                except (smtplib.SMTPException, OSError):
                    client.close()
        rejected = {
            address: f"{code} {message.decode(errors='replace')}"
            for address, (code, message) in refused.items()
        }
        accepted = [address for address in recipients if address not in refused]
        return SendResult(
            message_id=str(message["Message-ID"]),
            accepted_recipients=accepted,
            rejected_recipients=rejected,
            saved_to_sent=False,
        )
