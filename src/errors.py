class PermanentError(Exception):
    """A task failed in a way that retrying won't fix."""


class QuotaError(Exception):
    """The user's plan doesn't allow this action."""
