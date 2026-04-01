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

## Request Flow

```
Browser <──WebSocket──> /ws handler in server.py
                             │
                    Intent detection
                    ┌──────┴──────────┐
                 General chat     Project build
                    │                  │
           call_claude / call_ollama   run_orchestration()
                                       │
                               plan → execute → evaluate → test → complete
```

## Database Schema

```sql
messages (id, role, agent, content, timestamp)
projects (id, slug, name, description, status, created_at, updated_at, task_count, completed_tasks)
project_messages (id, project_id, role, agent, content, timestamp)
tasks (id, project_id, task_number, title, description, status, assigned_to, result, created_at, updated_at)
```

## File Extraction

`extract_files_from_response()` parses agent output using 4 patterns:
1. `FILE: path\n```lang\n...\n````
2. `## path\n```lang\n...\n````
3. Named code fences ` ```html:path `
4. Bare ` ```html ` blocks (assigned to `index.html`)

Path sanitization prevents directory traversal. Files are written to `projects/<slug>/src/`.

## Memory System

- `memory/universal_lessons.md` — cross-project learnings, appended after each fix
- `memory/<slug>/lessons.md` — project-specific lessons
- Claude extracts lessons automatically from fix-task completions

## Project Git Lifecycle

```
create_project()  → git init
per task          → git add -A && git commit -m "Task N: <title>"
fix applied       → git commit -m "fix: <feedback[:60]>"
completion        → git commit -m "docs: project complete"
```
