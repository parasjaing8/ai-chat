---
name: API Development
description: REST API design, FastAPI endpoints, and backend service patterns
keywords: api, rest, fastapi, endpoint, route, request, response, json, post, get, delete, put, patch, backend, server, middleware, cors, auth, token, webhook, openapi
---

## API Development Skill

### FastAPI Patterns (server.py is FastAPI)
```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

@app.get("/resource/{id}")
async def get_resource(id: int):
    item = get_item(id)
    if not item:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return item

@app.post("/resource")
async def create_resource(data: dict):
    name = data.get("name", "").strip()
    if not name:
        return {"error": "name required"}
    return create_item(name)
```

### Server Context
- The server runs on port **8080** (reserved — do not use for other services)
- No CORS middleware is configured — this server is LAN-only; do not add CORSMiddleware unless explicitly requested
- WebSocket endpoint at `/ws` handles all real-time communication
- Static files at `/static/`, project files at `/play/<slug>/`

### REST Endpoint Design Rules
- `GET` for reads, `POST` for creates, `DELETE` for deletes
- Always return `{"error": "message"}` with appropriate status for failures
- Use `async def` for all FastAPI route handlers
- Never block the event loop — use `await` for all I/O

### Adding a New Endpoint
Place new endpoints in server.py, following the existing pattern:
1. Add before the `# ── WebSocket` section
2. Use the existing `sqlite3.connect(DB_PATH)` pattern for DB access
3. Reuse `get_project()`, `list_projects()`, etc. for project data

### WebSocket Message Protocol
Client sends: `{"type": "...", ...fields}`
Server sends: `{"type": "chunk"|"done"|"status"|"error", ...}`
See `kb/server-reference.md` for full protocol.
