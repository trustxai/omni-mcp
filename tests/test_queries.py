"""Unit tests for the query / job tools against a fake client."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any

import httpx
import pyarrow as pa
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.queries import (
    GetJobStatusInput,
    RunQueryInput,
    WaitForQueryResultsInput,
    omni_get_job_status,
    omni_run_query,
    omni_wait_for_query_results,
)

JOB_A = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
JOB_B = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
WORKBOOK_URL = "https://myorg.omniapp.co/e/1:abc123DEF456/1"


class _Clock:
    """A fake monotonic clock the tests advance by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeClient:
    """Records `(method, path, kwargs)` calls; returns canned payload(s).

    `payloads` feeds one payload per call and repeats the last one, which is
    what the polling tool needs. With a `clock`, each request advances it by
    `seconds_per_call` capped at the timeout the caller asked for — the way a
    server-side long poll behaves.
    """

    def __init__(
        self,
        payload: Any = None,
        exc: Exception | None = None,
        payloads: list[Any] | None = None,
        headers: dict[str, str] | None = None,
        clock: _Clock | None = None,
        seconds_per_call: float = 0.0,
    ) -> None:
        self._payload = payload if payload is not None else {}
        self._payloads = list(payloads) if payloads is not None else None
        self._exc = exc
        self._headers = headers or {}
        self._clock = clock
        self._seconds_per_call = seconds_per_call
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, method: str, path: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, path, kwargs))
        if self._clock is not None and self._seconds_per_call:
            timeout = kwargs.get("timeout")
            spent = min(self._seconds_per_call, timeout) if isinstance(timeout, int | float) else self._seconds_per_call
            self._clock.advance(spent)
        if self._exc is not None:
            raise self._exc
        if self._payloads is not None:
            return self._payloads[min(len(self.calls) - 1, len(self._payloads) - 1)]
        return self._payload

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._record(method, path, kwargs)

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        payload = self._record(method, path, kwargs)
        request = httpx.Request(method, f"https://acme.omniapp.co/api{path.lstrip('/')}")
        if isinstance(payload, str):
            return httpx.Response(200, text=payload, headers=self._headers, request=request)
        return httpx.Response(200, json=payload, headers=self._headers, request=request)


