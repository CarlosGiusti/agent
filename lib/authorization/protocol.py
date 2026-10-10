import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum


class DecisionType(StrEnum):
    ALLOW_ONCE = "allow_once"
    DENY = "deny"


class AuthorizationProtocol(ABC):
    @abstractmethod
    async def check(self, action: str, args: dict) -> DecisionType:
        raise NotImplementedError


class PromptAuthorizationProtocol(AuthorizationProtocol):
    def __init__(self, ask: Callable[[str, dict], DecisionType]) -> None:
        self._ask = ask

    async def check(self, action: str, args: dict) -> DecisionType:
        return await asyncio.to_thread(self._ask, action, args)
