"""Unit tests for the shared response formatters, including Arrow decoding."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

import pyarrow as pa
import pytest

from omni_mcp.formatters import (
    ResponseFormat,
    cursor_paginated_response,
    decode_arrow_base64,
    format_arrow_result,
    iso_or_na,
    markdown_table,
    to_json,
    truncate_result,
)

# --- basics -----------------------------------------------------------


def test_to_json_is_indented_and_handles_unknown_types() -> None:
    text = to_json({"when": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "n": 1})

    assert '"n": 1' in text
    assert "2026-01-02" in text
    assert json.loads(text)["n"] == 1


def test_truncate_result_leaves_short_text_alone() -> None:
    assert truncate_result("short", limit=100) == "short"


def test_truncate_result_marks_truncation_and_respects_limit() -> None:
    result = truncate_result("x" * 500, limit=200)

    assert len(result) <= 200
    assert "[truncated" in result


def test_truncate_result_uses_settings_default() -> None:
    # Default OMNI_MAX_RESULT_CHARS is 900_000, so this passes through.
    assert truncate_result("y" * 1000) == "y" * 1000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "N/A"),
        ("", "N/A"),
        ("   ", "N/A"),
        ("2026-01-15T10:00:00.000Z", "2026-01-15T10:00:00.000Z"),
        (datetime(2026, 1, 15, 10, 0, tzinfo=UTC), "2026-01-15T10:00:00+00:00"),
        (42, "42"),
    ],
)
def test_iso_or_na(value: Any, expected: str) -> None:
    assert iso_or_na(value) == expected


# --- markdown table ---------------------------------------------------


def test_markdown_table_renders_union_of_keys() -> None:
    table = markdown_table([{"a": 1, "b": 2}, {"b": 3, "c": 4}])
    lines = table.splitlines()

    assert lines[0] == "| a | b | c |"
    assert lines[1] == "| --- | --- | --- |"
    assert lines[2] == "| 1 | 2 |  |"
    assert lines[3] == "|  | 3 | 4 |"


def test_markdown_table_escapes_pipes_and_newlines() -> None:
    table = markdown_table([{"text": "a|b\nc"}])

    assert "a\\|b c" in table


def test_markdown_table_serialises_nested_values() -> None:
    table = markdown_table([{"tags": ["x", "y"]}])

    assert '["x", "y"]' in table


def test_markdown_table_max_rows_notes_the_remainder() -> None:
    table = markdown_table([{"i": i} for i in range(10)], max_rows=3)

    assert "| 2 |" in table
    assert "| 3 |" not in table
    assert "7 more row(s) not shown" in table


def test_markdown_table_empty() -> None:
    assert markdown_table([]) == "_No rows._"


# --- cursor pagination ------------------------------------------------


def _item_formatter(item: Mapping[str, Any]) -> str:
    return f"- {item['name']}"


def test_cursor_paginated_markdown_prints_next_cursor() -> None:
    result = cursor_paginated_response(
        items=[{"name": "one"}, {"name": "two"}],
        page_info={"hasNextPage": True, "nextCursor": "cur-123", "pageSize": 2, "totalRecords": 7},
        fmt=ResponseFormat.MARKDOWN,
        item_formatter=_item_formatter,
        title="Users",
    )

    assert "# Users" in result
    assert "Showing **2** of **7** record(s)." in result
    assert 'cursor="cur-123"' in result
    assert "- one" in result and "- two" in result


def test_cursor_paginated_markdown_end_of_results() -> None:
    result = cursor_paginated_response(
        items=[{"name": "only"}],
        page_info={"hasNextPage": False, "nextCursor": None, "pageSize": 20, "totalRecords": 1},
        fmt=ResponseFormat.MARKDOWN,
        item_formatter=_item_formatter,
        title="Users",
    )

    assert "End of results." in result


def test_cursor_paginated_markdown_empty_and_missing_page_info() -> None:
    result = cursor_paginated_response(
        items=[],
        page_info=None,
        fmt=ResponseFormat.MARKDOWN,
        item_formatter=_item_formatter,
        title="Users",
    )

    assert "_No records._" in result
    assert "Showing **0** record(s)." in result


def test_cursor_paginated_json_shape() -> None:
    result = cursor_paginated_response(
        items=[{"name": "one"}],
        page_info={"hasNextPage": True, "nextCursor": "cur-123", "pageSize": 1, "totalRecords": 5},
        fmt=ResponseFormat.JSON,
        item_formatter=_item_formatter,
        title="Users",
    )
    payload = json.loads(result)

    assert payload["count"] == 1
    assert payload["totalRecords"] == 5
    assert payload["nextCursor"] == "cur-123"
    assert payload["items"] == [{"name": "one"}]


# --- Arrow ------------------------------------------------------------


def _sample_table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array(["alpha", "beta"], type=pa.string()),
            "amount": pa.array([Decimal("10.50"), Decimal("2.25")], type=pa.decimal128(10, 2)),
            "created_at": pa.array(
                [datetime(2026, 1, 15, 10, 0, tzinfo=UTC), datetime(2026, 2, 15, 11, 30, tzinfo=UTC)],
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "blob": pa.array([b"\x00\x01", b"\x02"], type=pa.binary()),
            "note": pa.array(["ok", None], type=pa.string()),
        }
    )


def _stream_b64(table: pa.Table) -> str:
    sink = BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return base64.b64encode(sink.getvalue()).decode("ascii")


def _file_b64(table: pa.Table) -> str:
    sink = BytesIO()
    with pa.ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)
    return base64.b64encode(sink.getvalue()).decode("ascii")


@pytest.mark.parametrize("encoder", [_stream_b64, _file_b64], ids=["ipc-stream", "ipc-file"])
def test_decode_arrow_base64_both_ipc_formats(encoder: Any) -> None:
    rows = decode_arrow_base64(encoder(_sample_table()))

    assert len(rows) == 2
    assert rows[0]["id"] == 1
    assert rows[0]["name"] == "alpha"
    assert rows[0]["amount"] == 10.5
    assert rows[0]["created_at"] == "2026-01-15T10:00:00+00:00"
    assert rows[0]["blob"] == base64.b64encode(b"\x00\x01").decode("ascii")
    assert rows[1]["note"] is None


def test_decode_arrow_base64_json_serialisable() -> None:
    rows = decode_arrow_base64(_stream_b64(_sample_table()))

    assert json.loads(json.dumps(rows)) == rows


def test_decode_arrow_base64_empty_input() -> None:
    assert decode_arrow_base64("") == []
    assert decode_arrow_base64("   ") == []


def test_decode_arrow_base64_rejects_non_arrow_payload() -> None:
    with pytest.raises(ValueError, match="Arrow IPC"):
        decode_arrow_base64(base64.b64encode(b"not arrow at all").decode("ascii"))


def test_format_arrow_result_markdown() -> None:
    result = format_arrow_result(_stream_b64(_sample_table()), ResponseFormat.MARKDOWN)

    assert "**2** row(s) returned." in result
    assert "| id | name | amount | created_at | blob | note |" in result
    assert "alpha" in result


def test_format_arrow_result_markdown_max_rows() -> None:
    result = format_arrow_result(_stream_b64(_sample_table()), ResponseFormat.MARKDOWN, max_rows=1)

    assert "alpha" in result
    assert "beta" not in result
    assert "1 more row(s) not shown" in result


def test_format_arrow_result_json() -> None:
    payload = json.loads(format_arrow_result(_stream_b64(_sample_table()), ResponseFormat.JSON, max_rows=1))

    assert payload["rowCount"] == 2
    assert payload["returnedRows"] == 1
    assert payload["truncated"] is True
    assert payload["rows"][0]["name"] == "alpha"


def test_format_arrow_result_empty_table() -> None:
    empty = pa.table({"id": pa.array([], type=pa.int64())})

    assert format_arrow_result(_stream_b64(empty), ResponseFormat.MARKDOWN) == "_Query returned no rows._"


# --- strict JSON / non-finite numbers ---------------------------------


def test_to_json_maps_non_finite_floats_to_null() -> None:
    text = to_json({"a": float("nan"), "b": float("inf"), "c": float("-inf"), "d": 1.5})
    payload = json.loads(text)

    assert "NaN" not in text
    assert "Infinity" not in text
    assert payload == {"a": None, "b": None, "c": None, "d": 1.5}


def test_to_json_handles_non_finite_inside_nested_structures() -> None:
    payload = json.loads(to_json({"rows": [{"v": float("nan")}], "t": (float("inf"), 2)}))

    assert payload == {"rows": [{"v": None}], "t": [None, 2]}


def test_decode_arrow_base64_maps_non_finite_floats_to_null() -> None:
    table = pa.table({"value": pa.array([1.5, float("nan"), float("inf"), float("-inf")], type=pa.float64())})

    rows = decode_arrow_base64(_stream_b64(table))

    assert [row["value"] for row in rows] == [1.5, None, None, None]
    assert json.loads(json.dumps(rows)) == rows


def test_format_arrow_result_json_is_strict_json_for_non_finite() -> None:
    table = pa.table({"value": pa.array([float("nan")], type=pa.float64())})

    text = format_arrow_result(_stream_b64(table), ResponseFormat.JSON)

    assert "NaN" not in text
    assert json.loads(text)["rows"] == [{"value": None}]


# --- byte-based truncation --------------------------------------------


def test_truncate_result_counts_utf8_bytes_not_characters() -> None:
    # 100 three-byte characters = 300 bytes, well over a 120-byte budget.
    text = "\u3042" * 100

    result = truncate_result(text, limit=120)

    assert len(result.encode("utf-8")) <= 120
    assert "[truncated" in result
    assert "bytes" in result


def test_truncate_result_keeps_multibyte_text_that_fits() -> None:
    text = "\u00e9\u00e9\u00e9"

    assert truncate_result(text, limit=10) == text


def test_truncate_result_never_splits_a_code_point() -> None:
    result = truncate_result("\U0001f600" * 50, limit=100)

    # Decodes cleanly: the byte cut dropped any partial code point.
    assert result.encode("utf-8").decode("utf-8") == result