def _patch_polling(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> list[float]:
    """Install the fake clock and a sleep that records and advances it."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr("omni_mcp.tools.queries._monotonic", clock)
    monkeypatch.setattr("omni_mcp.tools.queries._sleep", _record)
    return slept


def _sample_table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array(["alpha", "beta"], type=pa.string()),
        }
    )


def _stream_b64(table: pa.Table) -> str:
    """Arrow table -> IPC stream bytes -> base64, exactly as the API sends it."""
    sink = BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return base64.b64encode(sink.getvalue()).decode("ascii")


ARROW_B64 = _stream_b64(_sample_table())


def _result_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jobs_submitted": {"job-1": "client-1"},
        "job_id": "job-1",
        "status": "COMPLETE",
        "client_result_id": "client-1",
        "summary": {
            "sql": "SELECT * FROM order_items LIMIT 10",
            "total_rows": 2,
            "execution_time_ms": 245,
            "fields": [{"field_name": "id"}, {"field_name": "name"}],
        },
        "cache_metadata": {"cache_hit": True, "ttl": 3600},
        "stream_stats": {"server_stream": 12},
        "result": ARROW_B64,
        "timed_out": False,
    }
    payload.update(overrides)
    return payload


def _status_error(status: int, body: dict[str, Any], path: str = "/api/v1/query/run") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", f"https://acme.omniapp.co{path}")
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def _fields_input(**overrides: Any) -> RunQueryInput:
    base: dict[str, Any] = {
        "model_id": "bcf0cffd-ec1b-44d5-945a-a261ebe407fc",
        "table": "order_items",
        "fields": ["inventory_items.product_department", "inventory_items.count"],
    }
    base.update(overrides)
    return RunQueryInput(**base)


# --- omni_run_query ---------------------------------------------------


async def test_run_query_sends_body_and_renders_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    result = await omni_run_query(
        _fields_input(
            limit=10,
            sorts=[{"column_name": "inventory_items.product_department", "sort_descending": False}],
            join_paths_from_topic_name="order_items",
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/query/run",
            {
                "params": None,
                "json_body": {
                    "query": {
                        "modelId": "bcf0cffd-ec1b-44d5-945a-a261ebe407fc",
                        "table": "order_items",
                        "fields": ["inventory_items.product_department", "inventory_items.count"],
                        "limit": 10,
                        "sorts": [{"column_name": "inventory_items.product_department", "sort_descending": False}],
                        "join_paths_from_topic_name": "order_items",
                    }
                },
                "timeout": 120.0,
            },
        )
    ]
    assert "# Query results" in result
    assert "- Job ID: `job-1`" in result
    assert "- Status: `COMPLETE`" in result
    assert "- Rows reported by the API: 2" in result
    assert "- Execution time: 245 ms" in result
    assert "- Result streaming: 12 ms" in result
    assert "cache_hit=True" in result
    assert "```sql" in result
    assert "SELECT * FROM order_items LIMIT 10" in result
    assert "**2** row(s) returned." in result
    assert "| id | name |" in result
    assert "| 1 | alpha |" in result
    assert "| 2 | beta |" in result


async def test_run_query_formats_large_numbers_without_exponents(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _result_payload(
        summary={"total_rows": 1_234_567, "execution_time_ms": 1_234_567},
        stream_stats={"server_stream": 9_876_543},
    )
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_run_query(_fields_input())

    assert "- Rows reported by the API: 1,234,567" in result
    assert "- Execution time: 1,234,567 ms" in result
    assert "- Result streaming: 9,876,543 ms" in result
    assert "e+" not in result


async def test_run_query_raw_query_wins_over_first_class_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)
    raw = {
        "modelId": "7155f419-a071-405c-8426-b4b5d3939049",
        "table": "order_items",
        "fields": ["order_items.created_at[month]", "order_items.sale_price_sum"],
        "filters": {},
        "limit": 1000,
    }

    await omni_run_query(_fields_input(limit=10, filters={"ignored": True}, query=raw))

    body = fake.calls[0][2]["json_body"]
    assert body == {"query": raw}


async def test_run_query_passes_user_id_and_body_options(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    await omni_run_query(
        _fields_input(
            user_id="9e8719d9-276a-4964-9395-a493189a247c",
            branch_id="4e6953a9-a71b-4c0b-8b63-a9ea308f6aaf",
            connection_environment_id="c0ffee00-0000-4000-8000-000000000000",
            cache="SkipCache",
            timeout_seconds=45,
        )
    )

    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("POST", "/v1/query/run")
    assert kwargs["params"] == {"userId": "9e8719d9-276a-4964-9395-a493189a247c"}
    assert kwargs["timeout"] == 45.0
    assert kwargs["json_body"]["branchId"] == "4e6953a9-a71b-4c0b-8b63-a9ea308f6aaf"
    assert kwargs["json_body"]["connectionEnvironmentId"] == "c0ffee00-0000-4000-8000-000000000000"
    assert kwargs["json_body"]["cache"] == "SkipCache"
    # `userId` goes in the query string only: sending it in both places is a 400.
    assert "userId" not in kwargs["json_body"]


async def test_run_query_maps_advanced_query_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    await omni_run_query(
        _fields_input(
            pivots=[{"field": "a"}],
            calculations=[{"formula": "1+1"}],
            fill_fields=["order_items.created_at[month]"],
            column_totals={"enabled": True},
            row_totals={"enabled": False},
            column_limit=50,
            default_group_by=True,
            manual_sort=True,
            dbt_mode=False,
            rewrite_sql=True,
            dimension_index=1,
            user_edited_sql="SELECT 1",
            custom_summary_types={"a": "sum"},
            join_via_map={"a": "b"},
            controls=[{"id": "c1"}],
            version=3,
        )
    )

    query = fake.calls[0][2]["json_body"]["query"]
    assert query["pivots"] == [{"field": "a"}]
    assert query["calculations"] == [{"formula": "1+1"}]
    assert query["fill_fields"] == ["order_items.created_at[month]"]
    assert query["column_totals"] == {"enabled": True}
    assert query["row_totals"] == {"enabled": False}
    assert query["column_limit"] == 50
    assert query["default_group_by"] is True
    assert query["manualSort"] is True
    assert query["dbtMode"] is False
    assert query["rewriteSql"] is True
    assert query["dimensionIndex"] == 1
    assert query["userEditedSQL"] == "SELECT 1"
    assert query["custom_summary_types"] == {"a": "sum"}
    assert query["join_via_map"] == {"a": "b"}
    assert query["controls"] == [{"id": "c1"}]
    assert query["version"] == 3


async def test_run_query_json_format_carries_metadata_and_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    payload = json.loads(await omni_run_query(_fields_input(response_format=ResponseFormat.JSON)))

    assert payload["jobId"] == "job-1"
    assert payload["status"] == "COMPLETE"
    assert payload["summary"]["sql"] == "SELECT * FROM order_items LIMIT 10"
    assert payload["rowCount"] == 2
    assert payload["returnedRows"] == 2
    assert payload["truncated"] is False
    assert payload["rows"] == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]


async def test_run_query_max_rows_caps_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    result = await omni_run_query(_fields_input(max_rows=1))

    assert "alpha" in result
    assert "beta" not in result
    assert "1 more row(s) not shown" in result


async def test_run_query_max_rows_caps_json_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    payload = json.loads(
        await omni_run_query(_fields_input(response_format=ResponseFormat.JSON, max_rows=1)),
    )

    assert payload["rowCount"] == 2
    assert payload["returnedRows"] == 1
    assert payload["truncated"] is True
    assert payload["rows"] == [{"id": 1, "name": "alpha"}]


async def test_run_query_requires_a_query(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload())
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    result = await omni_run_query(RunQueryInput(table="order_items"))

    assert result.startswith("Error: a query needs")
    assert fake.calls == []


async def test_run_query_soft_timeout_returns_remaining_job_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"timed_out": True, "remaining_job_ids": [JOB_A, JOB_B]}
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_run_query(_fields_input())

    assert "# Query still running" in result
    assert JOB_A in result
    assert JOB_B in result
    assert "omni_wait_for_query_results" in result


async def test_run_query_reports_jobs_still_running_beside_the_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run can return one job's rows while other jobs are still going."""
    payload = _result_payload(remaining_job_ids=[JOB_B])
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_run_query(_fields_input())
    markdown_line = f"- Still running: `{JOB_B}` — poll them with `omni_wait_for_query_results`."

    assert markdown_line in result
    assert "| 1 | alpha |" in result


async def test_run_query_json_reports_jobs_still_running(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _result_payload(remaining_job_ids=[JOB_B])
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=payload))

    body = json.loads(await omni_run_query(_fields_input(response_format=ResponseFormat.JSON)))

    assert body["remainingJobIds"] == [JOB_B]


