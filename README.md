# MailForge MCP

MailForge MCP is a self-hosted Model Context Protocol server that exposes email through portable
IMAP and SMTP. It is designed for MCP clients that expect a Gmail-like email tool set but need to
work with conventional providers. The server communicates only over MCP stdio, runs as an
unprivileged container user, reads credentials from secret files, and optionally keeps a bounded
SQLite cache of parsed message content.

## Features

| Area | Support |
| --- | --- |
| Messages | List, read, portable search, reply, reply-all, forward, send |
| State | Read/unread and flagged/unflagged IMAP flags |
| Organization | List/create/rename/delete folders; copy, move, archive, delete messages |
| Drafts | List, create, replace, and delete through IMAP APPEND |
| Attachments | List metadata, download as base64, send base64 inputs; no persistent byte cache |
| Protocol security | IMAPS or IMAP STARTTLS; SMTPS, SMTP STARTTLS, or explicitly configured plain SMTP |
| IMAP extensions | Capability detection, SPECIAL-USE, MOVE, UIDPLUS, CONDSTORE metadata |
| Cache | SQLite WAL, UIDVALIDITY invalidation, body read-through, size/age eviction |
| Runtime | Python 3.12, official Python MCP SDK, Astral uv, rootless Podman |

## Architecture

```text
MCP client <-> stdio <-> FastMCP tools <-> async MailService
                                             |       |
                                  asyncio.to_thread   SQLite cache
                                             |
                                      IMAP / SMTP
```

`src/mailforge/server.py` contains the MCP contracts and no protocol logic. `service.py` owns
async orchestration and bounds blocking calls. `imap.py` and `smtp.py` use one authenticated
connection per operation, avoiding shared connection state across worker threads. `mime.py`
handles message construction and parsing. `cache.py` stores parsed content and mutable remote
state separately. Configuration and secret loading are isolated in `config.py`.

IMAP is always authoritative. Before returning a cached body, MailForge selects the mailbox and
fetches the UID's current flags. A missing UID is therefore never replaced with an old cached
message. Cache keys are `(account_id, mailbox, UIDVALIDITY, UID)`, and a UIDVALIDITY change removes
all old entries for that mailbox.

## MCP Tools

All results are structured JSON. Message mutations require a mailbox and UID because IMAP UIDs are
not global. Destructive operations also require an explicit confirmation boolean so an MCP client
can obtain user approval first.

| Tool | Purpose |
| --- | --- |
| `list_emails` | List newest messages in one mailbox |
| `search_emails` | Search by text, sender, recipient, subject, dates, state, or attachment hint |
| `read_email` | Read headers, text/HTML bodies, flags, and attachment metadata |
| `send_email` | Send text/HTML mail and attachments over SMTP |
| `reply_to_email` | Reply or reply-all with RFC reference headers |
| `forward_email` | Forward the original message as a `message/rfc822` attachment |
| `list_attachments` | Return attachment metadata only |
| `download_attachment` | Return one MIME part as base64 without caching its bytes |
| `list_drafts` | List the Drafts SPECIAL-USE/configured folder |
| `create_draft` | Append a message with the `\Draft` flag |
| `update_draft` | Append replacement first, then delete the old draft UID |
| `delete_draft` | Delete one draft UID after confirmation |
| `list_folders` | List folders, delimiters, attributes, and SPECIAL-USE roles |
| `create_folder` | Create an IMAP folder |
| `rename_folder` | Rename an IMAP folder |
| `delete_folder` | Delete a folder after confirmation |
| `copy_emails` | Copy UIDs to another folder |
| `move_emails` | Move UIDs, using safe copy/delete fallback if needed |
| `archive_emails` | Move UIDs to the Archive SPECIAL-USE/configured folder |
| `delete_emails` | Mark deleted and use UID EXPUNGE when available |
| `mark_emails_read` / `mark_emails_unread` | Add/remove `\Seen` |
| `flag_emails` / `unflag_emails` | Add/remove `\Flagged` |
| `get_mailbox_info` | Return counts, UID metadata, MODSEQ, and capabilities |
| `cache_status` | Return local entry counts and bounded storage usage |
| `clear_cache` | Clear local data only; never changes remote messages |

If a server lacks `MOVE`, MailForge performs `UID COPY` and marks only the requested UIDs
`\Deleted`. If it also lacks `UIDPLUS`, MailForge does **not** issue global `EXPUNGE`; the result
sets `pending_expunge: true`. This avoids expunging unrelated messages marked by another client.

## Configuration

