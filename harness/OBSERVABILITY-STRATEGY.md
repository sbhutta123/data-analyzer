# Smart Dataset Explainer — Observability Strategy

How we understand what the system is doing at runtime. This document is prescriptive — follow these patterns when adding error handling, LLM calls, or session operations.

---

## Philosophy

Traditional observability logs everything to persistent storage and waits for a human to investigate when something breaks. That's wasteful for a prototype:

- Most logged data is never read.
- Persistent log infrastructure (ELK, Datadog, etc.) is YAGNI for this project.
- A human reviewing raw logs is slower than an LLM reviewing structured context.

**Our approach:** Keep a rolling context buffer in memory for each session. When an error occurs, an LLM troubleshooter agent receives that context and produces a human-readable diagnosis. No persistent storage. No infrastructure. The context lives and dies with the session.

This complements — not replaces — the test strategy. Tests verify correctness before deployment. The troubleshooter diagnoses failures at runtime.

---

## Principles

1. **React, don't record.** Don't log for the sake of logging. Capture context in memory so the troubleshooter has what it needs when an error occurs. If nothing goes wrong, the context is never examined and is discarded when the session ends.

2. **The LLM is the reviewer.** When an error fires, hand the context buffer to a troubleshooter agent. It reads the recent operation history, the error, and the session state, then produces a plain-English diagnosis. The human sees a diagnosis, not a stack trace.

3. **Session-scoped, not global.** Each session maintains its own context buffer. There is no cross-session log aggregation, no central log store, no global metrics. The unit of observability is one user's conversation.

4. **Shallow buffer, not deep archive.** The buffer holds a fixed window of recent operations (not the entire session history). Older entries roll off. The goal is "what happened in the last N operations that led to this error," not "what happened since the session started."

5. **Instrument boundaries, not internals.** Capture context at the points where the system interacts with something non-deterministic or external: LLM calls, sandboxed code execution, file parsing. Don't instrument pure functions or internal data transformations — those are covered by tests.

---

## The Runtime Context Buffer

### What it is

A per-session, in-memory list of recent operations. Each entry is a lightweight dict capturing what happened at a system boundary. The buffer has a fixed max length; when full, the oldest entry is dropped.

### What an entry contains

Each entry captures one operation at a system boundary:

```python
@dataclass
class ContextEntry:
    timestamp: str              # ISO 8601, for ordering
    operation: str              # e.g. "llm_call", "code_execution", "file_parse", "data_clean"
    input_summary: str          # Brief description of what went in (NOT the full payload)
    output_summary: str | None  # Brief description of what came out (None if it hasn't completed)
    success: bool | None        # None while in-flight, True/False on completion
    error: str | None           # Error message + type if failed, None otherwise
    metadata: dict              # Operation-specific context (see per-operation guidance below)
```

### What it does NOT contain

- **Full LLM prompts or responses** — Too large. Summarize: "Asked for cleaning suggestions for 5 columns" / "Returned 3 suggestions in valid JSON."
- **Full dataframes or datasets** — Reference by shape and column names only.
- **API keys or credentials** — Never, under any circumstances.
- **User PII** — The buffer holds operation metadata, not user data content.

### Buffer sizing

Start with a max length of **20 entries**. This is enough to trace the chain of operations leading to most errors without holding the entire session history. Adjust based on experience — if the troubleshooter consistently needs more context, increase it; if most diagnoses only need the last 3–5 entries, decrease it.

```python
CONTEXT_BUFFER_MAX_LENGTH = 20
```

### Where it lives

The context buffer is a field on the session object in `session.py`. It is created when the session starts and garbage-collected when the session ends. It is never persisted to disk.

---

## What to Instrument

Capture context entries at these boundaries, in priority order:

### 1. LLM calls (highest priority)

Every call to the Anthropic/OpenAI API gets a context entry. This is the most common source of runtime failures (malformed responses, unexpected formats, refusals, rate limits).

