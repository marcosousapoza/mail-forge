"""Synchronous, connection-scoped generic IMAP operations."""

from __future__ import annotations

import base64
import binascii
import imaplib
import re
import ssl
from collections.abc import Generator
from contextlib import contextmanager, suppress
from email.message import EmailMessage

from mailforge.config import ImapConfig
from mailforge.errors import AuthenticationError, MessageNotFoundError, ProtocolError
from mailforge.mime import extract_attachment, parse_message
from mailforge.models import (
    AttachmentInfo,
    MailboxInfo,
    MailboxStatus,
    MessageDetail,
    MessageSummary,
    SearchQuery,
)

_FETCH_UID = re.compile(rb"\bUID (\d+)\b")
_FETCH_FLAGS = re.compile(rb"\bFLAGS \(([^)]*)\)")
_FETCH_SIZE = re.compile(rb"\bRFC822\.SIZE (\d+)\b")
_LIST = re.compile(rb'^\((.*?)\)\s+(?:"([^"]*)"|NIL)\s+(.+)$')


def encode_mailbox(value: str) -> str:
    """Encode an IMAP mailbox name with modified UTF-7 (RFC 3501)."""
    output: list[str] = []
    non_ascii: list[str] = []

    def flush() -> None:
        if not non_ascii:
            return
        encoded = base64.b64encode("".join(non_ascii).encode("utf-16be")).decode().rstrip("=")
        output.append("&" + encoded.replace("/", ",") + "-")
        non_ascii.clear()

    for character in value:
        if " " <= character <= "~":
            flush()
            output.append("&-" if character == "&" else character)
        else:
            non_ascii.append(character)
    flush()
    return "".join(output)


