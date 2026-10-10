import asyncio
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from lib.tools.schema import ArgumentsSchema, to_arguments_schema


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        arguments: ArgumentsSchema | Mapping[str, Any],
        function: Callable,
    ):
        self.name = name
        self.description = description
        self.arguments = to_arguments_schema(arguments)
        self.function = self._ensure_async(function)

    @staticmethod
    def _ensure_async(function: Callable) -> Callable:
        if inspect.iscoroutinefunction(function):
            return function

        async def wrapper(**args):
            return await asyncio.to_thread(function, **args)

        wrapper.__name__ = getattr(function, "__name__", "async_wrapper")
        return wrapper

    async def call(self, **args) -> str:
        return await self.function(**args)
