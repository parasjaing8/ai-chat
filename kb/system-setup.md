# System Setup & Infrastructure

## Hardware

| Property | Value |
|----------|-------|
| Machine | Mac Mini (Apple Silicon, M-series) |
| RAM | 16 GB |
| OS | macOS (headless — no monitor/keyboard/mouse) |
| Network IP | 192.168.0.130 |
| Dev machine | Windows laptop at ~192.168.0.67 |

## Access

- **Browser**: http://192.168.0.130:8080
- **SSH**: `ssh parasjain@192.168.0.130`
- **Code sync**: Claude Code on Windows syncs via SCP to Mac Mini (configured in `.claude/settings.local.json`)

## Server Management

```bash
# Start (background, logs to /tmp/ai-chat.log)
nohup /Users/parasjain/ai-chat/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080 > /tmp/ai-chat.log 2>&1 &

# Kill
kill $(ps aux | grep 'uvicorn server:app' | grep -v grep | awk '{print $2}') 2>/dev/null

# Tail logs
tail -f /tmp/ai-chat.log
```

## Python Environment

```
/Users/parasjain/ai-chat/
  .venv/          — virtual environment
  .env            — ANTHROPIC_API_KEY (not in git)
  requirements.txt
```

Dependencies: `fastapi==0.115.0`, `uvicorn[standard]==0.32.0`, `httpx==0.28.0`, `python-dotenv==1.0.1`, `psutil==5.9.8`

## AI Models

| Model | Role | Access |
|-------|------|--------|
| `claude-sonnet-4-6` | Orchestration, planning, evaluation | Anthropic API (requires key) |
| `deepseek-coder-v2:16b-lite-instruct-q5_K_S` | Coding tasks | Ollama at localhost:11434 |
| `qwen3.5:9b` | Reasoning/analysis | Ollama at localhost:11434 |

Ollama must be running on the Mac Mini for DeepSeek and Qwen to be available. Claude falls back gracefully if Ollama is down.

## Key Paths on Mac Mini

```
/Users/parasjain/ai-chat/
  server.py          — entire application
  chat.db            — SQLite database
  .env               — API keys
  static/index.html  — Chat UI
  projects/          — Generated project storage
    <slug>/
      devlog.md      — Build activity log
      src/           — Source files (served via /play/<slug>/)
      .git/          — Per-project git repo
  memory/
    universal_lessons.md   — Cross-project learnings
    <slug>/lessons.md      — Project-specific lessons
```

## Cost Tracking

Claude API usage is estimated at:
- Input: $3.00 / million tokens
- Output: $15.00 / million tokens

`OrchStats` accumulates token counts per run and reports cost in the completion summary.
