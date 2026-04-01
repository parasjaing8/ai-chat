# AI Chat Platform - System Knowledge Base

## Hardware

- **Machine**: Mac Mini (Apple Silicon, M-series)
- **RAM**: 16 GB
- **Network IP**: 192.168.0.130
- **Access mode**: Headless (no monitor, no keyboard, no mouse attached)
- **Operating System**: macOS

## Network & Access

- **Windows laptop** at approximately 192.168.0.67 connects via:
  - Browser: http://192.168.0.130:8080
  - SSH: `ssh parasjain@192.168.0.130`
- The Mac Mini has NO display. All interaction happens remotely.

## AI Chat Server

- **Framework**: FastAPI + Uvicorn
- **Port**: 8080 (reserved - do NOT use for anything else)
- **URL**: http://192.168.0.130:8080
- **Entry point**: `/Users/parasjain/ai-chat/server.py`
- **Static files**: `/Users/parasjain/ai-chat/static/`
- **Python venv**: `/Users/parasjain/ai-chat/.venv/`
- **Database**: `/Users/parasjain/ai-chat/chat.db` (SQLite)
- **Environment**: `/Users/parasjain/ai-chat/.env` (contains ANTHROPIC_API_KEY)

## How to Start / Restart the Server

```bash
cd /Users/parasjain/ai-chat
nohup /Users/parasjain/ai-chat/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080 > /tmp/ai-chat.log 2>&1 &
```

To kill first:
```bash
kill $(ps aux | grep 'uvicorn server:app' | grep -v grep | awk '{print $2}') 2>/dev/null
```

## Projects

- **Storage**: `/Users/parasjain/ai-chat/projects/<slug>/`
- **Source files**: `/Users/parasjain/ai-chat/projects/<slug>/src/`
- **Web access**: http://192.168.0.130:8080/play/<slug>/
- Each project has its own git repo initialized on creation
- Each project has a `devlog.md` tracking all build activity

## Serving Web Projects

The server has a `/play/{slug}` route that serves files from `projects/<slug>/src/`.

- `GET /play/car-racing-game/` serves `src/index.html`
- `GET /play/car-racing-game/js/game.js` serves `src/js/game.js`
- Correct MIME types are applied automatically
- This means ANY web project built by the agents is instantly playable from any device on the local network

## AI Models

- **Claude** (Anthropic API): claude-sonnet-4-6, used for orchestration/planning/evaluation
- **DeepSeek** (local via Ollama): deepseek-coder-v2:16b-lite-instruct-q5_K_S, coding specialist
- **Qwen** (local via Ollama): qwen3.5:9b, reasoning/analysis specialist
- **Ollama** runs locally on the Mac Mini at http://localhost:11434

## Git

- Git is available system-wide
- Each project gets `git init` on creation
- Auto-commits happen after each task and at project completion

## Key Directories

```
/Users/parasjain/ai-chat/
  server.py          - Main server
  chat.db            - SQLite database
  .env               - API keys
  .venv/             - Python virtual environment
  static/
    index.html       - Chat UI
  projects/
    <slug>/
      devlog.md      - Build log
      src/           - Project source files (served via /play/<slug>/)
```