Non-sensitive settings use environment variables. Passwords and usernames are read from files;
there are intentionally no password command-line flags or direct password environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAILFORGE_IMAP_HOST` | required | IMAP hostname |
| `MAILFORGE_IMAP_PORT` | `993` | IMAP port |
| `MAILFORGE_IMAP_TLS` | `true` | `true`: implicit TLS; `false`: connect then require STARTTLS |
| `MAILFORGE_SMTP_HOST` | required | SMTP hostname |
| `MAILFORGE_SMTP_PORT` | `587` | SMTP port |
| `MAILFORGE_SMTP_SECURITY` | `starttls` | `tls`, `starttls`, or `plain` |
| `MAILFORGE_DEFAULT_FROM` | SMTP username | Envelope/header sender |
| `MAILFORGE_CONNECTION_TIMEOUT` | `30` | Network timeout in seconds |
| `MAILFORGE_ACCOUNT_ID` | derived | Stable, non-secret cache account namespace |
| `MAILFORGE_ARCHIVE_MAILBOX` | discovered | Archive folder override |
| `MAILFORGE_DRAFTS_MAILBOX` | discovered | Drafts folder override |
| `MAILFORGE_SENT_MAILBOX` | unset | If set, append successfully sent mail here |
| `MAILFORGE_CACHE_ENABLED` | `true` | Enable persistent cache |
| `MAILFORGE_CACHE_PATH` | `/var/cache/mailforge/cache.sqlite3` | SQLite path |
| `MAILFORGE_CACHE_MAX_MB` | `512` | Parsed-content size bound |
| `MAILFORGE_CACHE_EVICTION_DAYS` | `30` | Maximum unaccessed age |
| `MAILFORGE_FLAGS_FRESHNESS_SECONDS` | `60` | Maximum mutable-state freshness bound |
| `MAILFORGE_LOG_LEVEL` | `INFO` | Structured stderr log level |

Secret file variables default to the shown Podman secret targets:

| Variable | Default path |
| --- | --- |
| `MAILFORGE_IMAP_USERNAME_FILE` | `/run/secrets/mailforge_imap_username` |
| `MAILFORGE_IMAP_PASSWORD_FILE` | `/run/secrets/mailforge_imap_password` |
| `MAILFORGE_SMTP_USERNAME_FILE` | `/run/secrets/mailforge_smtp_username` |
| `MAILFORGE_SMTP_PASSWORD_FILE` | `/run/secrets/mailforge_smtp_password` |

SMTP secrets are optional and fall back to the IMAP credentials. Use separate credentials where
the provider supports them. See `.env.example` for a non-secret template.

## Podman Setup

Pull the published image:

```sh
podman pull ghcr.io/marcosousapoza/mailforge-mcp:latest
```

Alternatively, build it from source:

```sh
podman build -t localhost/mailforge-mcp:latest -f Containerfile .
podman volume create mailforge-cache
```

Create secrets without putting values in shell arguments or image layers:

```sh
read -r IMAP_USER
printf '%s' "$IMAP_USER" | podman secret create mailforge-imap-user -
read -rs IMAP_PASSWORD
printf '%s' "$IMAP_PASSWORD" | podman secret create mailforge-imap-password -
unset IMAP_USER IMAP_PASSWORD
```

For distinct SMTP credentials, create `mailforge-smtp-user` and `mailforge-smtp-password` the same
way. Rootless Podman stores and mounts secrets for the current user. Inside the container they are
read-only files available to UID 65532; do not bind-mount host secret files with broader
permissions. To rotate a secret, stop active instances, run `podman secret rm NAME`, recreate it,
and start a new container. Existing containers retain their original mounted secret.

Run over stdio without a TTY:

```sh
podman run --rm -i \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --volume mailforge-cache:/var/cache/mailforge:U \
  --secret mailforge-imap-user,target=mailforge_imap_username \
  --secret mailforge-imap-password,target=mailforge_imap_password \
  --env MAILFORGE_IMAP_HOST=imap.example.com \
  --env MAILFORGE_SMTP_HOST=smtp.example.com \
  localhost/mailforge-mcp:latest
