"""Unit tests for the schedule and schedule-recipient tools against a fake client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_mcp.formatters import ResponseFormat
from omni_mcp.tools.schedules import (
    AddScheduleRecipientsInput,
    BulkManageEmailOnlyUsersInput,
    CreateScheduleInput,
    DeleteScheduleInput,
    EmailOnlyUserSpec,
    GetScheduleInput,
    ListScheduleRecipientsInput,
    ListSchedulesInput,
    ManageEmailOnlyUserInput,
    PauseScheduleInput,
    RemoveScheduleRecipientsInput,
    ResumeScheduleInput,
    TransferScheduleOwnershipInput,
    TriggerScheduleInput,
    UpdateScheduleInput,
    omni_add_schedule_recipients,
    omni_bulk_manage_email_only_users,
    omni_create_schedule,
    omni_delete_schedule,
    omni_get_schedule,
    omni_list_schedule_recipients,
    omni_list_schedules,
    omni_manage_email_only_user,
    omni_pause_schedule,
    omni_remove_schedule_recipients,
    omni_resume_schedule,
    omni_transfer_schedule_ownership,
    omni_trigger_schedule,
    omni_update_schedule,
)

SCHEDULE_ID = "123e4567-e89b-12d3-a456-426614174000"
SCHEDULE_PATH = f"/v1/schedules/{SCHEDULE_ID}"

LIST_PAYLOAD: dict[str, Any] = {
    "pageInfo": {"hasNextPage": True, "nextCursor": "2", "pageSize": 20, "totalRecords": 42},
    "records": [
        {
            "id": SCHEDULE_ID,
            "name": "Weekly Sales Report",
            "schedule": "0 9 ? * MON *",
            "timezone": "America/New_York",
            "identifier": "12db1a0a",
            "dashboardName": "Sales",
            "ownerId": "owner-1",
            "ownerName": "Jordan Lee",
            "lastCompletedAt": "2025-01-20T09:00:05.000Z",
            "lastStatus": "COMPLETE",
            "destinationType": "email",
            "format": "pdf",
            "recipientCount": 3,
            "content": "dashboard",
            "disabledAt": None,
            "systemDisabledAt": None,
        },
        {
            "id": "schedule-2",
            "name": "Paused Webhook",
            "schedule": "0 */6 * * ? *",
            "timezone": "UTC",
            "identifier": "abc99999",
            "dashboardName": "Ops",
            "ownerName": "Sam Rivera",
            "lastCompletedAt": None,
            "lastStatus": None,
            "destinationType": "webhook",
            "format": "json",
            "recipientCount": -1,
            "disabledAt": "2025-01-10T00:00:00.000Z",
        },
    ],
}

GET_PAYLOAD: dict[str, Any] = {
    "id": SCHEDULE_ID,
    "name": "Weekly Sales Report",
    "organizationId": "org-1",
    "entityId": "12db1a0a",
    "ownerId": "owner-1",
    "owner": {"name": "Jordan Lee"},
    "schedule": "0 9 ? * MON *",
    "timezone": "America/New_York",
    "fanOut": False,
    "killJobsOnFailure": False,
    "filterConfig": {"region": {"kind": "EQ", "left_side": "US", "type": "string"}},
    "conditionType": "RESULTS_PRESENT",
    "conditionQueryMapKey": "1",
    "disabledAt": None,
    "systemDisabledAt": None,
    "createdAt": "2025-01-15T10:00:00.000Z",
    "updatedAt": "2025-01-20T08:30:00.000Z",
    "destinations": [
        {
            "id": "dest-1",
            "format": "pdf",
            "lastStatus": "COMPLETE",
            "lastCompletedAt": "2025-01-20T09:00:05.000Z",
            "metadata": {"type": "email"},
            "recipients": [
                {"id": "r-1", "membershipId": "m-1", "membership": {"user": {"email": "analyst@example.com"}}}
            ],
            "userGroupRecipients": [],
        }
    ],
}


class _FakeClient:
    """Records `(method, path, kwargs)` calls; returns a canned payload."""

    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload if payload is not None else {}
        self._exc = exc
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._payload


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> _FakeClient:
    monkeypatch.setattr("omni_mcp.tools.schedules.get_client", lambda: fake)
    return fake


def _status_error(status: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://acme.omniapp.co/api/v1/schedules")
    response = httpx.Response(status, json={"detail": detail, "status": status}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


# --------------------------------------------------------------------------- list


async def test_list_schedules_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=LIST_PAYLOAD))

    result = await omni_list_schedules(ListSchedulesInput())

    assert fake.calls == [("GET", "/v1/schedules", {"params": {"pageSize": 20}})]
    assert "Weekly Sales Report" in result
    assert "`0 9 ? * MON *` America/New_York" in result
    assert "email/pdf, 3 recipient(s)" in result
    assert "2025-01-20T09:00:05.000Z (COMPLETE)" in result
    assert "paused" in result  # second record
    assert "n/a" in result  # webhook recipientCount of -1
    assert 'cursor="2"' in result


async def test_list_schedules_sends_every_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=LIST_PAYLOAD))

    await omni_list_schedules(
        ListSchedulesInput(
            q="sales",
            status="error",
            destination="slack",
            schedule_type="alert",
            content_type="single tile",
            sort_field="lastRun",
            sort_direction="asc",
            owner_id="owner-1",
            embed_entity="entity-1",
            identifier="12db1a0a",
            page_size=50,
            cursor=3,
        )
    )

    assert fake.calls == [
        (
            "GET",
            "/v1/schedules",
            {
                "params": {
                    "pageSize": 50,
                    "q": "sales",
                    "status": "error",
                    "destination": "slack",
                    "scheduleType": "alert",
                    "contentType": "single tile",
                    "sortField": "lastRun",
                    "sortDirection": "asc",
                    "ownerId": "owner-1",
                    "embedEntity": "entity-1",
                    "identifier": "12db1a0a",
                    "cursor": 3,
                }
            },
        )
    ]


async def test_list_schedules_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=LIST_PAYLOAD))

    payload = json.loads(await omni_list_schedules(ListSchedulesInput(response_format=ResponseFormat.JSON)))

    assert payload["count"] == 2
    assert payload["totalRecords"] == 42
    assert payload["nextCursor"] == "2"
    assert payload["items"][0]["name"] == "Weekly Sales Report"


async def test_list_schedules_reports_system_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "records": [
            {
                "id": "s-3",
                "name": "Broken",
                "systemDisabledAt": "2025-02-01T00:00:00.000Z",
                "systemDisabledReason": "missingQuery",
            }
        ],
        "pageInfo": {},
    }
    _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_list_schedules(ListSchedulesInput())

    assert "system-disabled (missingQuery)" in result


async def test_list_schedules_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, "User with id owner-9 does not exist")))

    result = await omni_list_schedules(ListSchedulesInput(owner_id="owner-9"))

    assert result.startswith("Error (404):")
    assert "does not exist" in result


# ---------------------------------------------------------------------------- get


async def test_get_schedule_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GET_PAYLOAD))

    result = await omni_get_schedule(GetScheduleInput(schedule_id=SCHEDULE_ID))

    assert fake.calls == [("GET", SCHEDULE_PATH, {"params": None})]
    assert "# Weekly Sales Report" in result
    assert f"`{SCHEDULE_ID}`" in result
    assert "Jordan Lee" in result
    assert "`0 9 ? * MON *` in America/New_York" in result
    assert "Status: **active**" in result
    assert "RESULTS_PRESENT" in result
    assert "analyst@example.com" in result
    assert "2025-01-20T09:00:05.000Z" in result


async def test_get_schedule_passes_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GET_PAYLOAD))

    await omni_get_schedule(GetScheduleInput(schedule_id=SCHEDULE_ID, user_id="membership-1"))

    assert fake.calls == [("GET", SCHEDULE_PATH, {"params": {"userId": "membership-1"}})]


async def test_get_schedule_quotes_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload=GET_PAYLOAD))

    await omni_get_schedule(GetScheduleInput(schedule_id="weird/id?x=1"))

    assert fake.calls[0][1] == "/v1/schedules/weird%2Fid%3Fx%3D1"


async def test_get_schedule_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(payload=GET_PAYLOAD))

    payload = json.loads(
        await omni_get_schedule(GetScheduleInput(schedule_id=SCHEDULE_ID, response_format=ResponseFormat.JSON))
    )

    assert payload["id"] == SCHEDULE_ID
    assert payload["destinations"][0]["format"] == "pdf"


async def test_get_schedule_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(403, "User cannot view the associated dashboard")))

    result = await omni_get_schedule(GetScheduleInput(schedule_id=SCHEDULE_ID))

    assert result.startswith("Error (403):")


# ------------------------------------------------------------------------- create


async def test_create_schedule_email_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"id": "new-1", "message": "Successfully created schedule"}))

    result = await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Daily Sales",
            cron="0 9 ? * * *",
            timezone="UTC",
            format="pdf",
            destination_type="email",
            recipients=["analyst@example.com"],
            subject="Daily Sales report",
            paper_format="legal",
            paper_orientation="landscape",
            expand_tables_to_show_all_rows=True,
            fan_out=False,
            filter_config={"region": {"kind": "EQ", "left_side": "US", "type": "string"}},
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/schedules",
            {
                "params": None,
                "json_body": {
                    "identifier": "12db1a0a",
                    "name": "Daily Sales",
                    "schedule": "0 9 ? * * *",
                    "timezone": "UTC",
                    "format": "pdf",
                    "destinationType": "email",
                    "filterConfig": {"region": {"kind": "EQ", "left_side": "US", "type": "string"}},
                    "expandTablesToShowAllRows": True,
                    "paperFormat": "legal",
                    "paperOrientation": "landscape",
                    "recipients": ["analyst@example.com"],
                    "subject": "Daily Sales report",
                    "fanOut": False,
                },
            },
        )
    ]
    assert "Created schedule **Daily Sales** (id `new-1`)" in result
    assert "email/pdf" in result


async def test_create_schedule_slack_channel_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"id": "new-2"}))

    await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Weekly Report",
            cron="0 9 ? * MON *",
            timezone="America/New_York",
            format="pdf",
            destination_type="slack",
            recipients="C01234567",
            slack_recipient_type="channel",
            text_body="Here is your weekly report",
        )
    )

    assert fake.calls[0][2]["json_body"] == {
        "identifier": "12db1a0a",
        "name": "Weekly Report",
        "schedule": "0 9 ? * MON *",
        "timezone": "America/New_York",
        "format": "pdf",
        "destinationType": "slack",
        "recipients": "C01234567",
        "textBody": "Here is your weekly report",
        "slackRecipientType": "channel",
    }


async def test_create_schedule_sftp_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"id": "new-3"}))

    await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Nightly export",
            cron="0 2 ? * * *",
            timezone="UTC",
            format="xlsx",
            destination_type="sftp",
            address="sftp.example.com",
            port=22,
            username="reporting",
            path="/uploads/nightly",
            password_unencrypted="s3cret",
        )
    )

    body = fake.calls[0][2]["json_body"]
    assert body["address"] == "sftp.example.com"
    assert body["port"] == 22
    assert body["username"] == "reporting"
    assert body["path"] == "/uploads/nightly"
    assert body["passwordUnencrypted"] == "s3cret"


async def test_create_schedule_never_echoes_the_sftp_password(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"id": "new-3b"}))

    result = await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Nightly export",
            cron="0 2 ? * * *",
            timezone="UTC",
            format="xlsx",
            destination_type="sftp",
            address="sftp.example.com",
            port=22,
            username="reporting",
            password_unencrypted="sup3r-s3cret",
        )
    )

    assert fake.calls[0][2]["json_body"]["passwordUnencrypted"] == "sup3r-s3cret"
    assert "sup3r-s3cret" not in result
    assert "passwordUnencrypted" not in result


async def test_get_schedule_never_echoes_destination_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict(GET_PAYLOAD)
    payload["destinations"] = [
        {
            "id": "dest-2",
            "format": "xlsx",
            "metadata": {"type": "sftp", "passwordUnencrypted": "sup3r-s3cret"},
            "recipients": [],
        }
    ]
    _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_get_schedule(GetScheduleInput(schedule_id=SCHEDULE_ID))

    assert "sftp" in result
    assert "sup3r-s3cret" not in result


async def test_create_schedule_s3_reports_trust_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(
        monkeypatch,
        _FakeClient(
            payload={
                "id": "new-4",
                "delivererRoleArn": "arn:aws:iam::123456789012:role/Deliverer",
                "externalId": "ext-123",
            }
        ),
    )

    result = await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Daily export",
            cron="0 9 ? * * *",
            timezone="UTC",
            format="csv",
            destination_type="s3",
            bucket_name="reports-bucket",
            region="us-east-1",
            role_arn="arn:aws:iam::123456789012:role/DeliveryRole",
            key_prefix="reports/daily/",
            filename="{{entityName}}-{{currentDate}}",
        )
    )

    body = fake.calls[0][2]["json_body"]
    assert body["bucketName"] == "reports-bucket"
    assert body["roleArn"] == "arn:aws:iam::123456789012:role/DeliveryRole"
    assert "arn:aws:iam::123456789012:role/Deliverer" in result
    assert "ext-123" in result


async def test_create_schedule_raw_body_alone_is_sent_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"id": "new-5"}))
    raw = {
        "identifier": "raw-dash",
        "name": "Raw",
        "schedule": "0 9 ? * * *",
        "timezone": "UTC",
        "format": "csv",
        "destinationType": "email",
        "recipients": ["analyst@example.com"],
        "subject": "Raw",
    }

    result = await omni_create_schedule(CreateScheduleInput(body=raw))

    assert fake.calls == [("POST", "/v1/schedules", {"params": None, "json_body": raw})]
    assert "Created schedule **Raw**" in result
    assert "raw-dash" in result


def test_create_rejects_raw_body_mixed_with_typed_fields() -> None:
    with pytest.raises(ValueError, match="cannot be combined with the typed field"):
        CreateScheduleInput(
            name="Ignored",
            destination_type="slack",
            body={"identifier": "raw-dash", "name": "Raw"},
        )


def test_create_raw_body_still_allows_the_user_id_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    params = CreateScheduleInput(body={"identifier": "raw-dash", "name": "Raw"}, user_id="membership-1")

    assert params.user_id == "membership-1"


async def test_create_schedule_passes_user_id_and_test_now(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"id": "new-6"}))

    result = await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Test Delivery",
            cron="0 0 1 1 ? 2099",
            timezone="UTC",
            format="pdf",
            destination_type="email",
            recipients=["analyst@example.com"],
            subject="Test Delivery",
            test_now=True,
            user_id="membership-1",
        )
    )

    assert fake.calls[0][2]["params"] == {"userId": "membership-1"}
    assert fake.calls[0][2]["json_body"]["testNow"] is True
    assert "test delivery" in result


async def test_create_schedule_alert_condition_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"id": "new-7"}))

    await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Data alert",
            cron="0 */6 * * ? *",
            timezone="UTC",
            format="json",
            destination_type="webhook",
            url="https://example.com/hooks/alert",
            condition_type="RESULTS_PRESENT",
            condition_query_map_key="1",
            query_identifier_map_key="1",
            override_row_limit=True,
            max_row_limit=1000,
        )
    )

    body = fake.calls[0][2]["json_body"]
    assert body["conditionType"] == "RESULTS_PRESENT"
    assert body["conditionQueryMapKey"] == "1"
    assert body["queryIdentifierMapKey"] == "1"
    assert body["overrideRowLimit"] is True
    assert body["maxRowLimit"] == 1000
    assert body["url"] == "https://example.com/hooks/alert"


async def test_create_schedule_error_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(400, "schedule: Schedule is an invalid cron expression")))

    result = await omni_create_schedule(
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Bad cron",
            cron="not-a-cron",
            timezone="UTC",
            format="csv",
            destination_type="email",
            recipients=["analyst@example.com"],
            subject="Bad cron",
        )
    )

    assert result.startswith("Error (400):")
    assert "invalid cron expression" in result


# ------------------------------------------------------------------------- update


async def test_update_schedule_sends_only_given_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True, "scheduledTaskId": SCHEDULE_ID}))

    result = await omni_update_schedule(
        UpdateScheduleInput(
            schedule_id=SCHEDULE_ID,
            cron="0 9 ? * MON *",
            timezone="America/New_York",
            subject="Weekly Sales",
        )
    )

    assert fake.calls == [
        (
            "PUT",
            SCHEDULE_PATH,
            {
                "json_body": {
                    "schedule": "0 9 ? * MON *",
                    "timezone": "America/New_York",
                    "subject": "Weekly Sales",
                }
            },
        )
    ]
    assert f"Updated schedule `{SCHEDULE_ID}`" in result
    assert "`schedule`" in result


async def test_update_schedule_raw_body_alone_is_sent_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))
    raw = {"name": "Weekly Sales Report", "schedule": "0 9 ? * MON *", "timezone": "America/New_York"}

    await omni_update_schedule(UpdateScheduleInput(schedule_id=SCHEDULE_ID, body=raw))

    assert fake.calls == [("PUT", SCHEDULE_PATH, {"json_body": raw})]


def test_update_rejects_raw_body_mixed_with_typed_fields() -> None:
    with pytest.raises(ValueError, match="cannot be combined with the typed field"):
        UpdateScheduleInput(schedule_id=SCHEDULE_ID, name="ignored", body={"name": "Raw"})


async def test_update_schedule_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, f"Scheduled task with id {SCHEDULE_ID} does not exist")))

    result = await omni_update_schedule(UpdateScheduleInput(schedule_id=SCHEDULE_ID, name="New name"))

    assert result.startswith("Error (404):")


# ------------------------------------------------------------- lifecycle mutations


async def test_delete_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_delete_schedule(DeleteScheduleInput(schedule_id=SCHEDULE_ID))

    assert fake.calls == [("DELETE", SCHEDULE_PATH, {})]
    assert f"Deleted schedule `{SCHEDULE_ID}`" in result


async def test_pause_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_pause_schedule(PauseScheduleInput(schedule_id=SCHEDULE_ID))

    assert fake.calls == [("PUT", f"{SCHEDULE_PATH}/pause", {})]
    assert f"Paused schedule `{SCHEDULE_ID}`" in result


async def test_pause_schedule_error_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(400, "Schedule is already paused")))

    result = await omni_pause_schedule(PauseScheduleInput(schedule_id=SCHEDULE_ID))

    assert result.startswith("Error (400):")
    assert "already paused" in result


async def test_resume_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_resume_schedule(ResumeScheduleInput(schedule_id=SCHEDULE_ID))

    assert fake.calls == [("PUT", f"{SCHEDULE_PATH}/resume", {})]
    assert f"Resumed schedule `{SCHEDULE_ID}`" in result


async def test_trigger_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_trigger_schedule(TriggerScheduleInput(schedule_id=SCHEDULE_ID))

    assert fake.calls == [("POST", f"{SCHEDULE_PATH}/trigger", {})]
    assert f"Triggered schedule `{SCHEDULE_ID}`" in result
    assert "real delivery" in result


async def test_trigger_schedule_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(409, "Cannot trigger an inactive schedule")))

    result = await omni_trigger_schedule(TriggerScheduleInput(schedule_id=SCHEDULE_ID))

    assert result.startswith("Error (409):")
    assert "inactive schedule" in result


async def test_transfer_schedule_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"success": True}))

    result = await omni_transfer_schedule_ownership(
        TransferScheduleOwnershipInput(schedule_id=SCHEDULE_ID, user_id="user-9")
    )

    assert fake.calls == [("PUT", f"{SCHEDULE_PATH}/transfer-ownership", {"json_body": {"userId": "user-9"}})]
    assert "Transferred ownership" in result
    assert "`user-9`" in result


async def test_transfer_schedule_ownership_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(403, "New owner does not have permission to view the dashboard")))

    result = await omni_transfer_schedule_ownership(
        TransferScheduleOwnershipInput(schedule_id=SCHEDULE_ID, user_id="user-9")
    )

    assert result.startswith("Error (403):")


# ---------------------------------------------------------------------- recipients


async def test_list_schedule_recipients_email(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "type": "email",
        "recipients": [
            {"email": "analyst@example.com", "emailOnly": False, "id": "r-1", "name": "Analyst"},
            {"email": "external@example.com", "emailOnly": True, "id": "r-2", "name": "External"},
        ],
    }
    fake = _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_list_schedule_recipients(ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID))

    assert fake.calls == [("GET", f"{SCHEDULE_PATH}/recipients", {})]
    assert "# Schedule recipients (email)" in result
    assert "analyst@example.com" in result
    assert "external@example.com" in result
    assert "**2** recipient(s)." in result


async def test_list_schedule_recipients_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"type": "slack", "recipients": [{"recipientType": "channel", "slackId": "C123456789"}]}
    _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_list_schedule_recipients(ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID))

    assert "C123456789" in result
    assert "channel" in result


async def test_list_schedule_recipients_sftp_and_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch, _FakeClient(payload={"type": "sftp", "address": "sftp.example.com", "port": 22, "username": "u"})
    )
    sftp = await omni_list_schedule_recipients(ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID))
    assert "sftp.example.com" in sftp

    _patch(monkeypatch, _FakeClient(payload={"type": "webhook", "url": "https://example.com/webhook"}))
    webhook = await omni_list_schedule_recipients(ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID))
    assert "https://example.com/webhook" in webhook


async def test_list_schedule_recipients_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "type": "s3",
        "bucketName": "reports-bucket",
        "region": "us-east-1",
        "roleArn": "arn:aws:iam::123456789012:role/DeliveryRole",
        "externalId": "ext-123",
        "keyPrefix": "reports/daily/",
        "filename": "{{entityName}}-{{currentDate}}",
    }
    _patch(monkeypatch, _FakeClient(payload=payload))

    result = await omni_list_schedule_recipients(ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID))

    assert "reports-bucket" in result
    assert "ext-123" in result


async def test_list_schedule_recipients_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"type": "email", "recipients": [{"email": "analyst@example.com", "id": "r-1"}]}
    _patch(monkeypatch, _FakeClient(payload=payload))

    decoded = json.loads(
        await omni_list_schedule_recipients(
            ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID, response_format=ResponseFormat.JSON)
        )
    )

    assert decoded["type"] == "email"
    assert decoded["recipients"][0]["email"] == "analyst@example.com"


async def test_list_schedule_recipients_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(404, f"Scheduled task with id {SCHEDULE_ID} does not exist")))

    result = await omni_list_schedule_recipients(ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID))

    assert result.startswith("Error (404):")


async def test_add_schedule_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"addedRecipientsCount": 2, "success": True}))

    result = await omni_add_schedule_recipients(
        AddScheduleRecipientsInput(
            schedule_id=SCHEDULE_ID, emails=["analyst@example.com"], user_ids=["9e8719d9-276a-4964-9395-a493189a247c"]
        )
    )

    assert fake.calls == [
        (
            "PUT",
            f"{SCHEDULE_PATH}/add-recipients",
            {
                "json_body": {
                    "emails": ["analyst@example.com"],
                    "userIds": ["9e8719d9-276a-4964-9395-a493189a247c"],
                }
            },
        )
    ]
    assert "Added 2 recipient(s)" in result


async def test_add_schedule_recipients_error_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(400, "Cannot add recipients to destination type slack")))

    result = await omni_add_schedule_recipients(
        AddScheduleRecipientsInput(schedule_id=SCHEDULE_ID, emails=["analyst@example.com"])
    )

    assert result.startswith("Error (400):")
    assert "destination type slack" in result


async def test_remove_schedule_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"removedRecipientsCount": 1, "success": True}))

    result = await omni_remove_schedule_recipients(
        RemoveScheduleRecipientsInput(schedule_id=SCHEDULE_ID, emails=["former.member@example.com"])
    )

    assert fake.calls == [
        ("PUT", f"{SCHEDULE_PATH}/remove-recipients", {"json_body": {"emails": ["former.member@example.com"]}})
    ]
    assert "Removed 1 recipient(s)" in result


# ---------------------------------------------------------------- email-only users


async def test_manage_email_only_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"email": "stakeholder@example.com", "userId": "9e8719d9-276a"}))

    result = await omni_manage_email_only_user(
        ManageEmailOnlyUserInput(
            email="stakeholder@example.com",
            user_attributes={"region": ["US", "EU"], "omni_user_timezone": "America/New_York"},
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/users/email-only",
            {
                "json_body": {
                    "email": "stakeholder@example.com",
                    "userAttributes": {"region": ["US", "EU"], "omni_user_timezone": "America/New_York"},
                }
            },
        )
    ]
    assert "stakeholder@example.com" in result
    assert "`9e8719d9-276a`" in result


async def test_manage_email_only_user_without_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, _FakeClient(payload={"email": "stakeholder@example.com", "userId": "u-1"}))

    await omni_manage_email_only_user(ManageEmailOnlyUserInput(email="stakeholder@example.com"))

    assert fake.calls[0][2]["json_body"] == {"email": "stakeholder@example.com"}


async def test_bulk_manage_email_only_users(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(
        monkeypatch,
        _FakeClient(
            payload={
                "results": [
                    {"email": "analyst@example.com", "userId": "u-1"},
                    {"email": "lead@example.com", "userId": "u-2"},
                ]
            }
        ),
    )

    result = await omni_bulk_manage_email_only_users(
        BulkManageEmailOnlyUsersInput(
            users=[
                EmailOnlyUserSpec(email="analyst@example.com", user_attributes={"region": ["US"]}),
                EmailOnlyUserSpec(email="lead@example.com"),
            ]
        )
    )

    assert fake.calls == [
        (
            "POST",
            "/v1/users/email-only/bulk",
            {
                "json_body": {
                    "users": [
                        {"email": "analyst@example.com", "userAttributes": {"region": ["US"]}},
                        {"email": "lead@example.com"},
                    ]
                }
            },
        )
    ]
    assert "**2** email-only user(s)" in result
    assert "analyst@example.com → `u-1`" in result


async def test_bulk_manage_email_only_users_error_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient(exc=_status_error(403, "Organization API key required")))

    result = await omni_bulk_manage_email_only_users(
        BulkManageEmailOnlyUsersInput(users=[EmailOnlyUserSpec(email="analyst@example.com")])
    )

    assert result.startswith("Error (403):")


# --------------------------------------------------------------------- validation


def test_create_requires_core_fields() -> None:
    with pytest.raises(ValueError, match="Missing required field"):
        CreateScheduleInput(identifier="12db1a0a", name="No cron")


def test_create_accepts_raw_body_without_core_fields() -> None:
    params = CreateScheduleInput(body={"identifier": "12db1a0a", "name": "Raw"})

    assert params.body == {"identifier": "12db1a0a", "name": "Raw"}


def test_cron_string_is_accepted_where_the_api_documents_schedule() -> None:
    """The API's `schedule` key is a cron string, so `body` must not shadow that name."""
    params = CreateScheduleInput(
        identifier="12db1a0a",
        name="Daily",
        cron="0 9 ? * MON *",
        timezone="UTC",
        format="csv",
        destination_type="email",
    )

    assert params.cron == "0 9 ? * MON *"
    assert params.body is None


