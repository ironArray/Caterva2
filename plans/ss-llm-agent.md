# Server-Side LLM Agent Integration Plan

## Objective

Integrate `Caterva2-LLM-Agent` into Caterva2 as a server-side capability exposed through FastAPI, with JupyterLite notebooks acting only as thin clients.

The browser-side notebook must not hold provider API keys or execute provider SDK logic directly. All LLM calls and tool execution should happen in the Caterva2 server process or in a closely related server-side module.

## Implementation Status

Phase 1 is implemented in this branch.

Delivered:

- vendored server-side agent package under `caterva2/services/llm_agent/`
- FastAPI endpoints under `/api/llm-agent/...`
- in-memory TTL-backed session registry with per-session locking
- read-only tool set:
  - `list_roots`
  - `list_datasets`
  - `get_dataset_info`
  - `get_dataset_stats`
- provider abstraction with:
  - `mock` provider for tests and local verification
  - `groq` provider for real server-side calls
- `caterva2.Client` helpers:
  - `create_llm_session()`
  - `get_llm_session()`
  - `chat_llm()`
  - `reset_llm_session()`
  - `delete_llm_session()`
- JupyterLite notebook example wiring in
  `_caterva2/state/personal/cd46395a-3517-4c48-baba-186d14b0fd94/prova3.ipynb`
- staging wheel channel support in the GitHub wheel workflow
- release and developer documentation for the staging wheel flow

Not implemented yet:

- persisted agent sessions
- streaming responses
- richer artifacts beyond plain response payloads
- a dedicated widget chat UI for the server-side agent
- additional data-access and mutation tools

## Why Server-Side

- JupyterLite notebooks run in Pyodide in the browser, not in the server conda env.
- Provider API keys must stay server-side.
- The current `Caterva2-LLM-Agent` codebase assumes local Python imports, local filesystem logging, and direct provider access.
- Caterva2 already has the right server and notebook plumbing to expose a new API and consume it from notebooks.

## End State

At the end of this work:

- Caterva2 exposes authenticated FastAPI endpoints for agent sessions and chat turns.
- The server owns provider credentials and LLM client instantiation.
- Agent tools use Caterva2 server internals or the Caterva2 client library from the server side.
- JupyterLite notebooks call the Caterva2 agent API with `fetch` or Python `requests/httpx` from Pyodide.
- A reusable notebook helper or widget UI can be injected or imported without shipping provider secrets to the browser.
- `Caterva2-LLM-Agent` becomes either:
  - a vendored server module inside Caterva2, or
  - an installable library consumed by Caterva2 with a clean package API.

## Non-Goals

- Running the agent provider SDK in Pyodide.
- Exposing provider keys to users.
- Reproducing the current notebook-local `ask()` pattern as the primary architecture.
- Full multi-agent orchestration.
- Arbitrary code execution by the agent.

## Recommended Architecture

## High-Level Model

1. JupyterLite notebook sends a message to Caterva2 over HTTPS.
2. FastAPI endpoint authenticates the user and resolves the agent session.
3. Server-side agent loop calls the provider and executes Caterva2 tools.
4. Server returns a structured response payload:
   - assistant text
   - optional structured artifacts
   - usage and trace metadata
5. Notebook renders the payload.

## Session Ownership

- Agent sessions should be scoped to the authenticated user.
- Each session should have a server-generated `session_id`.
- Session state should live outside the browser.
- Session state should survive multiple notebook cells and page reloads if desired.

Recommended persistence model:

- Phase 1: in-memory session registry with TTL.
- Phase 2: persisted session state in SQLite or the existing Caterva2 DB layer.

## Integration Strategy

Use Caterva2 as the integration host and adapt `Caterva2-LLM-Agent` into a library-like core.

Recommended decomposition:

- `agent core`
  - provider-agnostic loop
  - conversation state
  - tool dispatch
  - response shaping
- `provider adapter`
  - Groq or future providers
  - reads credentials from server config/env
