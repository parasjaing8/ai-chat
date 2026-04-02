# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **multi-agent AI orchestration platform** — a group chat + automated project builder running as a FastAPI server on a headless Mac Mini (192.168.0.130:8080). Users interact via browser; AI agents (Claude, DeepSeek, Qwen) collaborate to plan and build web projects automatically.

The backend is designed to be **fully flexible**: Claude can be toggled offline at any time, with Qwen taking over as the master orchestrator. Any LLM can act as planner, evaluator, or worker depending on availability and task type.

## Running the Server

**On the Mac Mini (remote):**
```bash
# Start (background)
cd /Users/parasjain/ai-chat
nohup /Users/parasjain/ai-chat/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080 > /tmp/ai-chat.log 2>&1 &

# Kill existing
kill $(ps aux | grep 'uvicorn server:app' | grep -v grep | awk '{print $2}') 2>/dev/null
```

**Local dev:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add ANTHROPIC_API_KEY
uvicorn server:app --host 0.0.0.0 --port 8080
```

No test suite. No lint configuration. There is no build step.

## Architecture

### Single-file backend: `server.py`

All logic lives in `server.py` (~2400 lines). Key sections in order:

| Area | What it does |
|------|-------------|
| **Config + `_config`** | Constants, `OLLAMA_MODELS`, `SERVER_HOST`, runtime config dict |
| **`get_master_model()` / `is_claude_available()`** | Master model routing helpers — central control point |
| **`OrchStats`** | Token tracking + elapsed time across one orchestration run |
| **Memory system** | `read_universal_lessons()`, `extract_and_save_lesson()` — lesson extraction via master model |
| **DB layer** | Raw `sqlite3` — tables: `messages`, `projects`, `project_messages`, `tasks` |
| **Project / Task management** | CRUD helpers, `create_project()`, `slugify()` |
| **File I/O** | `extract_files_from_response()` (4 parsing patterns), `write_project_files()` |
| **Git / Devlog** | `git_commit()`, `append_devlog()` |
| **Streaming** | `stream_claude()`, `stream_ollama()`, **`stream_master()`** (routes to active master) |
| **`_ollama_json_call()`** | Non-streaming Ollama call for structured JSON output |
| **`_master_json_call()`** | Routes structured calls to Claude or Qwen depending on `_config` |
| **Skills system** | `SKILLS_DIR`, `_load_skills(context)` — injects skill content into system prompts |
| **Intent detection** | `_claude_classify()` → `_master_json_call()`, `detect_intent()` |
| **Structured planning** | `claude_plan_project()`, `claude_evaluate_task()`, `claude_project_summary()` — all via master model |
| **Orchestration** | `run_orchestration()`, `_execute_task()`, `run_test_phase()`, `run_fix_task()` |
| **FastAPI routes** | REST endpoints including `/settings/master`, `/skills` |
| **WebSocket** | `/ws` — single entry point for all real-time client communication |

### Master Model System

`_config` dict (mutable at runtime via API) controls which model orchestrates:

```python
_config = {
    "master_model":   "claude",  # "claude" | "qwen"
    "claude_enabled": True,      # False = force offline without removing key
}
```

- `get_master_model()` — returns `"qwen"` if Claude disabled or no API key, else `_config["master_model"]`
- `is_claude_available()` — True only when enabled + key present
- `_master_json_call(system, prompt)` — routes to Claude API or Ollama based on master
- `stream_master(history, system_prompt)` — streams from master model
- Toggle via `POST /settings/master` with `{"model": "qwen", "claude_enabled": false}`

### Skills System

`skills/*.md` files inject domain knowledge into worker system prompts when task keywords match.

**Skill file format** (`skills/my-skill.md`):
```markdown
---
name: My Skill
description: One-line description
keywords: keyword1, keyword2, keyword3
---

## Skill content injected into system prompt...
```

Available skills: `ssh-operations`, `web-development`, `game-development`, `api-development`, `database`, `debugging`, `system-admin`, `data-visualization`, `mobile-responsive`, `performance-optimization`, `python-scripting`

Skills are loaded by `_load_skills(task_context)` and appended to `_build_worker_system()`. New skills are picked up automatically — no code changes needed.

### AI Model Routing

| Model | Default Role | When Used |
|-------|-------------|-----------|
| Claude (`claude-sonnet-4-6`) | Master orchestrator | Planning, eval, review, classification — when `claude_enabled=True` and key present |
| Qwen (`qwen3.5:9b` via Ollama) | Backup master + worker | All orchestration when Claude offline; reasoning/analysis tasks |
| DeepSeek (`deepseek-coder-v2` via Ollama) | Coding worker | File implementation tasks |

### Data Storage

```
chat.db                          — SQLite: chat history, projects, tasks
projects/<slug>/src/             — Generated project source files (served via /play/<slug>/)
projects/<slug>/devlog.md        — Per-project build log
projects/<slug>/.git/            — Auto-initialized git repo per project
memory/universal_lessons.md      — Lessons extracted across all projects
memory/<slug>/lessons.md         — Per-project lessons from bug-fix runs
skills/*.md                      — Skill knowledge base files
```

### Orchestration Flow

1. **Planning** — `claude_plan_project()` via master model → JSON task list (up to 100 tasks)
2. **Execution** — `_execute_task()` per task; skills auto-injected; files extracted + git committed
3. **Evaluation** — `claude_evaluate_task()` via master model; one retry if rejected
4. **Testing** — `run_test_phase()` via master model; auto-fix if bugs found
5. **Completion** — `claude_project_summary()` via master model; final git commit

## Key Constraints (from `AGENT_RULES.md`)

- **Port 8080 reserved** — never start project servers on any port; projects served via `/play/<slug>/`
- **Relative asset paths only** — `src="js/game.js"` not `src="/js/game.js"`
- **No local file access** — Mac Mini is headless; no Finder/Explorer/GUI references
- **`index.html` is the entry point** for every web project
- **Complete code only** — no stubs, TODOs, or placeholder comments
- **Canvas/game projects must include touch controls** (`@media (pointer: coarse)`)

## REST API Quick Reference

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/settings/master` | Get current master model config |
| POST | `/settings/master` | Toggle master model: `{"model":"qwen","claude_enabled":false}` |
| GET | `/skills` | List all available skill files |
| POST | `/settings/apikey` | Update Claude API key |
| GET | `/status` | Claude API connectivity check |

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Required for Claude — without it, Qwen becomes master automatically |
| `SERVER_HOST` | Optional — defaults to `192.168.0.130:8080` |

## Deployment Context

- **Target machine**: Mac Mini, Apple Silicon, 16GB RAM, macOS, headless
- **Access**: SSH from Windows laptop at 192.168.0.67 or browser at http://192.168.0.130:8080
- **Code sync**: Claude Code on Windows Dropbox syncs to Mac Mini via SCP (see `.claude/settings.local.json`)
- **Ollama**: Must be running on Mac Mini for DeepSeek/Qwen; server degrades gracefully if down