def decode_mailbox(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        ampersand = value.find("&", index)
        if ampersand < 0:
            output.append(value[index:])
            break
        output.append(value[index:ampersand])
        end = value.find("-", ampersand)
        if end < 0:
            output.append(value[ampersand:])
            break
        encoded = value[ampersand + 1 : end]
        if not encoded:
            output.append("&")
        else:
            padding = "=" * (-len(encoded) % 4)
            try:
                data = base64.b64decode(encoded.replace(",", "/") + padding)
                output.append(data.decode("utf-16be"))
            except (binascii.Error, UnicodeError):
                output.append(value[ampersand : end + 1])
        index = end + 1
    return "".join(output)


def _unquote(value: bytes) -> str:
    if value.startswith(b'"') and value.endswith(b'"'):
        value = value[1:-1].replace(b'\\"', b'"').replace(b"\\\\", bytes([92]))
    return decode_mailbox(value.decode("ascii", errors="replace"))


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _number(data: object) -> int | None:
    if isinstance(data, tuple) and len(data) >= 2:
        return _number(data[1])
    if isinstance(data, list) and data and data[0] is not None:
        raw = data[0]
        if isinstance(raw, bytes):
            match = re.search(rb"\d+", raw)
            return int(match.group()) if match else None
    return None


class ImapClient:
    def __init__(self, config: ImapConfig) -> None:
        self.config = config

    @contextmanager
    def connection(self) -> Generator[imaplib.IMAP4, None, None]:
        client: imaplib.IMAP4 | None = None
        try:
            if self.config.use_tls:
                client = imaplib.IMAP4_SSL(
                    self.config.host,
                    self.config.port,
                    ssl_context=ssl.create_default_context(),
                    timeout=self.config.timeout,
                )
            else:
                client = imaplib.IMAP4(
                    self.config.host, self.config.port, timeout=self.config.timeout
                )
                client.starttls(ssl_context=ssl.create_default_context())
            client.login(self.config.username, self.config.password)
            yield client
        except imaplib.IMAP4.error as exc:
            message = str(exc)
            if "AUTH" in message.upper() or "LOGIN" in message.upper():
                raise AuthenticationError("IMAP authentication failed") from exc
            raise ProtocolError(f"IMAP operation failed: {message}") from exc
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            raise ProtocolError(f"Unable to connect to IMAP server: {exc}") from exc
        finally:
            if client is not None:
                with suppress(imaplib.IMAP4.error, OSError):
                    client.logout()

    @staticmethod
    def _ok(status: str, data: object, operation: str) -> None:
        if status != "OK":
            raise ProtocolError(f"IMAP {operation} failed: {data!r}")

    @staticmethod
    def _capabilities(client: imaplib.IMAP4) -> set[str]:
        return {value.upper() for value in client.capabilities}

    def capabilities(self) -> list[str]:
        with self.connection() as client:
            return sorted(self._capabilities(client))

    def list_mailboxes(self) -> list[MailboxInfo]:
        with self.connection() as client:
            status, rows = client.list()
            self._ok(status, rows, "LIST")
        result: list[MailboxInfo] = []
        for row in rows or []:
            if not isinstance(row, bytes):
                continue
            match = _LIST.match(row)
            if not match:
                continue
            flags = [item.decode(errors="replace") for item in match.group(1).split()]
            special = next(
                (
                    flag
                    for flag in flags
                    if flag.lower()
                    in {
                        "\\all",
                        "\\archive",
                        "\\drafts",
                        "\\flagged",
                        "\\junk",
                        "\\sent",
                        "\\trash",
                    }
                ),
                None,
            )
            delimiter = (
                match.group(2).decode(errors="replace") if match.group(2) is not None else None
            )
            result.append(
                MailboxInfo(
                    name=_unquote(match.group(3)),
                    delimiter=delimiter,
                    flags=flags,
                    special_use=special,
                )
            )
        return result

    def _select(self, client: imaplib.IMAP4, mailbox: str, *, readonly: bool) -> MailboxStatus:
        status, count = client.select(encode_mailbox(mailbox), readonly=readonly)
        self._ok(status, count, f"SELECT {mailbox}")
        uid_validity = _number(client.response("UIDVALIDITY"))
        if uid_validity is None:
            raise ProtocolError("IMAP server did not provide UIDVALIDITY")
        return MailboxStatus(
            mailbox=mailbox,
            messages=_number(count) or 0,
            unseen=_number(client.response("UNSEEN")),
            recent=_number(client.response("RECENT")),
            uid_next=_number(client.response("UIDNEXT")),
            uid_validity=uid_validity,
            highest_modseq=_number(client.response("HIGHESTMODSEQ")),
        )

    def mailbox_status(self, mailbox: str) -> MailboxStatus:
        with self.connection() as client:
            return self._select(client, mailbox, readonly=True)

    @staticmethod
    def _parse_fetch(rows: list[object], mailbox: str, uid_validity: int) -> list[MessageDetail]:
        messages: list[MessageDetail] = []
        for row in rows:
            if not isinstance(row, tuple) or len(row) < 2 or not isinstance(row[0], bytes):
                continue
            metadata, raw = row[0], row[1]
            if not isinstance(raw, bytes):
                continue
            uid_match = _FETCH_UID.search(metadata)
            if uid_match is None:
                continue
            flags_match = _FETCH_FLAGS.search(metadata)
            flags = (
                [flag.decode(errors="replace") for flag in flags_match.group(1).split()]
                if flags_match
                else []
            )
            size_match = _FETCH_SIZE.search(metadata)
            messages.append(
                parse_message(
                    raw,
                    mailbox=mailbox,
                    uid_validity=uid_validity,
                    uid=int(uid_match.group(1)),
                    flags=flags,
                    size=int(size_match.group(1)) if size_match else len(raw),
                )
            )
        return messages

    @staticmethod
    def _uid_set(uids: list[int]) -> str:
        if not uids or any(uid < 1 for uid in uids):
            raise ProtocolError("At least one positive UID is required")
        return ",".join(str(uid) for uid in sorted(set(uids)))

    def get_message(self, mailbox: str, uid: int) -> tuple[MailboxStatus, MessageDetail]:
        mailbox_status, message, _ = self.get_raw_message(mailbox, uid)
        return mailbox_status, message

    def get_raw_message(self, mailbox: str, uid: int) -> tuple[MailboxStatus, MessageDetail, bytes]:
        with self.connection() as client:
            mailbox_status = self._select(client, mailbox, readonly=True)
            status, rows = client.uid("FETCH", str(uid), "(UID FLAGS RFC822.SIZE BODY.PEEK[])")
            self._ok(status, rows, "UID FETCH")
        messages = self._parse_fetch(rows or [], mailbox, mailbox_status.uid_validity)
        if not messages:
            raise MessageNotFoundError(f"UID {uid} does not exist in {mailbox}")
        raw = next(
            (row[1] for row in rows or [] if isinstance(row, tuple) and isinstance(row[1], bytes)),
            None,
        )
        if raw is None:
            raise ProtocolError("IMAP server returned no message content")
        return mailbox_status, messages[0], raw

    def get_attachment(
        self, mailbox: str, uid: int, part_id: str
    ) -> tuple[MailboxStatus, AttachmentInfo, bytes]:
        mailbox_status, _, raw = self.get_raw_message(mailbox, uid)
        attachment, content = extract_attachment(raw, part_id)
        return mailbox_status, attachment, content

    def get_flags(self, mailbox: str, uid: int) -> tuple[MailboxStatus, list[str]]:
        with self.connection() as client:
            mailbox_status = self._select(client, mailbox, readonly=True)
            status, rows = client.uid("FETCH", str(uid), "(UID FLAGS)")
            self._ok(status, rows, "UID FETCH flags")
        for row in rows or []:
            metadata = row[0] if isinstance(row, tuple) else row
            if not isinstance(metadata, bytes) or _FETCH_UID.search(metadata) is None:
                continue
            match = _FETCH_FLAGS.search(metadata)
            flags = (
                [item.decode(errors="replace") for item in match.group(1).split()] if match else []
            )
            return mailbox_status, flags
        raise MessageNotFoundError(f"UID {uid} does not exist in {mailbox}")

    @staticmethod
    def _search_criteria(query: SearchQuery) -> list[str]:
        criteria: list[str] = []
        if query.text:
            criteria.extend(["TEXT", _quoted(query.text)])
        if query.from_address:
            criteria.extend(["FROM", _quoted(query.from_address)])
        if query.to_address:
            criteria.extend(["TO", _quoted(query.to_address)])
        if query.subject:
            criteria.extend(["SUBJECT", _quoted(query.subject)])
        if query.since:
            criteria.extend(["SINCE", query.since.strftime("%d-%b-%Y")])
        if query.before:
            criteria.extend(["BEFORE", query.before.strftime("%d-%b-%Y")])
        if query.unread is not None:
            criteria.append("UNSEEN" if query.unread else "SEEN")
        if query.flagged is not None:
            criteria.append("FLAGGED" if query.flagged else "UNFLAGGED")
        if query.has_attachment is not None:
            criteria.extend(
                ["HEADER", "Content-Type", _quoted("multipart/")]
                if query.has_attachment
                else ["NOT", "HEADER", "Content-Type", _quoted("multipart/")]
            )
        return criteria or ["ALL"]

    def search(self, query: SearchQuery) -> tuple[MailboxStatus, list[MessageSummary]]:
        with self.connection() as client:
            mailbox_status = self._select(client, query.mailbox, readonly=True)
            status, data = client.uid(
                "SEARCH",
                None,  # type: ignore[arg-type]  # None selects US-ASCII
                *self._search_criteria(query),
            )
            self._ok(status, data, "UID SEARCH")
            raw_uids = data[0].split() if data and isinstance(data[0], bytes) else []
            selected = raw_uids[-query.limit :]
            if not selected:
                return mailbox_status, []
            uid_set = b",".join(selected).decode()
            fields = "DATE FROM TO CC SUBJECT MESSAGE-ID REFERENCES IN-REPLY-TO"
            status, rows = client.uid(
                "FETCH", uid_set, f"(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({fields})])"
            )
            self._ok(status, rows, "UID FETCH search results")
        details = self._parse_fetch(rows or [], query.mailbox, mailbox_status.uid_validity)
        by_uid = {item.uid: item for item in details}
        ordered = [by_uid[int(uid)] for uid in reversed(selected) if int(uid) in by_uid]
        return mailbox_status, [item.summary() for item in ordered]

    def set_flags(
        self, mailbox: str, uids: list[int], flags: list[str], *, add: bool
    ) -> tuple[MailboxStatus, dict[int, list[str]]]:
        uid_set = self._uid_set(uids)
        operation = "+FLAGS.SILENT" if add else "-FLAGS.SILENT"
        with self.connection() as client:
            mailbox_status = self._select(client, mailbox, readonly=False)
            status, data = client.uid("STORE", uid_set, operation, f"({' '.join(flags)})")
            self._ok(status, data, "UID STORE")
            results: dict[int, list[str]] = {}
            for uid in sorted(set(uids)):
                status, rows = client.uid("FETCH", str(uid), "(UID FLAGS)")
                self._ok(status, rows, "UID FETCH flags")
                for row in rows or []:
                    raw = row[0] if isinstance(row, tuple) else row
                    if isinstance(raw, bytes) and (match := _FETCH_FLAGS.search(raw)):
                        results[uid] = [
                            flag.decode(errors="replace") for flag in match.group(1).split()
                        ]
            return mailbox_status, results

    def copy(self, mailbox: str, uids: list[int], destination: str) -> MailboxStatus:
        with self.connection() as client:
            mailbox_status = self._select(client, mailbox, readonly=False)
            status, data = client.uid("COPY", self._uid_set(uids), encode_mailbox(destination))
            self._ok(status, data, "UID COPY")
            return mailbox_status

    def move(self, mailbox: str, uids: list[int], destination: str) -> tuple[MailboxStatus, bool]:
        uid_set = self._uid_set(uids)
        with self.connection() as client:
            mailbox_status = self._select(client, mailbox, readonly=False)
            capabilities = self._capabilities(client)
            if "MOVE" in capabilities:
                status, data = client.uid("MOVE", uid_set, encode_mailbox(destination))
                self._ok(status, data, "UID MOVE")
                return mailbox_status, True
            status, data = client.uid("COPY", uid_set, encode_mailbox(destination))
            self._ok(status, data, "UID COPY")
            status, data = client.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Deleted)")
            self._ok(status, data, "UID STORE \\Deleted")
            if "UIDPLUS" in capabilities:
                status, data = client.uid("EXPUNGE", uid_set)
                self._ok(status, data, "UID EXPUNGE")
                return mailbox_status, True
            return mailbox_status, False

    def delete_messages(self, mailbox: str, uids: list[int]) -> tuple[MailboxStatus, bool]:
        uid_set = self._uid_set(uids)
        with self.connection() as client:
            mailbox_status = self._select(client, mailbox, readonly=False)
            status, data = client.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Deleted)")
            self._ok(status, data, "UID STORE \\Deleted")
            if "UIDPLUS" in self._capabilities(client):
                status, data = client.uid("EXPUNGE", uid_set)
                self._ok(status, data, "UID EXPUNGE")
                return mailbox_status, True
            return mailbox_status, False

    def create_mailbox(self, mailbox: str) -> None:
        with self.connection() as client:
            status, data = client.create(encode_mailbox(mailbox))
            self._ok(status, data, "CREATE")

    def rename_mailbox(self, mailbox: str, new_name: str) -> None:
        with self.connection() as client:
            status, data = client.rename(encode_mailbox(mailbox), encode_mailbox(new_name))
            self._ok(status, data, "RENAME")

    def delete_mailbox(self, mailbox: str) -> None:
        with self.connection() as client:
            status, data = client.delete(encode_mailbox(mailbox))
            self._ok(status, data, "DELETE mailbox")

    def append_message(
        self, mailbox: str, message: EmailMessage, flags: str = "\\Draft"
    ) -> tuple[int, int | None]:
        with self.connection() as client:
            status, data = client.append(
                encode_mailbox(mailbox), f"({flags})" if flags else None, None, message.as_bytes()
            )
            self._ok(status, data, "APPEND")
            response = client.response("APPENDUID")
            append_uid = None
            if isinstance(response, tuple) and isinstance(response[1], list) and response[1]:
                raw = response[1][0]
                if isinstance(raw, bytes):
                    numbers = re.findall(rb"\d+", raw)
                    append_uid = int(numbers[-1]) if len(numbers) >= 2 else None
            selected = self._select(client, mailbox, readonly=True)
            return selected.uid_validity, append_uid