- `Caterva2 tool adapter`
  - list roots
  - list datasets
  - dataset info
  - dataset stats
  - future dataset access tools
- `FastAPI transport`
  - session endpoints
  - chat endpoint
  - error and auth handling
- `notebook client`
  - minimal helper for session creation and chat calls
  - optional `ipywidgets` UI

## Packaging Decision

Two acceptable paths:

### Option A: Vendor into Caterva2

Move the reusable logic into something like:

- `caterva2/services/llm_agent/`

Pros:

- simplest deployment
- direct control over imports and config
- easier access to Caterva2 internals

Cons:

- duplicates project ownership boundaries

### Option B: Keep `Caterva2-LLM-Agent` as a dependency

Refactor `../Caterva2-LLM-Agent` into a proper package and consume it from Caterva2.

Pros:

- cleaner project separation
- reusable outside Caterva2

Cons:

- requires packaging cleanup first
- introduces version coordination

Recommendation: start with Option A for fastest delivery, then extract a reusable package later if needed.

## Required Refactor in `Caterva2-LLM-Agent`

Even if vendored, the current code needs structural cleanup before integration.

### 1. Fix package imports

Current files use same-directory imports such as:

- `from config import ...`
- `from tools import ...`
- `from agent import ...`

These need to become package-relative or be reorganized into explicit modules.

### 2. Separate CLI and notebook glue from core agent logic

The current repository mixes:

- provider config
- core loop
- CLI entrypoint
- notebook UI code

Refactor into:

- `core.py`
- `providers.py`
- `tools.py`
- `schemas.py`
- `session.py`
- `api_models.py`
- `cli.py` or notebook examples outside the core

### 3. Remove direct `.env` assumptions from core

Provider configuration must be injected by Caterva2 server config, not loaded implicitly from `find_dotenv()`.

### 4. Remove thread dependency in the core API surface

Server-side threads are fine if desired, but the public agent core should not require `ThreadPoolExecutor`.
The orchestration layer should decide whether tool execution is:

- sequential
- threaded
- async

### 5. Replace filesystem logging assumptions

Current rotating log file behavior is not ideal as a hardcoded library default.

Use Caterva2 logging configuration instead:

- logger namespaced under `caterva2.llm_agent`
- no automatic filesystem writes unless explicitly configured

## Caterva2 Server Changes

## New Module Layout

Recommended new server modules:

- `caterva2/services/llm_agent/__init__.py`
- `caterva2/services/llm_agent/config.py`
- `caterva2/services/llm_agent/providers.py`
- `caterva2/services/llm_agent/core.py`
- `caterva2/services/llm_agent/tools.py`
- `caterva2/services/llm_agent/sessions.py`
- `caterva2/services/llm_agent/schemas.py`

Optional:

- `caterva2/services/llm_agent/notebook_client.py`
- `caterva2/services/llm_agent/render.py`

Actual phase 1 implementation note:

- API route definitions were added directly to `caterva2/services/server.py`
  instead of introducing a separate `router.py` module.

## Server Configuration

Add config keys for the agent to Caterva2 settings.

Suggested settings:

- `llm.enabled`
- `llm.provider`
- `llm.model`
- `llm.api_key_envvar`
- `llm.max_iterations`
- `llm.max_history_messages`
- `llm.max_total_tokens`
- `llm.request_timeout`
- `llm.session_ttl_seconds`
- `llm.allow_public_access`
- `llm.max_concurrent_sessions`
- `llm.max_input_chars`

Environment variables:

- `CATERVA2_LLM_ENABLED`
- `CATERVA2_LLM_PROVIDER`
- `CATERVA2_LLM_MODEL`
- `CATERVA2_LLM_API_KEY`

If Groq is the first provider:

- `GROQ_API_KEY`

Recommendation: normalize into Caterva2 settings and only read provider env vars in one place.

## FastAPI API Design

## Base Path

Suggested base path:

- `/api/llm-agent`

