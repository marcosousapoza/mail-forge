"""Domain exceptions exposed as safe MCP errors."""


class MailForgeError(Exception):
    """Base class for expected operational failures."""


class ConfigurationError(MailForgeError):
    """Configuration is absent or invalid."""


class AuthenticationError(MailForgeError):
    """A mail server rejected authentication."""


class ProtocolError(MailForgeError):
    """A mail server returned an invalid or unsuccessful response."""


class MessageNotFoundError(MailForgeError):
    """The requested UID does not exist in the selected mailbox."""


class UnsafeOperationError(MailForgeError):
    """An operation could have effects outside the requested messages."""
