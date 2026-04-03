# Architecture Overview

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, FastAPI, Uvicorn |
| Realtime | WebSocket (single `/ws` endpoint) |
| Database | SQLite (`chat.db`) via raw `sqlite3` |
| HTTP client | `httpx` (async) |
| Frontend | Vanilla JS single-page app (`static/index.html`) |
| AI: Cloud | Anthropic API (Claude Sonnet 4.6) |
| AI: Local | Ollama (DeepSeek Coder v2, Qwen 3.5) |

## Master Model System

The "master model" handles orchestration, planning, evaluation, and classification. It defaults to Claude but can be switched to Qwen at runtime — no restart required.

```
_config = { "master_model": "claude", "claude_enabled": True }
         ↓
get_master_model()         → "claude" | "qwen"
is_claude_available()      → True | False
_master_json_call(sys, p)  → routes structured call to master
stream_master(history)     → streams from master
```

**When Qwen is master:** All planning, evaluation, code review, and intent classification use Qwen via Ollama. DeepSeek still handles coding worker tasks.

**Toggle via API:** `POST /settings/master` with `{"model":"qwen","claude_enabled":false}`

## Skills System

Skills are `.md` files in `skills/` that inject domain knowledge into worker system prompts.

```
skills/ssh-operations.md
skills/web-development.md
skills/game-development.md
skills/api-development.md
skills/database.md
skills/debugging.md
skills/system-admin.md
skills/data-visualization.md
skills/mobile-responsive.md
skills/performance-optimization.md
skills/python-scripting.md
```

Each skill file has YAML frontmatter with `keywords`. `_load_skills(task_context)` scans all skill files, matches keywords against the task description, and returns matched skill content. This is appended to `_build_worker_system()`. Add new skills by dropping a `.md` file in `skills/` — no code changes.

## Request Flow

```
Browser <──WebSocket──> /ws handler in server.py
                             │
                    Intent detection (_master_json_call)
                    ┌──────┴──────────┐
                 General chat     Project build
                    │                  │
           stream_master()         run_orchestration()
                                       │
                    Skills loaded → _build_worker_system()
                                       │
                               plan → execute → evaluate → test → complete
                               (all via _master_json_call / stream_master)
```

## Database Schema

```sql
messages (id, role, content, timestamp)
projects (id, slug, name, description, folder_path, status, created_at, updated_at)
project_messages (id, project_id, role, content, task_id, timestamp)
tasks (id, project_id, task_number, title, description, assigned_to, status, files_to_create, output_result, created_at, completed_at)
```

## File Extraction

`extract_files_from_response()` parses agent output using 4 patterns:
1. `FILE:` marker as a comment on the first line **inside** a fenced code block (e.g. `// FILE: js/game.js`)
2. `FILE:` marker on its own line **before** a fenced code block (DeepSeek style)
3. `**\`path\`**` or `### \`path\`` heading immediately before a code block
4. Bare ` ```html ` / ` ```javascript ` blocks (no FILE: marker) — mapped to `index.html` / `js/main.js`

Path sanitization prevents directory traversal. Files written to `projects/<slug>/src/`.

## Task Planning

`claude_plan_project()` calls `_master_json_call()`. Task cap is **100** (was 8). System prompt instructs:
- Simple projects: 1–3 tasks, single self-contained file
- Complex projects: use as many atomic tasks as needed (10–50+)

Each task has `assigned_to: "claude"|"deepseek"|"qwen"`. Skills are injected per task via `_load_skills(task.description)`.

## Memory System

- `memory/universal_lessons.md` — cross-project learnings, appended after each fix
- `memory/<slug>/lessons.md` — project-specific lessons
- `extract_and_save_lesson()` uses `_master_json_call()` — works with Claude or Qwen

## Project Git Lifecycle

```
create_project()  → git init
per task          → git add -A && git commit -m "Task N: <title>"
test phase fix    → git commit -m "fix: code review auto-fixes"
fix applied       → git commit -m "fix: <feedback[:60]>"
completion        → git commit -m "docs: project complete"
```