## Endpoints

### `POST /api/llm-agent/sessions`

Create a new session.

Request:

```json
{
  "name": "optional notebook session label",
  "root_hint": "@personal"
}
```

Response:

```json
{
  "session_id": "uuid",
  "created_at": "timestamp",
  "expires_at": "timestamp",
  "model": "configured-model"
}
```

### `GET /api/llm-agent/sessions/{session_id}`

Return session metadata and status.

### `DELETE /api/llm-agent/sessions/{session_id}`

Delete the session and its state.

### `POST /api/llm-agent/sessions/{session_id}/messages`

Submit a user turn and get the assistant response.

Request:

```json
{
  "message": "show me the datasets under @public/examples",
  "stream": false,
  "context": {
    "notebook_path": "@personal/user/prova3.ipynb"
  }
}
```

Response:

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "assistant": {
    "text": "I found ...",
    "artifacts": []
  },
  "usage": {
    "provider": "groq",
    "model": "openai/gpt-oss-120b",
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "trace": {
    "iterations": 2,
    "tool_calls": [
      {"name": "list_roots"},
      {"name": "list_datasets", "path": "@public/examples"}
    ]
  }
}
```

### `POST /api/llm-agent/sessions/{session_id}/reset`

Clears conversation history but keeps the session.

### `GET /api/llm-agent/sessions/{session_id}/history`

Optional debugging/admin endpoint.

Disable or restrict in production if needed.

## Streaming

Do not start with streaming.

Phase 1:

- synchronous request-response

Phase 2:

- SSE endpoint such as `POST /api/llm-agent/sessions/{session_id}/messages/stream`

Streaming is useful, but it adds complexity in:

- provider abstraction
- notebook client rendering
- proxy and timeout behavior

## Auth and Authorization

The agent endpoints must follow Caterva2 auth rules.

Rules:

- authenticated users can create and own personal sessions
- session access must be restricted to the owning user
- public anonymous access should be disabled by default
- if enabled for demo mode, tools must be read-only and scoped to public roots

Authorization checks must cover:

- session creation
- session lookup
- message submission
- reset and delete

## Session Storage

## Phase 1: In-Memory

Implement a process-local session registry:

- key: `session_id`
- value:
  - owner user id
  - created timestamp
  - expiry timestamp
  - message history
  - token counters
  - tool trace metadata

Pros:

- fastest to implement

Cons:

- sessions disappear on restart
- not suitable for multi-process horizontal scaling

## Phase 2: Persistent

Store session records and message history in the Caterva2 DB.

Suggested tables:

- `llm_agent_sessions`
- `llm_agent_messages`
- `llm_agent_tool_calls`

Minimal fields:

- session id
- owner id
- model
- created at
- updated at
- expires at
- status
- serialized message history

Recommendation:

- keep the persistence format simple first
- do not prematurely normalize every provider-specific detail

## Tool Layer Design

## Initial Tool Set

Mirror the existing agent toolset first:

- `list_roots`
- `list_datasets`
- `get_dataset_info`
- `get_dataset_stats`

## Tool Implementation Choice

Prefer direct server-side Caterva2 integration over making the server call itself over HTTP.

Preferred order:

1. call existing server-side service/helpers directly where practical
2. fall back to `caterva2.Client` against local server URL only if necessary

Benefits of direct calls:

- avoids extra HTTP hops
- simpler auth propagation
- easier performance control

## Tool Contracts

Every tool should return JSON-serializable data only.

Tool results should be normalized into:

```json
{
  "ok": true,
  "data": {},
  "error": null
}
```

or:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "DATASET_NOT_FOUND",
    "message": "..."
  }
}
```

This is preferable to returning ad hoc dicts with optional `"error"` keys.

## Future Tool Set

Do not ship these in phase 1 unless required:

- dataset slicing
- filter/where operations
- plotting helpers
- upload/mutate operations
- notebook file manipulation

Phase 1 should stay read-only.

## Provider Abstraction