```

The `:U` option lets rootless Podman initialize volume ownership for the image's non-root user. It
changes ownership in the volume, so use a dedicated named volume. No ports are exposed. Omit
`--read-only` only when troubleshooting; the application itself needs writes solely in the cache
volume and `/tmp`. Logs are JSON on stderr, while stdout remains reserved for MCP messages.

## MCP Client Configuration

The following complete stdio registration launches Podman directly. Replace endpoint values and
the image name if needed:

```json
{
  "mcpServers": {
    "mailforge": {
      "command": "podman",
      "args": [
        "run",
        "--rm",
        "-i",
        "--read-only",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
        "--volume=mailforge-cache:/var/cache/mailforge:U",
        "--secret=mailforge-imap-user,target=mailforge_imap_username",
        "--secret=mailforge-imap-password,target=mailforge_imap_password",
        "--env=MAILFORGE_IMAP_HOST=imap.example.com",
        "--env=MAILFORGE_IMAP_PORT=993",
        "--env=MAILFORGE_SMTP_HOST=smtp.example.com",
        "--env=MAILFORGE_SMTP_PORT=587",
        "--env=MAILFORGE_SMTP_SECURITY=starttls",
        "ghcr.io/marcosousapoza/mailforge-mcp:latest"
      ]
    }
  }
}
```

Do not add `-t`: MCP stdio does not use a TTY.

## Local Development

Install exactly the locked development environment:

```sh
uv sync --frozen --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov=mailforge
```

For a local server process, point the four `*_FILE` variables at files readable only by your user,
set the endpoint variables, and run `uv run mailforge-mcp`. Tests use fakes and subprocess-local
MCP handshakes; they never require Gmail, Microsoft, or production credentials.

## Cache Behavior

SQLite runs in WAL mode with a five-second busy timeout. Parsed body and header data are cached on
demand. Flags, existence, mailbox membership metadata, synchronization timestamps, and MODSEQ are
tracked separately. Search results always come from IMAP. Reads revalidate the UID against IMAP
before a cache hit is returned. This is stricter than the configured maximum flags freshness and
keeps external deletions from appearing as live cached messages. Moves and deletions invalidate
source entries immediately.

Eviction runs after writes and removes expired entries, then least-recently-used entries until the
configured parsed-content byte limit is met. SQLite/WAL page overhead is not part of that logical
byte count. `clear_cache` deletes rows only for the configured account and has no remote side
effects. Credentials and attachment payloads are never stored.

The server records `HIGHESTMODSEQ` when CONDSTORE is available. Incremental QRESYNC synchronization
is not currently issued; synchronization is intentionally lazy and request-driven.

## Gmail Compatibility

MailForge uses Gmail-like operations, not Gmail's proprietary data model:

- An IMAP folder is not a Gmail label. Moving between folders can create a new destination UID.
- UIDs are scoped to a mailbox and UIDVALIDITY; they are not Gmail message IDs.
- `thread_id` is an RFC References-derived hint, not a provider-native thread identifier.
- Search uses portable IMAP criteria, not Gmail search syntax. Attachment search is a MIME-header
  heuristic and can have false positives or negatives.
- Archive requires a configured folder or a server-advertised `\Archive` SPECIAL-USE folder.
- Provider rules determine whether SMTP already saves Sent mail. Configure
  `MAILFORGE_SENT_MAILBOX` only when an additional IMAP APPEND is required, or duplicates may occur.
- Authentication currently supports IMAP LOGIN and SMTP AUTH through the standard library. OAuth2
  token flows are not implemented; provider app passwords can be used where supported.
- Draft replacement cannot be atomic in generic IMAP. MailForge appends the replacement before
  deleting the old draft to avoid data loss; without UIDPLUS the old draft can remain pending
  expunge.

## Security

- Use TLS endpoints and provider-specific, least-privilege app passwords where possible.
- `MAILFORGE_SMTP_SECURITY=plain` permits cleartext SMTP and should only be used through a trusted
  local tunnel or test server.
- The image runs as UID/GID 65532, exposes no ports, embeds no secrets, and supports a read-only root
  filesystem.
- Secret values are never intentionally logged. Authentication errors are converted to sanitized
  domain errors.
- Email bodies, addresses, and local SQLite data remain sensitive. Protect the named volume and
  host backups accordingly.
- Attachment downloads return untrusted base64 content. MCP clients must not execute or render it
  without their own content security controls.
- Tool annotations identify read-only and destructive operations, but the MCP client remains
  responsible for presenting confirmation and enforcing its authorization policy.

## CI and Releases

`.github/workflows/ci.yml` runs locked lint/format checks, strict type checking, unit/integration
tests, dependency auditing, a reproducible Podman build, non-root/read-only checks, and a real MCP
stdio handshake inside the image. CI uses generated dummy credentials and no external mail account.

`.github/workflows/publish.yml` publishes multi-architecture images to GHCR using only
`GITHUB_TOKEN` with `packages: write`. Tags such as `v1.2.3` produce `1.2.3`, `1.2`, `1`, and
`latest`; prereleases do not update `latest`. GitHub Actions are commit-pinned, and Dependabot
tracks uv, action, and base-image updates.

## License

MIT. See `LICENSE`.
