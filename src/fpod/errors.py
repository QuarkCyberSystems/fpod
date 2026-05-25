"""Custom exception types for fpod."""


class FpodError(Exception):
    """Base error for fpod CLI failures."""


class ConfigError(FpodError):
    """Config file load/save problems."""


class BenchExistsError(FpodError):
    """Raised when creating a bench whose name is already taken."""


class BenchNotFoundError(FpodError):
    """Raised when an operation references a bench that doesn't exist."""


class PodmanError(FpodError):
    """Subprocess error from a podman invocation."""


class ValidationError(FpodError):
    """User-supplied input failed validation."""