Hide provider SDK details behind a small interface.

Suggested interface:

```python
class ChatProvider:
    def complete(
        self, *, model, messages, tools, tool_choice, temperature, max_tokens
    ): ...
```

Provider adapters:

- `GroqProvider`
- later `OpenAIProvider`
- later `LocalProvider`

The rest of Caterva2 should not know about provider SDK response classes.

Normalize provider outputs into internal models:

- `AssistantMessage`
- `ToolCall`
- `UsageInfo`

## Data Models

Create explicit Pydantic models for API boundaries and internal response shaping.

Suggested request/response models:

- `CreateSessionRequest`
- `CreateSessionResponse`
- `ChatRequest`
- `ChatResponse`
- `AssistantPayload`
- `Artifact`
- `UsagePayload`
- `TracePayload`
- `ResetSessionResponse`
- `SessionMetadata`

Do not pass raw provider objects across module boundaries.

## Notebook Client Design

## Phase 1: Simple Helper

Provide a notebook helper cell or small Python helper module that:

1. creates a session
2. sends user prompts
3. renders `assistant.text`
4. optionally renders artifacts

This can live as:

- a notebook example under `examples/`
- a small importable helper in Caterva2

Actual phase 1 implementation note:

- the notebook path chosen for the first integration is
  `_caterva2/state/personal/cd46395a-3517-4c48-baba-186d14b0fd94/prova3.ipynb`
- the helper uses `caterva2.Client(None)` from Pyodide and talks to the new
  server endpoints through the newly added client methods

## Phase 2: Widget UI

Adapt the existing `caterva2_agent.ipynb` UI into a client of the FastAPI API.

Important changes:

- notebook no longer imports provider SDK
- notebook no longer imports agent core
- notebook only talks to Caterva2 HTTP endpoints
- session state id is stored client-side, not message history

## Notebook Bootstrapping

Possible approaches:

- inject a helper cell into notebooks served through JupyterLite
- provide a ready-made notebook template
- expose a static JS or Python helper that notebooks can import

Recommendation:

- keep the existing bootstrap cell for `blosc2` and `caterva2`
- add a documented optional helper cell for the LLM client
- avoid silently injecting too much notebook UI logic into every notebook

## Error Handling

The API must distinguish:

- user input errors
- auth errors
- session ownership errors
- provider failures
- tool failures
- timeout and rate limit errors

Suggested HTTP mapping:

- `400` invalid request
- `401` unauthenticated
- `403` forbidden session access
- `404` session not found
- `409` session state conflict
- `422` validation error
- `429` rate limit / concurrency limit
- `502` provider upstream failure
- `504` provider timeout

Response bodies should include stable machine-readable codes.

## Security Constraints

Mandatory constraints for phase 1:

- read-only tools only
- no arbitrary Python execution
- no shell access
- no filesystem browsing outside Caterva2 permissions
- no provider key exposure in any notebook payload
- no session access across users
- no unbounded prompt size or conversation growth

Prompt and session guards:

- max input chars
- max iterations
- max history length
- max tool calls per turn
- request timeout
- per-user concurrency cap

## Observability

Add namespaced logging and basic metrics.

Log fields:

- user id
- session id
- request id
- provider
- model
- latency
- token usage
- tool names
- error code

Avoid logging:

- API keys
- full sensitive prompts by default
- raw dataset content unless explicitly needed for debug mode

Recommended metrics:

- sessions created
- active sessions
- chat requests
- request latency
- provider errors
- tool errors
- tokens used

## Testing Plan

## Unit Tests

Add tests for:

- provider adapter normalization
- session lifecycle
- tool dispatch
- tool result schema
- auth guards
- prompt size and iteration limits
- error mapping

## API Tests

Add FastAPI tests for:

- create session
- submit message
- reset session
- delete session
- forbidden access by another user
- anonymous access behavior
- provider failure handling

## Notebook Integration Tests

Minimal integration coverage:

- notebook helper can create a session
- notebook helper can submit a prompt and render response

This can be tested outside a full browser first by exercising the API and helper code.

## Manual Validation

Manual end-to-end checks:

1. open JupyterLite notebook
2. create agent session
3. ask for roots
4. ask for dataset listing
5. ask for dataset metadata
6. reset session
7. verify another user cannot access the session

Actual verification performed during implementation:

- full local pytest run was confirmed green outside the sandbox
- within the sandbox:
  - the new modules were compiled with `py_compile`
  - the agent core and tool flow were exercised inside the `blosc2` conda env
    using the `mock` provider
  - direct subprocess-backed HTTP pytest validation was blocked by local bind
    restrictions in the sandbox environment

## Implementation Phases

## Phase 0: Refactor Preparation

- decide vendored vs dependency path
- isolate reusable code from `Caterva2-LLM-Agent`
- define internal response models
- define provider adapter interface

Deliverable:

- clean server-usable agent core module

## Phase 1: Basic Server API

- add server config
- implement in-memory session registry
- implement provider adapter
- implement read-only tools
- add `create session`, `message`, `reset`, `delete`
- add API tests

Deliverable:

- FastAPI-backed agent usable from scripts or curl

Status:

- implemented

## Phase 2: Notebook Client

- implement simple notebook helper
- create example notebook
- document notebook usage
- optionally adapt existing widget UI

Deliverable:

- working JupyterLite notebook integration with server-side chat

Status:

- partially implemented
- a simple notebook helper flow is in place
- the richer widget-based chat UI remains future work

## Phase 3: Hardening

- persistent session storage
- structured logging and metrics
- rate limiting and concurrency controls
- timeout and retry policy tuning
- stricter tool result schemas

Deliverable:

- production-capable service behavior

Status:

- not implemented

## Phase 4: Extended Capabilities

- streaming responses
- richer artifacts
- plotting support
- dataset slice tools
- admin/debug endpoints

Deliverable:

- improved UX, still within the same server-side model

Status:

- not implemented

## Concrete File Targets in Caterva2

Likely files to touch:

- `caterva2/services/settings.py`
- `caterva2/services/server.py`
- `caterva2/services/db.py`
- `caterva2/services/schemas.py`
- `caterva2/services/llm_agent/*`
- `caterva2/tests/test_api.py`
- `caterva2/tests/services.py`
- `examples/` or `_caterva2/state/public/` for notebook examples

Optional:

- `README-DEVELOPERS.md`
- `README.md`
- release notes

## Key Design Choices to Resolve Early

These should be decided before implementation starts:

1. Vendor `Caterva2-LLM-Agent` into Caterva2 now, or keep it as an external dependency.
2. Keep sessions in memory first, or invest immediately in DB persistence.
3. Start with plain request-response only, or include streaming in the first API.
4. Restrict phase 1 to read-only tools, which is strongly recommended.
5. Whether the tool layer should use server internals directly or go through `caterva2.Client`.

Recommended answers:

1. Vendor now.
2. In-memory first.
3. No streaming in phase 1.
4. Read-only only.
5. Prefer direct server internals where practical.

## Acceptance Criteria

The first complete milestone is done when:

- an authenticated user can create an agent session through FastAPI
- a JupyterLite notebook can send a prompt to that session
- the server calls the provider using server-side credentials
- the agent can list roots and datasets and return structured answers
- session reset and delete work
- session ownership is enforced
- no provider secret appears in notebook code or responses
- tests cover the main API paths and auth boundaries

## Suggested First Slice

If implementing incrementally, the highest-value first slice is:

1. vendor or copy the minimal agent core into `caterva2/services/llm_agent/`
2. implement `POST /api/llm-agent/sessions`
3. implement `POST /api/llm-agent/sessions/{id}/messages`
4. support only `list_roots` and `list_datasets`
5. create a simple notebook helper cell that calls those endpoints

This is enough to validate the architecture before adding more tools and UI polish.