def test_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="at least one field"):
        UpdateScheduleInput(schedule_id=SCHEDULE_ID)


def test_recipient_inputs_require_a_recipient() -> None:
    with pytest.raises(ValueError, match="at least one recipient"):
        AddScheduleRecipientsInput(schedule_id=SCHEDULE_ID)
    with pytest.raises(ValueError, match="at least one recipient"):
        RemoveScheduleRecipientsInput(schedule_id=SCHEDULE_ID)


def test_enum_fields_reject_unknown_values() -> None:
    with pytest.raises(ValueError):
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Bad format",
            cron="0 9 ? * * *",
            timezone="UTC",
            format="parquet",  # type: ignore[arg-type]
            destination_type="email",
        )
    with pytest.raises(ValueError):
        CreateScheduleInput(
            identifier="12db1a0a",
            name="Bad destination",
            cron="0 9 ? * * *",
            timezone="UTC",
            format="csv",
            destination_type="carrier-pigeon",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        ListSchedulesInput(sort_field="whenever")  # type: ignore[arg-type]


def test_numeric_bounds() -> None:
    with pytest.raises(ValueError):
        ListSchedulesInput(page_size=0)
    with pytest.raises(ValueError):
        ListSchedulesInput(page_size=101)
    with pytest.raises(ValueError):
        ListSchedulesInput(cursor=0)
    with pytest.raises(ValueError):
        UpdateScheduleInput(schedule_id=SCHEDULE_ID, port=70000)
    with pytest.raises(ValueError):
        UpdateScheduleInput(schedule_id=SCHEDULE_ID, max_row_limit=0)


def test_string_length_bounds() -> None:
    with pytest.raises(ValueError):
        GetScheduleInput(schedule_id="")
    with pytest.raises(ValueError):
        UpdateScheduleInput(schedule_id=SCHEDULE_ID, bucket_name="ab")
    with pytest.raises(ValueError):
        BulkManageEmailOnlyUsersInput(users=[EmailOnlyUserSpec(email=f"user{i}@example.com") for i in range(21)])
    with pytest.raises(ValueError):
        BulkManageEmailOnlyUsersInput(users=[])


def test_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ListSchedulesInput(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        GetScheduleInput(schedule_id=SCHEDULE_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        DeleteScheduleInput(schedule_id=SCHEDULE_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        PauseScheduleInput(schedule_id=SCHEDULE_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ResumeScheduleInput(schedule_id=SCHEDULE_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        TriggerScheduleInput(schedule_id=SCHEDULE_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        TransferScheduleOwnershipInput(schedule_id=SCHEDULE_ID, user_id="u", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ListScheduleRecipientsInput(schedule_id=SCHEDULE_ID, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        ManageEmailOnlyUserInput(email="a@example.com", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        CreateScheduleInput(body={"name": "Raw"}, unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        UpdateScheduleInput(schedule_id=SCHEDULE_ID, name="n", unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        AddScheduleRecipientsInput(schedule_id=SCHEDULE_ID, emails=["a@example.com"], unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        RemoveScheduleRecipientsInput(schedule_id=SCHEDULE_ID, emails=["a@example.com"], unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        BulkManageEmailOnlyUsersInput(users=[EmailOnlyUserSpec(email="a@example.com")], unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        EmailOnlyUserSpec(email="a@example.com", unexpected="x")  # type: ignore[call-arg]
