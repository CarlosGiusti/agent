import os
import select
import sys
import termios
import tty
from typing import Protocol, Self


class KeyReader(Protocol):
    def read(self) -> str: ...


class TerminalKeys:
    def __enter__(self) -> Self:
        self._fd = sys.stdin.fileno()
        self._previous = termios.tcgetattr(self._fd)
        attrs = termios.tcgetattr(self._fd)
        attrs[tty.LFLAG] &= ~(termios.ECHO | termios.ICANON)
        attrs[tty.CC][termios.VMIN] = 1
        attrs[tty.CC][termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        return self

    def __exit__(self, *exc: object) -> None:
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._previous)

    def read(self) -> str:
        char = _read_byte(self._fd)
        if char == b"\x03":
            raise KeyboardInterrupt
        if char in (b"\r", b"\n"):
            return "enter"
        if char != b"\x1b":
            return "ignore"
        return _arrow(self._fd)


def _read_byte(fd: int) -> bytes:
    data = os.read(fd, 1)
    if data == b"":
        raise EOFError
    return data


def _arrow(fd: int) -> str:
    if not _pending(fd):
        return "ignore"
    second = _read_byte(fd)
    if second not in (b"[", b"O") or not _pending(fd):
        return "ignore"
    third = _read_byte(fd)
    if third == b"A":
        return "up"
    if third == b"B":
        return "down"
    return "ignore"


def _pending(fd: int) -> bool:
    return bool(select.select([fd], [], [], 0.05)[0])
