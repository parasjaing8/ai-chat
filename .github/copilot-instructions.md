# AI-Chat Platform — Copilot Instructions

Multi-agent AI orchestration platform: group chat + automated project builder.
Backend: FastAPI on a **headless Mac Mini** at `192.168.0.130:8080`. No display, no GUI, no Finder.

## Build & Run

No test suite. No lint config. No build step.

```bash
# Remote (Mac Mini) — start
nohup /Users/parasjain/ai-chat/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080 > /tmp/ai-chat.log 2>&1 &

# Remote — kill
kill $(ps aux | grep 'uvicorn server:app' | grep -v grep | awk '{print $2}') 2>/dev/null

# Local dev
source .venv/bin/activate && uvicorn server:app --host 0.0.0.0 --port 8080
```

Files sync from Windows Dropbox → Mac Mini via SCP.

## Architecture

All backend logic lives in a **single file: `server.py`** (~2400 lines). No modules.  
Frontend lives in `static/index.html` (~2400 lines). No build step, no framework.

Key sections of `server.py` in order:
- Config + `_config` dict (runtime toggles)
- `get_master_model()` / `is_claude_available()` — model routing
- DB layer: raw `sqlite3`, tables: `messages`, `projects`, `project_messages`, `tasks`
- File I/O: `extract_files_from_response()` (4 parse patterns), `write_project_files()`
- Streaming: `stream_claude()`, `stream_ollama()`, `stream_master()`
- `_master_json_call()` — structured JSON calls routed to Claude or Qwen
- `_load_skills(context)` — injects `skills/*.md` into worker prompts
- `run_orchestration()`, `_execute_task()`, `run_test_phase()`, `run_fix_task()`
- FastAPI routes + `/ws` WebSocket (single entry for all real-time comms)

See [CLAUDE.md](../CLAUDE.md) for the full architecture reference.

## Model Routing

```python
_config = {"master_model": "claude", "claude_enabled": True}  # mutable at runtime
```

- `get_master_model()` → `"qwen"` if Claude disabled/no key, else `_config["master_model"]`
- Toggle via `POST /settings/master`
- Claude (`claude-sonnet-4-6`) = master orchestrator when available
- Qwen (`qwen3.5:9b`) = backup master + reasoning worker via Ollama
- DeepSeek (`deepseek-coder-v2`) = coding worker via Ollama

## Skills System

`skills/*.md` files auto-inject into worker prompts when task keywords match.  
New skill files are picked up automatically — no code change needed.  
Frontmatter: `name`, `description`, `keywords` (comma-separated).

See [skills-system.md](../kb/skills-system.md) for details.

## Critical Rules (from [AGENT_RULES.md](../AGENT_RULES.md))

- **Port 8080 reserved** — never start project servers on any port; all projects served via `/play/<slug>/`
- **Relative asset paths only** — `src="js/game.js"` not `src="/js/game.js"`
- **No GUI / Finder references** — Mac Mini is headless
- **`index.html` is the entry point** for every web project
- **Complete code only** — no TODOs, stubs, or placeholders
- **Touch controls required** for canvas/game projects (`@media (pointer: coarse)`)
- **Project URL format**: `http://192.168.0.130:8080/play/<project-slug>/`

## Data Storage

```
chat.db                         — SQLite: chat history, projects, tasks
projects/<slug>/src/            — Generated project source files
projects/<slug>/devlog.md       — Per-project build log
projects/<slug>/.git/           — Auto-initialized git repo
memory/universal_lessons.md     — Cross-project lessons (extracted by master model)
memory/<slug>/lessons.md        — Per-project lessons
skills/*.md                     — Skill knowledge base
```

## Known Issues

See [audit4April.md](../audit4April.md) for the full bug/architecture audit (37 findings as of 4 Apr 2026).

Critical open items:
- `update_task()` has SQL column-name injection risk — whitelist `ALLOWED_COLS` before adding new routes that accept field names
- `check_claude_online()` makes a live API call on every WebSocket connect — cache with 30s TTL
- `_config` mutated without async lock — avoid mid-orchestration config changes
- `prior_tasks_quick` referenced before guaranteed assignment (initialize to `[]` before `if proj:` block)
- `stream_ollama()` missing `num_ctx` option — can silently truncate large prompts

## Conventions

- All AI-facing structured calls use `_master_json_call()` (returns parsed JSON dict)
- Free-form text generation uses `stream_master()` or `stream_ollama()`/`stream_claude()`
- `datetime.utcnow()` is deprecated — prefer `datetime.now(timezone.utc)`
- Skills keyword matching uses bare `in` substring check — use `\b`-bounded regex for new keywords
- `KEEP_ALIVE = "2m"` at top of `server.py` (consider raising to `"10m"` for long builds)
