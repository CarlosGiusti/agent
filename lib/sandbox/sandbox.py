import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 50_000

_SYSTEM_BINDS = ("/usr", "/bin", "/lib", "/lib64", "/etc")
_SYSTEM_PREFIXES = (
    "/usr",
    "/bin",
    "/lib",
    "/lib64",
    "/etc",
    "/dev",
    "/proc",
    "/tmp",
    "/run",
    "/opt",
    "/sys",
)

_NETWORK_MARKERS = (
    "network is unreachable",
    "could not resolve host",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname provided",
    "failed to connect",
    "no route to host",
    "network is down",
    "errno 101",
    "enetunreach",
    "eai_again",
    "eai_noname",
)

_FS_MARKERS = (
    "no such file or directory",
    "permission denied",
    "read-only file system",
    "operation not permitted",
    "not a directory",
)


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str


class CommandSandbox(Protocol):
    def run(self, command: str, *, elevated: bool) -> SandboxResult: ...


class BwrapSandbox:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def run(self, command: str, *, elevated: bool) -> SandboxResult:
        if elevated:
            return _run_bash(command, cwd=self.workspace)
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            return SandboxResult(
                exit_code=127,
                stdout="",
                stderr="Error: bubblewrap (bwrap) is not installed",
            )
        argv = [bwrap, "--die-with-parent", "--unshare-net"]
        for src in _SYSTEM_BINDS:
            path = Path(src)
            if path.exists():
                argv.extend(["--ro-bind", str(path), str(path)])
        argv.extend(
            [
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--bind",
                str(self.workspace),
                str(self.workspace),
                "--chdir",
                str(self.workspace),
                "bash",
                "-c",
                command,
            ]
        )
        return _run_argv(argv, cwd=self.workspace)


def classify_constraint(
    result: SandboxResult, command: str, workspace: Path
) -> list[str]:
    if result.exit_code == 0:
        return []

    blob = f"{result.stdout}\n{result.stderr}".lower()
    reasons: list[str] = []
    if any(marker in blob for marker in _NETWORK_MARKERS):
        reasons.append("network")

    outside = outside_paths(
        f"{command}\n{result.stdout}\n{result.stderr}",
        workspace,
    )
    if outside and any(marker in blob for marker in _FS_MARKERS):
        reasons.append("filesystem: " + ", ".join(outside))
    return reasons


def outside_paths(text: str, workspace: Path) -> list[str]:
    root = workspace.resolve()
    found: list[str] = []
    seen: set[str] = set()
    for raw in text.split():
        token = raw.strip("'\"")
        if token.startswith(("http://", "https://", "ftp://")):
            continue
        if not _looks_like_path(token):
            continue
        target = Path(token).expanduser()
        if not target.is_absolute():
            target = root / target
        try:
            resolved = target.resolve()
        except OSError:
            continue
        if resolved.is_relative_to(root) or _is_system(resolved):
            continue
        rendered = str(resolved)
        if rendered not in seen:
            seen.add(rendered)
            found.append(rendered)
    return found


def format_output(result: SandboxResult) -> str:
    parts = [f"exit_code: {result.exit_code}"]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    if not result.stdout and not result.stderr:
        parts.append("(no output)")
    text = "\n".join(parts)
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
    return text


def _looks_like_path(token: str) -> bool:
    if token.startswith("~"):
        return True
    if token.startswith("/") and not token.startswith("//"):
        return True
    return "/" in token and not token.startswith("-")


def _is_system(path: Path) -> bool:
    rendered = str(path)
    return any(
        rendered == prefix or rendered.startswith(prefix + "/")
        for prefix in _SYSTEM_PREFIXES
    )


def _run_bash(command: str, cwd: Path) -> SandboxResult:
    return _run_argv(["bash", "-c", command], cwd=cwd)


def _run_argv(argv: list[str], cwd: Path) -> SandboxResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            exit_code=124,
            stdout="",
            stderr=f"Error: command timed out after {TIMEOUT_SECONDS} seconds",
        )
    return SandboxResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
