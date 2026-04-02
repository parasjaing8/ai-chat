# Server Reference

## HTTP Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Chat UI (serves `static/index.html`) |
| GET | `/play/{slug}/` | Serve project `src/index.html` |
| GET | `/play/{slug}/{path}` | Serve project asset with correct MIME type |
| GET | `/config` | Returns `{ server_host, models }` |
| GET | `/status` | Claude API connectivity check |
| GET | `/settings` | Ollama model availability + API key status |
| POST | `/settings/apikey` | Update `ANTHROPIC_API_KEY` in `.env` |
| **GET** | **`/settings/master`** | **Get master model config** |
| **POST** | **`/settings/master`** | **Toggle master model / claude_enabled** |
| **GET** | **`/skills`** | **List all skill files with keywords** |
| GET | `/projects` | List all projects |
| POST | `/projects` | Create new project |
| GET | `/projects/{id}` | Get project details + task list |
| DELETE | `/projects/{id}` | Delete project + source files |
| WebSocket | `/ws` | All real-time chat and orchestration |

### POST /settings/master
```json
// Body (either field is optional)
{ "model": "claude" | "qwen", "claude_enabled": true | false }

// Response
{ "master_model": "qwen", "claude_enabled": false, "effective_master": "qwen" }
```

## WebSocket Message Protocol

### Client → Server
```json
{ "type": "chat", "content": "...", "project_id": null }
{ "type": "fix",  "project_id": 123, "feedback": "..." }
{ "type": "start_orchestration", "project_id": 1, "goal": "..." }
{ "type": "resume_orchestration", "project_id": 1 }
{ "type": "fix_project", "project_id": 1, "feedback": "..." }
{ "type": "load_project", "project_id": 1 }
{ "type": "get_projects" }
{ "type": "cancel" }
```

### Server → Client
```json
{ "type": "chunk",       "agent": "claude|qwen|deepseek", "content": "..." }
{ "type": "done",        "agent": "claude" }
{ "type": "typing",      "agent": "qwen" }
{ "type": "status",      "claude_online": true }
{ "type": "orch_phase",  "phase": "planning|testing|fixing|completed", "msg": "..." }
{ "type": "orch_plan",   "tasks": [...] }
{ "type": "orch_task_start", "task_id": 1, "title": "...", "assigned_to": "deepseek" }
{ "type": "orch_task_done",  "task_id": 1, "files": ["index.html"] }
{ "type": "orch_file",   "path": "js/game.js" }
{ "type": "orch_complete","summary": "..." }
{ "type": "orch_stats",  "elapsed": "1m 23s", "cost_usd": 0.012, ... }
{ "type": "fix_complete","files_fixed": [...], "project_slug": "...", "lesson": "..." }
{ "type": "intent_detected", "intent": "project_new", "name": "...", "original_message": "..." }
{ "type": "cancelled" }
{ "type": "error",       "message": "..." }
```

## Key Constants (`server.py` top section)

```python
OLLAMA_BASE   = "http://localhost:11434"
DB_PATH       = Path(__file__).parent / "chat.db"
PROJECTS_DIR  = Path(__file__).parent / "projects"
MEMORY_DIR    = Path(__file__).parent / "memory"
SKILLS_DIR    = Path(__file__).parent / "skills"
CONTEXT_LEN   = 20   # messages in context window for AI calls
DISPLAY_LEN   = 60   # messages loaded on UI open
SERVER_HOST   = os.getenv("SERVER_HOST", "192.168.0.130:8080")

OLLAMA_MODELS = {
    "deepseek": "deepseek-coder-v2:16b-lite-instruct-q5_K_S",
    "qwen":     "qwen3.5:9b",
}

_config = {
    "master_model":   "claude",   # toggle to "qwen" to go offline
    "claude_enabled": True,       # set False to force Qwen without removing key
}
```

## Core Routing Functions

```python
get_master_model() -> str           # "claude" or "qwen"
is_claude_available() -> bool       # True if enabled + key present

# Structured JSON call (planning, eval, classify)
await _master_json_call(system, prompt, max_tokens) -> str

# Streaming response
async for chunk in stream_master(history, system_prompt, cancel_event, usage):
    ...

# Non-streaming Ollama call
await _ollama_json_call(agent, system, prompt, max_tokens) -> str

# Skills injection
_load_skills(context_text) -> str   # appended to system prompts
```

## AI Caller Signatures

```python
async def stream_claude(messages, system_prompt, cancel_event, usage) -> AsyncGenerator
async def stream_ollama(agent, messages, system_prompt, cancel_event, usage) -> AsyncGenerator
async def stream_master(history, system_prompt, cancel_event, usage) -> AsyncGenerator
```

All stream tokens to the WebSocket client. `usage` dict is populated with `input_tokens`/`output_tokens`.
