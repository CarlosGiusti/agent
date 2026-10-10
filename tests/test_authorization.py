from lib.authorization.authorization import Authorization
from lib.authorization.protocol import AuthorizationProtocol, DecisionType


class ScriptedProtocol(AuthorizationProtocol):
    def __init__(self, decisions: list[DecisionType]) -> None:
        self._decisions = list(decisions)
        self.calls: list[tuple[str, dict]] = []

    async def check(self, action: str, args: dict) -> DecisionType:
        self.calls.append((action, args))
        if not self._decisions:
            raise AssertionError(f"unexpected prompt for {action}")
        return self._decisions.pop(0)


class FailProtocol(AuthorizationProtocol):
    async def check(self, action: str, args: dict) -> DecisionType:
        raise AssertionError(f"should not prompt for {action}")


async def test_check_allow_once_returns_true_and_does_not_skip_later_prompts():
    protocol = ScriptedProtocol(
        [DecisionType.ALLOW_ONCE, DecisionType.ALLOW_ONCE]
    )
    auth = Authorization(protocol)

    assert await auth.check("npm install", ["network"]) is True
    assert await auth.check("npm install", ["network"]) is True
    assert protocol.calls == [
        ("elevate", {"command": "npm install", "reasons": ["network"]}),
        ("elevate", {"command": "npm install", "reasons": ["network"]}),
    ]


async def test_check_deny_returns_false():
    protocol = ScriptedProtocol([DecisionType.DENY])
    auth = Authorization(protocol)

    assert await auth.check("curl https://example.com", ["network"]) is False
    assert protocol.calls == [
        (
            "elevate",
            {"command": "curl https://example.com", "reasons": ["network"]},
        ),
    ]
