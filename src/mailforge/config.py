"""Environment and Podman secret based configuration."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from mailforge.errors import ConfigurationError


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _secret(name: str, *, required: bool = True) -> str | None:
    """Load a secret from a file; direct values are intentionally unsupported."""
    path_value = os.getenv(f"{name}_FILE", f"/run/secrets/{name.lower()}")
    path = Path(path_value)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if required:
            raise ConfigurationError(
                f"Required secret {name} was not found at {path}; set {name}_FILE"
            ) from None
        return None
    except OSError as exc:
        raise ConfigurationError(f"Unable to read secret {name} from {path}: {exc}") from exc
    if not value and required:
        raise ConfigurationError(f"Secret {name} at {path} is empty")
    return value or None


@dataclass(frozen=True, slots=True)
class ImapConfig:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    timeout: int


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    security: str
    timeout: int


@dataclass(frozen=True, slots=True)
class CacheConfig:
    enabled: bool
    path: Path
    max_bytes: int
    eviction_age_seconds: int
    flags_freshness_seconds: int


@dataclass(frozen=True, slots=True)
class Settings:
    imap: ImapConfig
    smtp: SmtpConfig
    cache: CacheConfig
    account_id: str
    default_from: str
    archive_mailbox: str | None
    drafts_mailbox: str | None
    sent_mailbox: str | None
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        imap_host = os.getenv("MAILFORGE_IMAP_HOST", "").strip()
        smtp_host = os.getenv("MAILFORGE_SMTP_HOST", "").strip()
        username = _secret("MAILFORGE_IMAP_USERNAME")
        password = _secret("MAILFORGE_IMAP_PASSWORD")
        smtp_username = _secret("MAILFORGE_SMTP_USERNAME", required=False) or username
        smtp_password = _secret("MAILFORGE_SMTP_PASSWORD", required=False) or password
        if not imap_host or not smtp_host:
            raise ConfigurationError("MAILFORGE_IMAP_HOST and MAILFORGE_SMTP_HOST are required")
        assert username is not None and password is not None
        assert smtp_username is not None and smtp_password is not None
        security = os.getenv("MAILFORGE_SMTP_SECURITY", "starttls").strip().lower()
        if security not in {"tls", "starttls", "plain"}:
            raise ConfigurationError("MAILFORGE_SMTP_SECURITY must be tls, starttls, or plain")
        default_from = os.getenv("MAILFORGE_DEFAULT_FROM", smtp_username).strip()
        account_id = os.getenv("MAILFORGE_ACCOUNT_ID", "").strip()
        if not account_id:
            identity = f"{imap_host}\0{username}".encode()
            account_id = hashlib.sha256(identity).hexdigest()[:24]
        return cls(
            imap=ImapConfig(
                host=imap_host,
                port=_env_int("MAILFORGE_IMAP_PORT", 993, 1),
                username=username,
                password=password,
                use_tls=_env_bool("MAILFORGE_IMAP_TLS", True),
                timeout=_env_int("MAILFORGE_CONNECTION_TIMEOUT", 30, 1),
            ),
            smtp=SmtpConfig(
                host=smtp_host,
                port=_env_int("MAILFORGE_SMTP_PORT", 587, 1),
                username=smtp_username,
                password=smtp_password,
                security=security,
                timeout=_env_int("MAILFORGE_CONNECTION_TIMEOUT", 30, 1),
            ),
            cache=CacheConfig(
                enabled=_env_bool("MAILFORGE_CACHE_ENABLED", True),
                path=Path(os.getenv("MAILFORGE_CACHE_PATH", "/var/cache/mailforge/cache.sqlite3")),
                max_bytes=_env_int("MAILFORGE_CACHE_MAX_MB", 512, 1) * 1024 * 1024,
                eviction_age_seconds=_env_int("MAILFORGE_CACHE_EVICTION_DAYS", 30, 1) * 86400,
                flags_freshness_seconds=_env_int("MAILFORGE_FLAGS_FRESHNESS_SECONDS", 60),
            ),
            account_id=account_id,
            default_from=default_from,
            archive_mailbox=os.getenv("MAILFORGE_ARCHIVE_MAILBOX") or None,
            drafts_mailbox=os.getenv("MAILFORGE_DRAFTS_MAILBOX") or None,
            sent_mailbox=os.getenv("MAILFORGE_SENT_MAILBOX") or None,
            log_level=os.getenv("MAILFORGE_LOG_LEVEL", "INFO").upper(),
        )
