"""Structured errors at definition and evidence boundaries."""


class ObserverValidationError(ValueError):
    def __init__(self, message: str, *, path: str = "$", code: str = "invalid_definition") -> None:
        self.path = path
        self.code = code
        super().__init__(f"{path}: {message}")


class SourceError(RuntimeError):
    """A failed scan is not a new snapshot and must never imply deletion."""