async def test_run_query_plan_only_renders_sql_without_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _result_payload(status="PLANNED", result=None)
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_run_query(_fields_input(plan_only=True))

    assert "- Status: `PLANNED`" in result
    assert "SELECT * FROM order_items LIMIT 10" in result
    assert "no result table" in result


async def test_run_query_truncates_a_huge_sql_block(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _result_payload(summary={"sql": "SELECT " + "x" * 5_000})
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=payload))

    result = await omni_run_query(_fields_input())

    assert "SQL truncated at 2,000 characters" in result


async def test_run_query_surfaces_the_workbook_url_header(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload=_result_payload(), headers={"X-Omni-Workbook-Url": WORKBOOK_URL})
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    result = await omni_run_query(_fields_input(workbook_url=True))
    body = json.loads(await omni_run_query(_fields_input(workbook_url=True, response_format=ResponseFormat.JSON)))

    assert fake.calls[0][2]["json_body"]["workbookUrl"] is True
    assert f"- Workbook: {WORKBOOK_URL}" in result
    assert body["workbookUrl"] == WORKBOOK_URL


async def test_run_query_without_the_workbook_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=_result_payload()))

    result = await omni_run_query(_fields_input(workbook_url=True))

    assert "- Workbook:" not in result


async def test_run_query_csv_export_is_returned_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload="id,name\n1,alpha\n"))

    result = await omni_run_query(_fields_input(result_type="csv"))

    assert result == "id,name\n1,alpha\n"


async def test_run_query_json_export_object_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `json` export parses into a dict — it must not reach the Arrow renderer."""
    export = {"rows": [{"id": 1, "name": "alpha"}], "meta": {"total": 1}}
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=export))

    result = await omni_run_query(_fields_input(result_type="json"))

    assert json.loads(result) == export
    assert "no result table" not in result


async def test_run_query_json_export_array_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """...and a list export must not be rendered through Python's `repr`."""
    export = [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=export))

    result = await omni_run_query(_fields_input(result_type="json"))

    assert json.loads(result) == export
    assert "'id'" not in result


async def test_run_query_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=""))

    result = await omni_run_query(_fields_input())

    assert result == "_The API returned an empty response._"


async def test_run_query_408_surfaces_remaining_job_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(
        408,
        {"detail": "Query timed out", "remaining_job_ids": [JOB_A, JOB_B]},
    )
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(exc=error))

    result = await omni_run_query(_fields_input())

    assert result.startswith("Error (408):")
    assert JOB_A in result
    assert JOB_B in result
    assert "wait-for-query-results" in result


