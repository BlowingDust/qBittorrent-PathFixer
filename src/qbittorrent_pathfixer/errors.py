class BtFileRenameError(Exception):
    """Base class for expected user-facing errors."""


class ConfigError(BtFileRenameError):
    pass


class ConnectionError(BtFileRenameError):
    pass


class AuthenticationError(ConnectionError):
    pass


class ApiCompatibilityError(BtFileRenameError):
    pass


class TorrentNotFoundError(BtFileRenameError):
    pass


class ScanDataError(BtFileRenameError):
    pass


class PlanFormatError(BtFileRenameError):
    pass


class ValidationFailedError(BtFileRenameError):
    pass


class ExecutionFailedError(BtFileRenameError):
    pass


class ExecutionUnknownError(BtFileRenameError):
    pass
