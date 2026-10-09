"""Small public operation contracts without installation or task-runner dependencies."""
from dataclasses import dataclass

class OperationError(ValueError):
    def __init__(self, code, message, *, retryable=False, details=None):
        super().__init__(message)
        self.code, self.message, self.retryable, self.details = code, message, retryable, details

@dataclass(frozen=True)
class OperationSpec:
    operation_id: str
    owner: str
    request_schema: str
    response_schema: str
    execution_mode: str
    risk: str
    lock_scope: str | None
    confirmation_required: bool
    confirmation_note_required: bool
    legacy_aliases: tuple
    package_group: str
