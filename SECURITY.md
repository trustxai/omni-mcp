# Security policy

## Supported versions

The latest released version on the `main` branch is supported.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub Security Advisories:
<https://github.com/trustxai/omni-mcp/security/advisories/new>. Please do not open a public
issue for a security problem.

Include the affected version, reproduction steps, and the impact you observed. Expect an initial
response within a few business days.

## Handling credentials

This server reads `OMNI_API_KEY` from the environment or a local `.env` file and sends it as a
bearer token. It never logs, prints, or returns the key in tool output — tool results report only
whether a key is configured. Keep `.env` out of version control (it is git-ignored), scope keys to
the least privilege that works, and revoke a key immediately if it may have been exposed.
