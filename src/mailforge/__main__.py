"""MailForge stdio entrypoint."""

from __future__ import annotations

import logging

from mailforge.config import Settings
from mailforge.errors import ConfigurationError
from mailforge.logging import configure_logging
from mailforge.server import mcp


def main() -> None:
    try:
        settings = Settings.from_env()
        configure_logging(settings.log_level)
    except ConfigurationError:
        configure_logging("INFO")
        logging.getLogger(__name__).exception("Invalid MailForge configuration")
        raise SystemExit(2) from None
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
