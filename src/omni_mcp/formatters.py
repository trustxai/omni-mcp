"""Response formatting shared by every tool (markdown / JSON dual output)."""

from __future__ import annotations

import base64
import binascii
import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from io import BytesIO
from typing import Any

from omni_mcp.config import get_settings

TRUNCATION_NOTE = "[truncated: result exceeded {limit:,} bytes — narrow the request or page through it]"

#: Fallback marker for budgets too small to hold `TRUNCATION_NOTE`.
SHORT_TRUNCATION_NOTE = "[truncated]"


class ResponseFormat(StrEnum):
    """Output format selector present on read tools."""

    MARKDOWN = "markdown"
    JSON = "json"


def to_json(data: Any) -> str:
    """Serialize any payload for the LLM (stable, human-readable, strict JSON).

    Input is normalised through `_json_safe` first and `allow_nan=False` is set,
    so a `NaN`/`Infinity` can never reach the output: bare `NaN` is invalid JSON
    and breaks non-Python clients that parse tool results.
    """
    return json.dumps(_json_safe(data), indent=2, default=str, ensure_ascii=False, allow_nan=False)


def truncate_result(text: str, limit: int | None = None) -> str:
    """Cut `text` to `limit` UTF-8 bytes, appending a visible truncation marker.

    The budget is measured in bytes, not characters, because the MCP
    tool-result ceiling is a payload size — a character limit would let
    multi-byte text (accents, CJK, emoji) blow through it. The default comes
    from `OMNI_MAX_RESULT_CHARS`.

    The limit always wins: when it is too small to hold the marker as well, the
    marker degrades to `SHORT_TRUNCATION_NOTE` and then disappears, rather than
    the return value overshooting the budget it exists to enforce.
    """
    effective = limit if limit is not None else get_settings().omni_max_result_chars
    if effective <= 0:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= effective:
        return text
    for marker in ("\n\n" + TRUNCATION_NOTE.format(limit=effective), SHORT_TRUNCATION_NOTE):
        room = effective - len(marker.encode("utf-8"))
        if room >= 0:
            # `errors="ignore"` drops a code point split by the byte cut.
            return encoded[:room].decode("utf-8", errors="ignore") + marker
    return encoded[:effective].decode("utf-8", errors="ignore")


def iso_or_na(value: Any) -> str:
    """Render a timestamp as an ISO-8601 string, or `N/A` when absent."""
    if value is None:
        return "N/A"
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or "N/A"
    return str(value)


def _cell(value: Any) -> str:
    """Render one table cell: collapsed to a single line, with `|` escaped."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict | list | tuple):
        text = json.dumps(value, default=str, ensure_ascii=False)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def markdown_table(rows: Sequence[Mapping[str, Any]], max_rows: int | None = None) -> str:
    """Render a list of row dicts as a markdown table.

    Columns are the union of the rows' keys, in first-seen order. When
    `max_rows` cuts the table short, a trailing note says how many rows
    were omitted.
    """
    if not rows:
        return "_No rows._"

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    if not columns:
        return "_No columns._"

    shown = list(rows) if max_rows is None else list(rows)[:max_rows]
    lines = [
        "| " + " | ".join(_cell(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in shown:
        lines.append("| " + " | ".join(_cell(row.get(column)) for column in columns) + " |")
    omitted = len(rows) - len(shown)
    if omitted > 0:
        lines.append("")
        lines.append(f"_… {omitted:,} more row(s) not shown._")
    return "\n".join(lines)


def cursor_paginated_response(
    *,
    items: Sequence[Mapping[str, Any]],
    page_info: Mapping[str, Any] | None,
    fmt: ResponseFormat,
    item_formatter: Callable[[Mapping[str, Any]], str],
    title: str,
) -> str:
    """Uniform output for list tools using the API's `pageInfo` cursor shape.

    `page_info` is the response's `pageInfo` object (`hasNextPage`,
    `nextCursor`, `pageSize`, `totalRecords`). The next cursor is always
    printed so the caller can pass it straight back as `cursor`.
    """
    info: Mapping[str, Any] = page_info or {}
    has_next = bool(info.get("hasNextPage"))
    next_cursor = info.get("nextCursor")
    total = info.get("totalRecords")
    page_size = info.get("pageSize")
    count = len(items)

    if fmt is ResponseFormat.JSON:
        return truncate_result(
            to_json(
                {
                    "title": title,
                    "count": count,
                    "totalRecords": total,
                    "pageSize": page_size,
                    "hasNextPage": has_next,
                    "nextCursor": next_cursor,
                    "items": list(items),
                }
            )
        )

    lines = [f"# {title}", ""]
    if isinstance(total, int):
        lines.append(f"Showing **{count:,}** of **{total:,}** record(s).")
    else:
        lines.append(f"Showing **{count:,}** record(s).")
    if has_next and next_cursor:
        lines.append(f'More available — pass `cursor="{next_cursor}"` to fetch the next page.')
    elif has_next:
        lines.append("More available — the API reported another page but returned no cursor.")
    else:
        lines.append("End of results.")
    lines.append("")
    if items:
        lines.extend(item_formatter(item) for item in items)
    else:
        lines.append("_No records._")
    return truncate_result("\n".join(lines))


def _json_safe(value: Any) -> Any:
    """Convert a Python value into something strict JSON can represent.

    Non-finite floats (`nan`, `inf`, `-inf`) become `None`: JSON has no literal
    for them, and emitting bare `NaN` produces output that most clients refuse
    to parse.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return str(value)
        return number if math.isfinite(number) else None
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(item) for item in value]
    return str(value)


def decode_arrow_base64(b64: str) -> list[dict[str, Any]]:
    """Decode a base64 Apache Arrow payload into JSON-safe row dicts.

    Query results come back as a base64-encoded Arrow table. Both IPC
    encapsulations are accepted: the stream format is tried first and the
    random-access *file* format is the fallback. Values are converted to
    JSON-safe Python (timestamps → ISO strings, decimals → floats,
    binary → base64).
    """
    import pyarrow as pa  # imported lazily: keeps `import omni_mcp` cheap

    payload = (b64 or "").strip()
    if not payload:
        return []
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Query result is not valid base64: {exc}") from exc
    if not raw:
        return []

    try:
        with pa.ipc.open_stream(BytesIO(raw)) as reader:
            table = reader.read_all()
    except pa.ArrowInvalid:
        try:
            with pa.ipc.open_file(BytesIO(raw)) as reader:
                table = reader.read_all()
        except pa.ArrowInvalid as exc:
            raise ValueError(f"Query result is not a readable Arrow IPC stream or file: {exc}") from exc

    rows: list[dict[str, Any]] = table.to_pylist()
    return [{str(key): _json_safe(value) for key, value in row.items()} for row in rows]


def format_arrow_result(b64: str, fmt: ResponseFormat, max_rows: int | None = None) -> str:
    """Render a base64 Arrow query result as a markdown table or JSON rows."""
    rows = decode_arrow_base64(b64)
    if fmt is ResponseFormat.JSON:
        shown = rows if max_rows is None else rows[:max_rows]
        return truncate_result(
            to_json(
                {"rowCount": len(rows), "returnedRows": len(shown), "truncated": len(shown) < len(rows), "rows": shown}
            )
        )
    if not rows:
        return "_Query returned no rows._"
    header = f"**{len(rows):,}** row(s) returned."
    return truncate_result(f"{header}\n\n{markdown_table(rows, max_rows=max_rows)}")
