from collections.abc import Sequence

from lib.authorization.protocol import AuthorizationProtocol, DecisionType


class Authorization:
    def __init__(self, protocol: AuthorizationProtocol):
        self.protocol = protocol

    async def check(self, command: str, reasons: Sequence[str]) -> bool:
        decision = await self.protocol.check(
            "elevate",
            {"command": command, "reasons": list(reasons)},
        )
        return decision is DecisionType.ALLOW_ONCE
