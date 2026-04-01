# Server Reference

## HTTP Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Chat UI (serves `static/index.html`) |
| GET | `/play/{slug}/` | Serve project `src/index.html` |
| GET | `/play/{slug}/{path}` | Serve project asset with correct MIME type |
| GET | `/config` | Returns `{ server_host, models }` |
| GET | `/status` | Claude API connectivity check |
| GET | `/settings` | Ollama model availability status |
| POST | `/settings/apikey` | Update `ANTHROPIC_API_KEY` in `.env` |
| GET | `/projects` | List all projects |
| POST | `/projects` | Create new project |
| GET | `/projects/{id}` | Get project details + task list |
| DELETE | `/projects/{id}` | Delete project + source files |
| WebSocket | `/ws` | All real-time chat and orchestration |

## WebSocket Message Protocol

### Client → Server
```json
{ "type": "chat", "agent": "claude|deepseek|qwen|auto", "message": "...", "project_id": null }
{ "type": "fix",  "project_id": 123, "feedback": "..." }
{ "type": "get_history", "limit": 60 }
{ "type": "get_projects" }
```

### Server → Client
```json
{ "type": "token",    "agent": "claude", "content": "..." }
{ "type": "message",  "agent": "claude", "content": "...", "role": "assistant" }
{ "type": "status",   "message": "Planning tasks..." }
{ "type": "task_update", "task_id": 1, "status": "in_progress", "title": "..." }
{ "type": "projects", "projects": [...] }
{ "type": "history",  "messages": [...] }
{ "type": "error",    "message": "..." }
```

## Key Constants (`server.py` top section)

```python
OLLAMA_BASE   = "http://localhost:11434"
DB_PATH       = Path(__file__).parent / "chat.db"
PROJECTS_DIR  = Path(__file__).parent / "projects"
MEMORY_DIR    = Path(__file__).parent / "memory"
CONTEXT_LEN   = 20   # messages in context window for AI calls
DISPLAY_LEN   = 60   # messages loaded on UI open
SERVER_HOST   = os.getenv("SERVER_HOST", "192.168.0.130:8080")

OLLAMA_MODELS = {
    "deepseek": "deepseek-coder-v2:16b-lite-instruct-q5_K_S",
    "qwen":     "qwen3.5:9b",
}
```

## AI Caller Signatures

```python
async def call_claude(messages, system_prompt, ws, stats, stream=True) -> str
async def call_ollama(model_key, messages, system_prompt, ws, stats, stream=True) -> str
```

Both stream tokens to the WebSocket client and return the full response string. `stats` is an `OrchStats` instance that accumulates token counts.

## OrchStats

```python
stats = OrchStats()
stats.record(agent, input_tok, output_tok)
stats.elapsed()          # "2m 15s"
stats.claude_tokens()    # (input, output) ints
stats.local_tokens()     # total Ollama tokens
stats.total_tasks()      # sum across all agents
```
