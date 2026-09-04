"""Uniform error-to-string mapping for tool responses."""

from __future__ import annotations

from typing import Any

import httpx


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _detail_from_body(response: httpx.Response) -> str:
    """Extract the human-readable detail from either documented error shape.

    The API returns `{"detail", "status"}` on most endpoints and
    `{"error", "message"}` on the older ones.
    """
    body = _body(response)
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        message = body.get("message")
        error = body.get("error")
        if isinstance(message, str) and message.strip():
            if isinstance(error, str) and error.strip() and error.strip() != message.strip():
                return f"{message.strip()} ({error.strip()})"
            return message.strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
    text = (response.text or "").strip()
    return text[:300] if text else "no error detail in the response body"


def _remaining_job_ids(response: httpx.Response) -> list[str]:
    body = _body(response)
    if isinstance(body, dict):
        jobs = body.get("remaining_job_ids")
        if isinstance(jobs, list):
            return [str(job) for job in jobs]
    return []


def handle_api_error(exc: Exception) -> str:
    """Map an exception to a human-readable `Error ...` string for the LLM.

    Tools never raise: every tool body is wrapped in try/except and returns
    this string on failure.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        detail = _detail_from_body(response)

        if status == 400:
            return (
                f"Error (400): Bad request – {detail}. Check parameter values, required fields, and enum options; "
                "the API rejects unknown body keys."
            )
        if status == 401:
            return (
                f"Error (401): Unauthorized – {detail}. OMNI_API_KEY is missing, invalid, disabled, or revoked. "
                "Create one in Omni → Settings → API keys (Organization API key), or use a Personal Access Token, "
                "and confirm OMNI_BASE_URL points at the same instance."
            )
        if status == 403:
            return (
                f"Error (403): Forbidden – {detail}. Either the caller's permission level is too low "
                "(many admin endpoints require Organization Admin), or the endpoint accepts Organization API keys "
                "only — Personal Access Tokens act as a single user and cannot use SCIM or `userId` impersonation."
            )
        if status == 404:
            return f"Error (404): Not found – {detail}. Double-check the id (and that it belongs to this instance)."
        if status == 408:
            jobs = _remaining_job_ids(response)
            jobs_text = ", ".join(jobs) if jobs else "none reported"
            return (
                f"Error (408): The query timed out – {detail}. Remaining job ids: {jobs_text}. "
                "Poll them with the wait-for-query-results tool until `timed_out` is false, "
                "or narrow the query (fewer rows, fewer fields, a tighter filter)."
            )
        if status == 409:
            return (
                f"Error (409): Conflict – {detail}. The resource already exists or was modified concurrently; "
                "re-read the current state before retrying."
            )
        if status == 410:
            link = response.headers.get("link", "")
            sunset = response.headers.get("sunset", "")
            parts = [f"Error (410): Endpoint removed – {detail}."]
            if link:
                parts.append(f"Successor: {link}.")
            if sunset:
                parts.append(f"Removed on {sunset}.")
            parts.append("Use the replacement endpoint's tool instead.")
            return " ".join(parts)
        if status == 429:
            return (
                f"Error (429): Rate limited – {detail}. The API allows 60 requests/minute per key; "
                "the client already retried while honouring `Retry-After`. Wait before retrying and "
                "request fewer, larger pages."
            )
        return f"Error ({status}): {detail}"

    if isinstance(exc, httpx.TimeoutException):
        return (
            "Error: the Omni API request timed out. Retry, raise OMNI_REQUEST_TIMEOUT_SECONDS, "
            "or reduce the amount of data requested."
        )
    if isinstance(exc, httpx.ConnectError):
        return "Error: could not connect to the Omni API. Check network access and that OMNI_BASE_URL is correct."
    if isinstance(exc, httpx.HTTPError):
        return f"Error: HTTP transport failure – {type(exc).__name__}: {exc}"
    if isinstance(exc, RuntimeError):
        return f"Error: {exc}"
    return f"Error: unexpected failure – {type(exc).__name__}: {exc}"
