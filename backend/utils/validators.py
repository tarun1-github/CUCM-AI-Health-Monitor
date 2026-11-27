"""Read-only command allowlist and input validation helpers."""

import re
from typing import Iterable, Tuple

from backend.utils.config_loader import load_commands_yaml

_FORBIDDEN_PATTERNS = (
    r"\bset\b",
    r"\brestart\b",
    r"\breboot\b",
    r"\bshutdown\b",
    r"\bdelete\b",
    r"\bunset\b",
    r"\bstop\b",
    r"\bstart\b",
    r"\bupdate\b",
    r"\binsert\b",
    r"\bdrop\b",
    r"\balter\b",
    r"\bcreate\b",
    r"\bfile\s+delete\b",
    r"\butils\s+system\s+restart\b",
    r"\butils\s+system\s+shutdown\b",
    r"\butils\s+service\s+(?:start|stop|restart)\b",
)


def get_allowed_commands() -> list[str]:
    """Return enabled read-only commands from commands.yaml."""
    commands = load_commands_yaml()
    return [c["command"].strip() for c in commands if c.get("enabled", True) and c.get("command")]


def validate_readonly_command(command: str, allowlist: Iterable[str] | None = None) -> Tuple[bool, str]:
    """Validate that a CLI command is strictly read-only and on the allowlist."""
    if not command or not command.strip():
        return False, "Empty command is not allowed."

    normalized = " ".join(command.strip().split())

    for pattern in _FORBIDDEN_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return False, f"Command '{normalized}' rejected by read-only safety policy."

    allowed = list(allowlist) if allowlist is not None else get_allowed_commands()
    allowed_normalized = {" ".join(c.strip().split()).lower() for c in allowed}

    if normalized.lower() not in allowed_normalized:
        return False, f"Command '{normalized}' is not in the approved read-only allowlist."

    return True, "OK"


def validate_readonly_sql(sql: str) -> Tuple[bool, str]:
    """Ensure AXL SQL queries are strictly SELECT statements."""
    if not sql or not sql.strip():
        return False, "Empty SQL query."
    stripped = sql.strip()
    if not re.match(r"^\s*select\b", stripped, re.IGNORECASE):
        return False, "Only SELECT queries are permitted."
    if re.search(r"\b(insert|update|delete|drop|alter|create|truncate|exec|execute)\b", stripped, re.IGNORECASE):
        return False, "Mutating SQL keywords are forbidden."
    return True, "OK"
