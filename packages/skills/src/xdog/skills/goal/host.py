"""Actual invocation authority; source labels and skill instructions cannot grant it."""
from dataclasses import dataclass


class OwnerRetired(ValueError):
    """An original invocation/owner ended; never a persistence or protocol failure."""


@dataclass(frozen=True)
class OperationContext:
    user_authorized: bool = False
    origin: str = 'service'

    def __post_init__(self) -> None:
        if type(self.user_authorized) is not bool:
            raise ValueError('User authority must come from the actual host invocation')
        if (not isinstance(self.origin, str)
                or self.origin not in {'service', 'native_command', 'model_tool', 'runtime'}):
            raise ValueError('Invalid Goal operation origin')


def require_user(context: OperationContext) -> None:
    if not isinstance(context, OperationContext) or context.user_authorized is not True:
        raise ValueError('An actual user operation is required; an automatic reminder is not authorization')