async def test_run_query_403_for_pat_impersonation(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(403, {"detail": "PATs can only run queries as their own user", "status": 403})
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(exc=error))

    result = await omni_run_query(_fields_input(user_id=JOB_A))

    assert result.startswith("Error (403):")
    assert "Personal Access Token" in result


# --- omni_wait_for_query_results --------------------------------------


async def test_wait_polls_until_the_jobs_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        payloads=[
            {"timed_out": True, "remaining_job_ids": [JOB_A, JOB_B]},
            {"timed_out": True, "remaining_job_ids": [JOB_B]},
            _result_payload(),
        ]
    )
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)
    slept = _patch_polling(monkeypatch, _Clock())

    result = await omni_wait_for_query_results(WaitForQueryResultsInput(job_ids=[JOB_A, JOB_B]))

    assert slept == [2.0, 2.0]
    assert [call[:2] for call in fake.calls] == [("GET", "/v1/query/wait")] * 3
    assert fake.calls[0][2] == {"params": {"job_ids": json.dumps([JOB_A, JOB_B])}, "timeout": 120.0}
    # The first response still listed both jobs, so poll two repeats them; only
    # poll three narrows to the single job the second response left pending.
    assert fake.calls[1][2]["params"] == {"job_ids": json.dumps([JOB_A, JOB_B])}
    assert fake.calls[2][2]["params"] == {"job_ids": json.dumps([JOB_B])}
    assert "**2** row(s) returned." in result
    assert "| 1 | alpha |" in result


async def test_wait_uses_the_configured_poll_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payloads=[{"timed_out": True, "remaining_job_ids": [JOB_A]}, _result_payload()])
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)
    slept = _patch_polling(monkeypatch, _Clock())

    await omni_wait_for_query_results(
        WaitForQueryResultsInput(job_ids=[JOB_A], poll_interval_seconds=5, timeout_seconds=30)
    )

    assert slept == [5.0]
    assert fake.calls[0][2]["timeout"] == 30.0


