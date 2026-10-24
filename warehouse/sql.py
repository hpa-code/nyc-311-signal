"""SQL files under snowflake/, rendered with {{placeholder}} values."""

import re
from pathlib import Path

SQL_ROOT = Path(__file__).resolve().parent.parent / "snowflake"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(sql: str, values: dict[str, str]) -> str:
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"SQL placeholder {{{{{name}}}}} has no value")
        return values[name]

    return _PLACEHOLDER.sub(substitute, sql)


def read(relative_path: str) -> str:
    return (SQL_ROOT / relative_path).read_text()
