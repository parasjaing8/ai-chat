# AI-Chat Platform Audit — 4 April 2026

**Auditor**: Claude Opus 4.6  
**Scope**: Full codebase — `server.py` (~2400 lines), `static/index.html` (~2400 lines), `CLAUDE.md`, all skill files, all kb files  
**Total findings**: 37 across 10 categories

---

## Context

This audit is based on issues discovered and fixed during development sessions up to 4 April 2026. The following are already fixed and excluded from findings:
- First user message not shown in project chat
- `check_claude_online()` ignoring `claude_enabled` flag
- `detect_intent_in_project` returning "chat" for Qwen failures
- `_master_json_call` no fallback to Qwen when Claude fails
- Ollama error strings not detected in task output
- Task bar chips missing after WS reconnect
- `orch_complete` missing slug field
- Qwen assigning code tasks to itself
- `_ollama_json_call` context window too small
- Bare \`\`\`html blocks not extracted (fallback added)
- Worker system prompt FILE: marker enforcement

---

## 1. Remaining Bugs and Fragile Code Paths

### 1.1 SQL Column Name Injection in `update_task`
- **Severity**: CRITICAL
- **Location**: `server.py:496–506`
- `update_task()` builds SQL with f-string interpolation of kwarg *key names*: `f"UPDATE tasks SET {', '.join(sets)} WHERE id=?"`. Values are parameterised but column names are not. Safe today because callers use hardcoded strings, but one future route passing user-supplied fields would be exploitable.
- **Fix**: Whitelist allowed columns: `ALLOWED_COLS = {"status", "output_result", "completed_at"}` — reject any key outside it.

### 1.2 `check_claude_online()` Makes a Billable API Call on Every WS Connect
- **Severity**: HIGH
- **Location**: `server.py:762–786`, called at line 2184
- Every client connect (including the forced reconnect on `switchToChat()`) fires a real `POST /v1/messages` with `max_tokens=1`. With a 3-second reconnect timer this compounds quickly.
- **Fix**: Cache the result with a 30-second TTL. Never call the API if `claude_enabled=False` (partially fixed — still called at startup ping).

### 1.3 `switchToChat()` Destroys and Recreates the WebSocket
- **Severity**: MEDIUM
- **Location**: `static/index.html:1322–1326`
- `ws.close(); connect();` on every chat switch drops in-flight streaming, triggers a billing ping, and causes visible history flicker.
- **Fix**: Don't close the WS — just re-render cached chat history client-side. Send a context-switch message to the server if needed.

### 1.4 `prior_tasks_quick` Potentially Referenced Before Assignment
- **Severity**: MEDIUM
- **Location**: `server.py:2269–2303`
- `prior_tasks_quick` is assigned inside `if proj:` but referenced in `if routing == "build":`. Currently safe because `routing="chat"` when `proj` is falsy, but fragile — a refactor could introduce a `NameError`.
- **Fix**: Initialise `prior_tasks_quick = []` before the `if proj:` block.

### 1.5 `stream_ollama()` Has No `num_ctx` Option
- **Severity**: MEDIUM
- **Location**: `server.py:861–921`
- `_ollama_json_call` uses `"num_ctx": 8192` but `stream_ollama()` sends no context limit. For worker tasks injecting 6000+ chars of file context, the default Ollama context window can silently truncate input, producing incomplete code.
- **Fix**: Add `"options": {"num_ctx": 8192}` to the `stream_ollama` request JSON.

### 1.6 `extract_and_save_lesson` Uses JSON Call for Free-Form Text
- **Severity**: LOW
- **Location**: `server.py:215–239`
- `_master_json_call()` is named and documented for structured JSON output, but `extract_and_save_lesson` uses it to get a plain English lesson string. The `/no_think` prefix and response parsing are wrong for this use case.
- **Fix**: Create `_master_text_call()` for non-JSON content, or at minimum document the dual use.

### 1.7 `_config` Mutated Without Async Lock
- **Severity**: MEDIUM
- **Location**: `server.py:69–73`, mutated at lines 1959–1964, 2097–2109
- The global `_config` dict is read by concurrent async WebSocket handlers and mutated by settings endpoints. A toggle mid-orchestration can cause inconsistent routing (e.g., master switches from Claude to Qwen during task evaluation).
- **Fix**: Use `asyncio.Lock` around config reads/writes, or only apply config changes to new orchestration runs.

### 1.8 `datetime.utcnow()` Deprecated
- **Severity**: LOW
- **Location**: Throughout `server.py` (lines 205, 293, 318, 391, etc.)
- Deprecated in Python 3.12; returns a naive datetime with no timezone info.
- **Fix**: Replace with `datetime.now(timezone.utc)`.

---

## 2. Architecture Issues

### 2.1 Single 2400-Line File for All Logic
- **Severity**: HIGH
- **Location**: `server.py`
- DB operations, AI routing, orchestration, file I/O, git, memory, skills, streaming, intent detection, and routes all in one file. Testing, refactoring, and onboarding are all impeded.
- **Fix**: Split into modules: `db.py`, `models.py`, `orchestration.py`, `skills.py`, `files.py`, `routes.py`, `app.py`.

### 2.2 Zero Test Coverage
- **Severity**: HIGH
- Pure functions like `extract_files_from_response()`, `slugify()`, `build_claude_messages()`, `parse_mentions()` are testable in isolation but have no tests. The extraction logic has had multiple bugs — tests would have caught them.
- **Fix**: Start with `pytest` unit tests for pure functions. Add httpx-based route tests. No external dependencies required for the first batch.

### 2.3 Synchronous SQLite in Async Handlers
- **Severity**: MEDIUM
- Every DB call uses synchronous `sqlite3.connect()` inside async FastAPI/WebSocket handlers. Under concurrent connections, SQLite file locks can block the event loop.
- **Fix**: Use `aiosqlite` or wrap calls in `asyncio.to_thread()`.

### 2.4 Monolithic Frontend (2400 lines in one HTML file)
- **Severity**: LOW
- CSS, HTML, and JS all in `index.html`. Makes UI iteration slow and error-prone.
- **Fix**: Extract `app.js` and `app.css` as separate files. No build step needed.

---

## 3. Performance Issues

### 3.1 Stats Polling Every 2 Seconds Per Client
- **Severity**: MEDIUM
- **Location**: `static/index.html:2392`
- Every connected browser tab fires `GET /stats` every 2 seconds indefinitely. Should use the existing WebSocket instead.
- **Fix**: Push stats over the WebSocket, or increase the interval to 10 seconds.

### 3.2 `read_project_files()` Reads All Files on Every Task
- **Severity**: MEDIUM
- **Location**: `server.py:631–644`, called in `_execute_task`
- Reads full content of every file in `src/` for every single task. For a 20-file project this is wasteful.
- **Fix**: Read only the file listing first, then selectively read relevant files.

### 3.3 Ollama `keep_alive` Too Short (2 Minutes)
- **Severity**: MEDIUM
- **Location**: `server.py:38` (`KEEP_ALIVE = "2m"`)
- After 2 minutes of inactivity Ollama unloads the model. Reloading DeepSeek 16B takes 10–15 seconds. During multi-step orchestration with planning gaps, this causes noticeable stalls between tasks.
- **Fix**: Increase to `"10m"` or `"30m"`. Consider warm-pinging the active model between tasks.

### 3.4 Markdown Re-rendered on Every Streaming Chunk
- **Severity**: MEDIUM
- **Location**: `static/index.html:2029–2031`
- `appendChunk()` calls `renderMd()` (which runs `marked.parse()` + `hljs.highlightElement()`) on the full accumulated text for every single token. For a 500-token response this means ~200 full markdown parses.
- **Fix**: Debounce renders to at most once every 100ms during streaming. Do a final full render on `finalise()`.

### 3.5 File Context Budget Split Naively
- **Severity**: LOW
- **Location**: `server.py:1449–1457`
- The 6000-char file context budget is split evenly across relevant files. If 6 files match, each gets only 1000 chars — which may truncate the most important file (`index.html`) while small files get their full content.
- **Fix**: Prioritise `index.html` and `files_to_create` entries with larger shares of the budget.

---

## 4. UX Gaps

### 4.1 No Progress Indicator During Planning
- **Severity**: MEDIUM
- During `claude_plan_project()` the user sees a single "planning..." message for up to 60 seconds (Qwen) with no progress. No spinner, no elapsed timer, no preview.
- **Fix**: Show elapsed time during planning phase, or stream plan tokens as they arrive.

### 4.2 No In-App File Viewer/Editor
- **Severity**: MEDIUM
- Generated files are only accessible via the "Open Project" link or raw file paths. Users cannot view or fix a small typo without triggering a full AI fix cycle.
- **Fix**: Add a simple file tree + read-only code viewer. Optionally a minimal inline editor.

### 4.3 No Build-Completion Notification When Tab Not Focused
- **Severity**: LOW
- Builds take several minutes. If the user switches tabs, there is no browser notification, title flash, or sound on completion.
- **Fix**: Use the browser Notification API or flash the document title when `orch_complete` arrives while `document.hidden` is true.

### 4.4 Hardcoded Speed/Size Stats in Settings Modal
- **Severity**: LOW
- **Location**: `server.py:1929–1930`
- DeepSeek and Qwen stats ("40.9 tok/s", "11.1 GB") are hardcoded strings, not live values from Ollama.
- **Fix**: Query Ollama's `/api/show` endpoint to get actual metadata dynamically.

---

## 5. Agent Coordination Gaps

### 5.1 All Tasks Execute Sequentially — No Parallelism
- **Severity**: HIGH
- **Location**: `server.py:1340` (`for task in saved_tasks:`)
- Independent tasks (separate JS files that don't reference each other) run one-at-a-time even though DeepSeek and Qwen are separate Ollama slots. Build times could be cut in half with parallelism.
- **Fix**: Add `depends_on` field to planning. Execute tasks with no dependencies concurrently via `asyncio.gather()`. Requires careful UI handling for simultaneous streaming agents.

### 5.2 Evaluation Is Auto-Approving
- **Severity**: MEDIUM
- **Location**: `server.py:1232–1253`
- `claude_evaluate_task()` says "Be lenient — approve if code is reasonable" and returns `{"approved": True}` on any parse failure or empty response. It almost never rejects output.
- **Fix**: Add structural checks in Python before calling the LLM: verify expected files were produced, check that FILE: markers were present, verify `<html>` tag in HTML files. Fail-closed, not fail-open.

### 5.3 Retry Output Is Never Evaluated
- **Severity**: MEDIUM
- **Location**: `server.py:1532–1572`
- When a task fails evaluation and retries, the retry output is always accepted as `done` — it is never evaluated again.
- **Fix**: Evaluate the retry output too. If it also fails, mark the task `errored` so the user knows.

### 5.4 Test Phase Does Not Re-verify After Fixing
- **Severity**: MEDIUM
- **Location**: `server.py:1611–1702`
- After `run_test_phase()` finds bugs and applies fixes, it does not run a second review pass. The fix could introduce new bugs.
- **Fix**: After applying fixes, re-run the review (cap at 2 iterations).

### 5.5 `run_fix_task` Does Not Run Test Phase
- **Severity**: MEDIUM
- **Location**: `server.py:1705–1771`
- The kb docs claim `run_test_phase()` runs after user-triggered fixes. The actual code does not call it.
- **Fix**: Add `await run_test_phase(ws, project, cancel_event=cancel_event)` after writing fixed files.

### 5.6 Skills Keyword Matching Uses Substring — Too Broad
- **Severity**: LOW
- **Location**: `server.py:1020`
- Keyword `"api"` matches "capitalize", "capital". Keyword `"css"` matches "accessing". Simple `in` check causes unintended skill activations.
- **Fix**: Use word-boundary regex: `re.search(r'\b' + re.escape(kw) + r'\b', ctx_lower)`.

---

## 6. Error Handling Gaps

### 6.1 WebSocket Sends After Client Disconnect Not Guarded
- **Severity**: HIGH
- Throughout `run_orchestration`, `_execute_task`, `run_test_phase`, `run_fix_task`
- If the client disconnects mid-orchestration, `ws.send_json()` raises `WebSocketDisconnect`. The outer catch handles the WS connection but mid-task exceptions leave tasks stuck in `in_progress` state.
- **Fix**: Wrap `ws.send_json()` in a helper that catches disconnect and sets the cancel event for clean shutdown.

### 6.2 `_master_json_call` Returns Empty String on All Failures — Callers Mishandle It
- **Severity**: MEDIUM
- **Location**: `server.py:958–983`
- When both Claude and Qwen fail, `""` is returned. `detect_intent` parses `""` as JSON error → `{"type":"chat"}`. `claude_evaluate_task` returns `{"approved": True}` on empty — auto-approves failed evaluations.
- **Fix**: Return `None` on failure. Callers must explicitly handle `None` vs. empty-but-valid response.

### 6.3 LLM Failures Invisible to the User
- **Severity**: MEDIUM
- When Ollama is down, tasks silently complete with no files. No `orch_phase` error message is sent to the client.
- **Fix**: Send a warning `orch_phase` message to the client whenever an LLM call returns empty/fails.

### 6.4 `_auto_generate_index` Writes File Inside a GET Handler
- **Severity**: MEDIUM
- **Location**: `server.py:1891`
- `serve_project()` GET handler calls `_auto_generate_index()` which writes to disk. Two simultaneous requests for a missing `index.html` create a race condition.
- **Fix**: Return the generated HTML directly without writing to disk, or generate only during project creation.

### 6.5 Git Errors Silently Swallowed
- **Severity**: LOW
- **Location**: `server.py:648–666`
- `git_init` and `git_commit` catch all exceptions and only log warnings. Git-not-found or disk-full failures are invisible.
- **Fix**: Surface git failures in the devlog. Report critical failures (git not found) at server startup.

---

## 7. Missing Features vs. Original Vision

### 7.1 No SSH / Hardware Execution Capability
- **Severity**: HIGH
- The skill file `ssh-operations.md` exists but there is no actual SSH execution in the server. The vision of a master LLM that can manage the Mac Mini (restart Ollama, check logs, deploy) is entirely unimplemented.
- **Fix**: Add `async def execute_ssh(cmd, timeout=30)` sandboxed with a whitelist of safe operations. Expose as a tool the master model can call.

### 7.2 No Dynamic LLM Registry
- **Severity**: MEDIUM
- **Location**: `server.py:53–56`
- Exactly 3 agents are hardcoded in `OLLAMA_MODELS`. Adding Llama 3, Codestral, or any other local model requires editing `server.py`. The "+" button in the UI shows "Coming in v2."
- **Fix**: Make `OLLAMA_MODELS` runtime-configurable. Add a UI that reads available models from Ollama's `/api/tags` and lets the user enable/disable them.

### 7.3 No Skill Management UI
- **Severity**: LOW
- `GET /skills` endpoint exists but the UI never calls it. Skills can only be managed by editing files on disk via SSH.
- **Fix**: Add a skills panel in the UI showing active skills, activation logs per task, and an option to create/edit skills.

---

## 8. Security Issues

### 8.1 API Key Writable via Unauthenticated HTTP
- **Severity**: HIGH
- **Location**: `server.py:1968–1976`
- `POST /settings/apikey` writes the Anthropic API key to disk in plaintext. Any device on the LAN can overwrite or read (via the preview in `GET /settings`) the key. No authentication on any endpoint.
- **Fix**: Add basic authentication (shared secret header) to all sensitive endpoints. At minimum restrict `/settings/apikey` and `DELETE /projects/{id}` to localhost.

### 8.2 `DELETE /projects/{id}` Unauthenticated — Calls `shutil.rmtree`
- **Severity**: MEDIUM
- **Location**: `server.py:2057–2075`
- Any HTTP client on the network can permanently delete any project and its files.
- **Fix**: Require authentication, or add a confirmation token in the request body.

### 8.3 No CORS Configuration (Documentation Wrong)
- **Severity**: LOW
- The `api-development.md` skill file claims "All routes already handle CORS via the existing FastAPI setup" — this is false. No `CORSMiddleware` is configured.
- **Fix**: Add `CORSMiddleware` restricted to LAN origins, or correct the skill documentation.

### 8.4 Markdown Sanitiser Incomplete (Custom Implementation)
- **Severity**: LOW
- **Location**: `static/index.html:1245–1253`
- Custom `sanitizeHtml()` blocks `<script>`, `<iframe>`, etc. and `on*` attributes, but may miss edge cases like `<a href="javascript:...">`. AI-generated content makes this a low risk, but prompt injection via crafted messages could exploit gaps.
- **Fix**: Replace with DOMPurify (already a CDN away).

---

## 9. Deployment / Ops Gaps

### 9.1 No Lightweight Health Check Endpoint
- **Severity**: HIGH
- `GET /status` makes a billable Claude API call. There is no side-effect-free health check for monitoring.
- **Fix**: Add `GET /health` returning `{"ok": true, "uptime_s": ..., "db": "ok"}` with no external calls.

### 9.2 No Ollama Auto-Start or Monitoring
- **Severity**: MEDIUM
- `start.sh` starts only uvicorn. If Ollama is not running, all local model calls fail silently. No startup check, no periodic health check, no auto-restart.
- **Fix**: Add Ollama health check at startup. Add a periodic background task (`asyncio` background coroutine) that pings `http://localhost:11434/api/tags` and updates the UI status dots for DeepSeek/Qwen.