**Metadata to capture:**
- `model`: Which model was called
- `purpose`: What the call was for ("analysis_query", "cleaning_suggestion", "code_generation")
- `prompt_summary`: One sentence describing what was asked (NOT the full prompt)
- `response_format`: What format was expected ("json", "code", "text")
- `parse_success`: Whether the response parsed into the expected format
- `token_usage`: Input/output token counts (useful for diagnosing truncation)

**Example entry:**
```python
{
    "operation": "llm_call",
    "input_summary": "Asked for Python code to visualize correlation matrix for 8 numeric columns",
    "output_summary": "Returned JSON with code field (47 lines) and explanation field",
    "success": True,
    "metadata": {
        "model": "claude-sonnet-4-20250514",
        "purpose": "analysis_query",
        "response_format": "json",
        "parse_success": True,
        "token_usage": {"input": 1820, "output": 645},
    },
}
```

### 2. Sandboxed code execution

Every `exec()` call in `executor.py` gets a context entry. LLM-generated code is the second most common failure point.

**Metadata to capture:**
- `code_summary`: First and last line of the code, plus line count (NOT the full code — it's in the session's code history already)
- `namespace_keys`: What variables were available in the execution namespace
- `had_figures`: Whether matplotlib figures were captured
- `stdout_length`: Length of captured stdout
- `execution_time_ms`: How long execution took (useful for diagnosing timeouts)

### 3. File upload and parsing

Dataset uploads involve format detection, encoding guessing, and pandas parsing — all of which can fail in surprising ways.

**Metadata to capture:**
- `filename`: Original filename
- `file_size_bytes`: Size of the uploaded file
- `detected_format`: csv, xlsx, etc.
- `resulting_shape`: (rows, cols) of the parsed DataFrame
- `column_types_summary`: e.g. "5 numeric, 3 object, 1 datetime"

### 4. Data cleaning operations

Cleaning steps transform the working dataframe. Capture what changed so the troubleshooter can trace data-related errors back to a cleaning step.

**Metadata to capture:**
- `cleaning_type`: e.g. "drop_column", "fill_missing", "convert_type"
- `target_columns`: Which columns were affected
- `rows_before`: Row count before
- `rows_after`: Row count after
- `shape_changed`: Whether the dataframe shape changed

---

## The Troubleshooter Agent

### When it activates

The troubleshooter activates when an error reaches a **user-facing boundary** — an API endpoint that would return an error response to the frontend. It does NOT activate for:

- Handled retries (e.g., LLM call fails, retry succeeds — no need to diagnose)
- Validation errors with obvious causes (e.g., "no file uploaded" — the user knows what happened)
- Expected edge cases already covered by explicit error messages

**Activation points:**
- `main.py` route handlers, in the `except` block before returning an error response
- After LLM response parsing fails and exhausts retries
- After sandboxed code execution fails with an error the user can't self-diagnose

### What it receives

The troubleshooter gets a **diagnosis request** containing:

1. **The error** — Exception type, message, and the immediate traceback (just the relevant frames, not the full stack)
2. **The context buffer** — The last N entries from the session's context buffer
3. **The current operation** — What the system was trying to do when the error occurred
4. **Session summary** — Dataset shape, column names, conversation turn count (NOT the full conversation history or dataframe contents)

```python
@dataclass
class DiagnosisRequest:
    error_type: str
    error_message: str
    traceback_summary: str          # Last 3-5 frames, not full trace
    context_buffer: list[dict]      # Recent ContextEntry dicts
    current_operation: str          # What was being attempted
    session_summary: dict           # Shape, columns, turn count
```

### What it produces

The troubleshooter returns a **diagnosis** — a plain-English explanation aimed at the developer (not the end user). The user-facing error message remains simple ("Something went wrong, try rephrasing your question"). The diagnosis goes to the developer console / server logs for the current session only.

