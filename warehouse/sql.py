"""SQL files under snowflake/, rendered with {{placeholder}} values.

`{{include:relative/path.sql}}` inlines another SQL file (rendered with the
same values), so a procedure can embed the same steps the pipeline runs.
"""

import re
from pathlib import Path

SQL_ROOT = Path(__file__).resolve().parent.parent / "snowflake"
_PLACEHOLDER = re.compile(r"\{\{(include:)?([\w/.]+)\}\}")


def render(sql: str, values: dict[str, str]) -> str:
    def substitute(match: re.Match) -> str:
        include, name = match.groups()
        if include:
            return render(read(name), values)
        if name not in values:
            raise KeyError(f"SQL placeholder {{{{{name}}}}} has no value")
        return values[name]

    return _PLACEHOLDER.sub(substitute, sql)


def read(relative_path: str) -> str:
    return (SQL_ROOT / relative_path).read_text()