async def test_wait_clamps_each_request_to_the_remaining_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow long poll burns the budget even though the tool barely sleeps."""
    clock = _Clock()
    fake = _FakeClient(
        payload={"timed_out": True, "remaining_job_ids": [JOB_A]},
        clock=clock,
        seconds_per_call=50.0,
    )
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)
    slept = _patch_polling(monkeypatch, clock)

    result = await omni_wait_for_query_results(WaitForQueryResultsInput(job_ids=[JOB_A]))

    # 50s per request + 2s sleeps: three polls exhaust the 120s budget, and each
    # request's timeout is cut down to what is left of it.
    assert [call[2]["timeout"] for call in fake.calls] == [120.0, 68.0, 16.0]
    assert len(fake.calls) == 3
    assert slept == [2.0, 2.0]
    assert clock.now <= 120.0
    assert "Still running after 120s of the 120s budget" in result
    assert JOB_A in result


async def test_wait_gives_up_at_max_wait_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"timed_out": True, "remaining_job_ids": [JOB_A]})
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)
    clock = _Clock()
    slept = _patch_polling(monkeypatch, clock)

    result = await omni_wait_for_query_results(
        WaitForQueryResultsInput(job_ids=[JOB_A], max_wait_seconds=5, poll_interval_seconds=2)
    )

    assert slept == [2.0, 2.0]
    assert len(fake.calls) == 3
    assert [call[2]["timeout"] for call in fake.calls] == [5.0, 3.0, 1.0]
    assert "# Query still running" in result
    assert JOB_A in result
    assert "Still running after 4s of the 5s budget" in result


async def test_wait_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=_result_payload()))
    _patch_polling(monkeypatch, _Clock())

    payload = json.loads(
        await omni_wait_for_query_results(
            WaitForQueryResultsInput(job_ids=[JOB_A], response_format=ResponseFormat.JSON)
        )
    )

    assert payload["timedOut"] is False
    assert payload["rowCount"] == 2
    assert payload["rows"][0]["name"] == "alpha"


async def test_wait_handles_a_string_timed_out_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """`timed_out` is typed as a string on the run endpoint: `"false"` is done."""
    fake = _FakeClient(payload=_result_payload(timed_out="false"))
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)
    _patch_polling(monkeypatch, _Clock())

    result = await omni_wait_for_query_results(WaitForQueryResultsInput(job_ids=[JOB_A]))

    assert len(fake.calls) == 1
    assert "**2** row(s) returned." in result


async def test_wait_404_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(404, {"detail": "Job not found", "status": 404}, path="/api/v1/query/wait")
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(exc=error))

    result = await omni_wait_for_query_results(WaitForQueryResultsInput(job_ids=[JOB_A]))

    assert result.startswith("Error (404):")
    assert "Job not found" in result


async def test_wait_403_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(403, {"detail": "Forbidden", "status": 403}, path="/api/v1/query/wait")
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(exc=error))

    result = await omni_wait_for_query_results(WaitForQueryResultsInput(job_ids=[JOB_A]))

    assert result.startswith("Error (403):")


# --- omni_get_job_status ----------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("RUNNING", "poll again in 2-5 seconds"),
        ("COMPLETED", "Finished successfully."),
        ("FAILED", "The job failed"),
    ],
)
async def test_get_job_status_markdown(monkeypatch: pytest.MonkeyPatch, status: str, expected: str) -> None:
    payload = {"job_type": "refresh_schema", "job_id": JOB_A, "status": status}
    fake = _FakeClient(payload=payload)
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    result = await omni_get_job_status(GetJobStatusInput(job_id=JOB_A))

    assert fake.calls == [("GET", f"/v1/jobs/{JOB_A}/status", {})]
    assert f"- Job ID: `{JOB_A}`" in result
    assert "- Job type: `refresh_schema`" in result
    assert f"- Status: **{status}**" in result
    assert expected in result


async def test_get_job_status_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"job_type": "refresh_schema", "job_id": JOB_A, "status": "COMPLETED"}
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(payload=payload))

    body = json.loads(await omni_get_job_status(GetJobStatusInput(job_id=JOB_A, response_format=ResponseFormat.JSON)))

    assert body == payload


async def test_get_job_status_escapes_the_path_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(payload={"status": "RUNNING"})
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: fake)

    await omni_get_job_status(GetJobStatusInput(job_id="a/b c"))

    assert fake.calls[0][1] == "/v1/jobs/a%2Fb%20c/status"


async def test_get_job_status_403_for_unsupported_job_type(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(
        403,
        {"detail": "Job type not supported for status checks", "status": 403},
        path=f"/api/v1/jobs/{JOB_A}/status",
    )
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(exc=error))

    result = await omni_get_job_status(GetJobStatusInput(job_id=JOB_A))

    assert result.startswith("Error (403):")
    assert "Job type not supported" in result


async def test_get_job_status_400_for_a_bad_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _status_error(400, {"detail": "Bad Request: jobId: Invalid uuid", "status": 400})
    monkeypatch.setattr("omni_mcp.tools.queries.get_client", lambda: _FakeClient(exc=error))

    result = await omni_get_job_status(GetJobStatusInput(job_id="not-a-uuid"))

    assert result.startswith("Error (400):")
    assert "Invalid uuid" in result


# --- validation -------------------------------------------------------


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        RunQueryInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        WaitForQueryResultsInput(job_ids=[JOB_A], unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        GetJobStatusInput(job_id=JOB_A, unexpected="x")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_rows": 0},
        {"max_rows": 10_001},
        {"limit": 0},
        {"limit": -1},
        {"limit": 75_001},
        {"column_limit": 0},
        {"dimension_index": -1},
        {"timeout_seconds": 0},
        {"timeout_seconds": 601},
        {"cache": "Nope"},
        {"result_type": "parquet"},
    ],
)
def test_run_query_input_rejects_out_of_range_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _fields_input(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"job_ids": []},
        {"job_ids": [JOB_A], "max_wait_seconds": 0},
        {"job_ids": [JOB_A], "max_wait_seconds": 901},
        {"job_ids": [JOB_A], "poll_interval_seconds": 0.1},
        {"job_ids": [JOB_A], "poll_interval_seconds": 61},
        {"job_ids": [JOB_A], "max_rows": 0},
        {"job_ids": [JOB_A], "timeout_seconds": 0},
    ],
)
def test_wait_input_rejects_out_of_range_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        WaitForQueryResultsInput(**kwargs)


def test_get_job_status_input_requires_a_job_id() -> None:
    with pytest.raises(ValueError):
        GetJobStatusInput(job_id="")


def test_run_query_defaults() -> None:
    params = _fields_input()

    assert params.timeout_seconds == 120.0
    assert params.max_rows == 100
    assert params.response_format is ResponseFormat.MARKDOWN


def test_wait_defaults() -> None:
    params = WaitForQueryResultsInput(job_ids=[JOB_A])

    assert params.max_wait_seconds == 120.0
    assert params.poll_interval_seconds == 2.0
    assert params.max_rows == 100