```python
@dataclass
class Diagnosis:
    summary: str            # One sentence: what went wrong
    likely_cause: str       # One paragraph: why it probably happened
    evidence: list[str]     # Which context buffer entries support the diagnosis
    suggested_action: str   # What to try: retry, rephrase, check data, file a bug
```

**Example diagnosis:**
```
summary: "LLM returned Python code that references a column 'revenue' which doesn't exist in the dataset."

likely_cause: "The user asked 'show me revenue trends' but the dataset has no 'revenue' column.
The LLM inferred a column name from the question instead of using the actual column names
provided in the prompt. The previous successful operation was a correlation matrix on numeric
columns — the LLM may have lost track of the actual column names by this point in the conversation."

evidence:
  - "Context entry 18: llm_call for analysis_query — response included code referencing 'revenue'"
  - "Context entry 15: file_parse — dataset has columns: ['date', 'sales', 'region', 'units']"

suggested_action: "Retry with explicit column reference. The user likely means the 'sales' column."
```

### Implementation approach

The troubleshooter is a function in `llm.py` (following the AI Logic Isolation principle from `coding_principles.md`). It makes a single LLM call with a diagnostic prompt. It is NOT a long-running agent, autonomous loop, or separate service. One error → one call → one diagnosis.

Keep the diagnostic prompt simple and stable. It receives the structured `DiagnosisRequest` and returns the structured `Diagnosis`. No tool use, no multi-turn reasoning — just pattern matching on the context buffer.

---

## What NOT to Build

This is a prototype. The following are explicitly out of scope:

| Don't build | Why not |
|------------|---------|
| Persistent log storage | Sessions are ephemeral. Logs die with the session. YAGNI. |
| Log aggregation / search (ELK, Loki, etc.) | No multi-user, no production deployment. |
| Metrics / dashboards (Prometheus, Grafana, etc.) | Single-user prototype. Look at the console. |
| Distributed tracing (OpenTelemetry, Jaeger, etc.) | Monolith. No services to trace between. |
| Alerting / paging | One user, running locally. They'll see the error. |
| Automatic retry/recovery orchestration | The troubleshooter diagnoses — it doesn't fix. Autonomic recovery is a different (and dangerous) concern. |
| Background health checks | The user is the health check. If it's broken, they'll tell you. |

If the prototype graduates to production, revisit these decisions. Until then, they are YAGNI.

---

## Integration With Existing Harness

### Relationship to TEST-STRATEGY.md

Tests verify behavior is correct before deployment. The troubleshooter diagnoses behavior that's incorrect at runtime — typically caused by non-deterministic LLM outputs or unexpected user data that tests couldn't anticipate.

They are complementary:
- A test catches "LLM JSON parsing breaks when code fences are present" → fix the parser.
- The troubleshooter catches "LLM referenced a column that doesn't exist in this specific user's dataset" → diagnose and suggest a retry.

### Relationship to code_review_patterns.md §16 (Logging)

The logging review patterns still apply — when you DO log (e.g., the troubleshooter's diagnosis output), it should be structured and contextual per §16. The difference is that most runtime context now flows through the buffer and troubleshooter rather than through scattered `logger.info()` calls.

**Keep standard `logger.error()` and `logger.warning()` calls** for:
- Application startup/shutdown events
- Configuration issues (missing env vars, invalid settings)
- Unrecoverable errors that crash the process

**Route through the context buffer** instead of logging for:
- Per-request operation tracking
- LLM call metadata
- Execution results
- Data transformation steps

### Execution plan integration

When implementing features that touch system boundaries (LLM calls, execution, file I/O), Phase C (Implementation) should include adding context buffer entries at those boundaries. Phase E (Code Review) should verify:

1. Every new LLM call has a context entry with the metadata fields listed above
2. Every new error handler at a user-facing boundary triggers the troubleshooter
3. No full payloads (prompts, responses, dataframes) are stored in the buffer — summaries only
