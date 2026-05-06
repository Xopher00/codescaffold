class RopeOperationError(Exception):
    """Raised when a rope_mcp_server call returns success=False."""

    def __init__(self, op: str, message: str, args: dict):
        super().__init__(f"{op}: {message}")
        self.op = op
        self.args = args


class RopeRefactoringError(RopeOperationError):
    """Rope rejected the refactoring (e.g. ambiguous move, invalid state)."""


class RopeArgumentError(RopeOperationError):
    """Bad arguments passed to a Rope operation."""


class RopeUnexpectedError(RopeOperationError):
    """Unexpected error from the Rope layer."""
