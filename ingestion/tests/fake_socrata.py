"""In-memory stand-in for the Socrata SODA HTTP API.

Speaks the HTTP-level transport interface the source client uses and
understands the small SoQL subset it emits: `$where` built from AND/OR,
parentheses and comparisons against quoted literals, `$order` on plain
fields, `$limit`, and `$select=count(*)`.
"""

import json
import re
from dataclasses import dataclass, field

from ingestion.api_client import Response

DATASET_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"


class SimulatedCrash(Exception):
    pass


@dataclass
class FakeSocrata:
    rows: list[dict]
    requests: list[dict] = field(default_factory=list)
    _failures: list[Response] = field(default_factory=list)
    _crash_on_request: int | None = None

    def fail_next(self, status: int, times: int = 1, body: str = "", headers: dict | None = None) -> None:
        for _ in range(times):
            self._failures.append(Response(status, body.encode(), headers or {}))

    def crash_on_request(self, n: int) -> None:
        """Raise SimulatedCrash on the n-th request (1-based) from now."""
        self._crash_on_request = len(self.requests) + n

    def page_requests(self) -> list[dict]:
        return [r for r in self.requests if r.get("$select") != "count(*)"]

    def __call__(self, url: str, params: dict, headers: dict) -> Response:
        self.requests.append(dict(params))
        if self._crash_on_request is not None and len(self.requests) >= self._crash_on_request:
            self._crash_on_request = None
            raise SimulatedCrash()
        if self._failures:
            return self._failures.pop(0)
        if url != DATASET_URL:
            return Response(404, b'{"message":"not found"}', {})

        matching = [r for r in self.rows if _matches(r, params.get("$where"))]
        if params.get("$select") == "count(*)":
            return _json([{"count": str(len(matching))}])

        for f in reversed([f.strip() for f in params.get("$order", "").split(",") if f.strip()]):
            matching.sort(key=lambda r: r.get(f, ""))
        limit = int(params.get("$limit", 1000))
        return _json(matching[:limit])


def _json(value) -> Response:
    return Response(200, json.dumps(value).encode(), {"Content-Type": "application/json"})


_TOKEN = re.compile(r"\s*(\(|\)|AND\b|OR\b|>=|<=|<>|=|>|<|'(?:[^']|'')*'|[:\w]+)")


def _tokenize(text: str) -> list[str]:
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"fake SoQL cannot parse: {text[pos:]!r}")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


def _matches(row: dict, where: str | None) -> bool:
    if not where:
        return True
    tokens = _tokenize(where)
    pos = 0

    def expr() -> bool:
        nonlocal pos
        value = term()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            rhs = term()
            value = value or rhs
        return value

    def term() -> bool:
        nonlocal pos
        value = factor()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            rhs = factor()
            value = value and rhs
        return value

    def factor() -> bool:
        nonlocal pos
        if tokens[pos] == "(":
            pos += 1
            value = expr()
            assert tokens[pos] == ")"
            pos += 1
            return value
        name, op, literal = tokens[pos], tokens[pos + 1], tokens[pos + 2]
        pos += 3
        assert literal.startswith("'"), f"fake SoQL expects quoted literals, got {literal}"
        rhs = literal[1:-1].replace("''", "'")
        lhs = row.get(name)
        if lhs is None:
            return False
        return {
            "=": lhs == rhs,
            "<>": lhs != rhs,
            ">": lhs > rhs,
            ">=": lhs >= rhs,
            "<": lhs < rhs,
            "<=": lhs <= rhs,
        }[op]

    result = expr()
    assert pos == len(tokens), f"fake SoQL left tokens: {tokens[pos:]}"
    return result