### 9.3 No Log Rotation or Structured Logging
- **Severity**: MEDIUM
- Logs go to `nohup > /tmp/ai-chat.log` with no rotation. The file grows indefinitely.
- **Fix**: Add `RotatingFileHandler` (max 10MB, keep 3 files). Consider JSON logging for analysis.

### 9.4 No Process Manager / Crash Recovery
- **Severity**: MEDIUM
- Started with `nohup`. No auto-restart on crash. OOM or unhandled exception = server stays down.
- **Fix**: Use `launchd` plist on macOS for auto-restart on crash, or `pm2`/`supervisord`.

### 9.5 No Database Migrations
- **Severity**: LOW
- `init_db()` uses `CREATE TABLE IF NOT EXISTS` — handles initial creation but not schema changes. Adding a column requires manual SQL.
- **Fix**: Add a `schema_version` table and a list of sequential migration functions.

### 9.6 No Backup for `chat.db` or `memory/`
- **Severity**: LOW
- All user data is excluded from git and exists only on the Mac Mini's disk with no backup.
- **Fix**: Add a cron job that copies `chat.db` and `memory/` to a backup path daily.

---

## 10. Documentation Drift

| File | Issue |
|------|-------|
| `kb/orchestration-flow.md:52` | Claims `run_fix_task` calls `run_test_phase` — it does not |
| `kb/architecture.md:83–84` | Lists file extraction pattern 3 as `\`\`\`html:path` — not implemented; actual patterns differ |
| `skills/web-development.md`, `skills/ssh-operations.md` | Hardcode `192.168.0.130:8080` instead of using `SERVER_HOST` — will break if server IP changes |
| `skills/api-development.md` | Claims CORS is already configured — it is not |

---

## Priority Fix Order

### Do immediately
1. **`prior_tasks_quick` init before branch** — 2-line fix, prevents potential NameError
2. **`stream_ollama` add `num_ctx: 8192`** — directly causes code truncation in multi-file projects
3. **Evaluation fail-closed** — tasks should fail loud, not silently succeed
4. **`run_fix_task` call `run_test_phase`** — the kb says it happens; make it true
5. **Skills word-boundary matching** — broad substring matching injects wrong skills

### Next sprint
6. Health endpoint (`/health`)
7. Ollama startup health check + background monitor
8. `keep_alive` increase (2m → 10m)
9. Markdown render debounce
10. WebSocket send-after-disconnect guard
11. Cache `check_claude_online` result (TTL 30s)

### Longer term (architecture)
12. Module split (server.py → multiple files)
13. Unit tests for pure functions
14. Dynamic LLM registry
15. SSH execution capability
16. Parallel task execution
17. In-app file viewer
18. Process manager (launchd)
19. Database migrations

---

*Generated by Claude Opus 4.6 · ai-chat audit · 4 April 2026*
