# Chat verification (Phase 7)

Derived numbers only: versions, pass rates, costs and latencies. No rows, no keys. Task 7.3 completes
this file; task 1.1 wrote the toolchain section.

## Python toolchain (task 1.1)

Resolved on 2026-10-23 (system clock, UTC) with the plan's policy: within the planned major, the newest
release that is at least 7 days old, pre-releases excluded. `pyproject.toml` keeps the plan's range
(`anthropic>=1.15,<2` in the group `chat`) and caps each package that the policy applies to with
`[tool.uv] exclude-newer-package` (cutoff 2026-10-16 01:00 UTC); `uv.lock` is committed, so a plain
`uv sync` reproduces these versions.

| Package | Resolved | Released | Newest on PyPI that day | Why this one |
|---|---|---|---|---|
| anthropic | 1.15.0 | 2026-10-15 | 2.1.0 (2026-10-21); newest 1.x is 1.17.0 (2026-10-20) | newest 1.x at least 7 days old (decision [18]) |
| httpx2 | 2.13.1 | 2026-09-23 | 2.16.0 (2026-10-21) | the SDK's HTTP library; 2.14 to 2.16 are inside the 7-day window |
| httpcore2 | 2.13.1 | 2026-09-23 | 2.16.0 (2026-10-21) | httpx2's transport layer, kept in step with it |
| pydantic | 2.14.1 | n/a | n/a | already locked by Phase 6 (fastapi); anthropic accepts `>=1.10,<3` |

Also new in the lock, pulled in by `anthropic` 1.15.0: docstring-parser 0.18.0, jiter 0.17.0,
sniffio 1.3.1, truststore 0.10.5 (a dependency of httpx2) and httpx2-jsfetch 1.0 (emscripten only).

`anthropic` 1.x runs on `httpx2`, not on `httpx`. The project's `httpx==0.28.1` (dev group, for the
FastAPI `TestClient`) stays as it is; the chat's tests use `httpx2.MockTransport` through
`anthropic.DefaultHttpxClient`.

### Gate A4: the in-process transport (task 1.1)

Result: **passed**, with no `extra_body` and no cast needed (`tests/unit/test_chat_sdk_gate.py`).

- `DefaultHttpxClient(transport=httpx2.MockTransport(...), event_hooks={"request": [...]})` is accepted by
  `anthropic.Anthropic(api_key=..., base_url=..., max_retries=..., timeout=..., http_client=...)`, and the
  request hook runs before the transport.
- `client.beta.messages.stream(...)` sends, typed by the SDK (1.15.0): `fallbacks="default"`,
  `output_config={"effort": ...}`, `eager_input_streaming` on a tool, a `role: "system"` message in
  `messages`, `thinking={"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": ...}}`,
  `cache_control` on a system block and at the top level, `tool_choice`, and `betas` (the header
  `anthropic-beta` carries `server-side-fallback-2026-07-01` and `thinking-binding-controls-2026-08-01`).
  The key travels in `x-api-key` only; there is no `Authorization` header.
- The final message exposes `input_transformations` from `message_start`.
- A stream can be closed from another thread. The SDK then does **not** raise: `get_final_message()`
  returns the partial message with `stop_reason` `None`. The chat's model class therefore treats a final
  message without a stop reason as "stream closed" and decides by its own cancel flag.
- A client from the `httpx` package is rejected.
- The first import of `anthropic.types.beta` took over a minute on the OneDrive drive (compiling the
  SDK's type modules); later runs are fast. A virtual environment on a native disk
  (`UV_PROJECT_ENVIRONMENT`) imports it in about 4 seconds.
