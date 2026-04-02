---
name: System Administration
description: Mac Mini process management, resource monitoring, log inspection, and service management
keywords: system, admin, process, cpu, memory, ram, disk, monitor, log, service, startup, launchd, cron, kill, restart, update, install, brew, python, venv, pip, ollama, uvicorn
---

## System Administration Skill

### Mac Mini Context
- **Host**: 192.168.0.130
- **User**: parasjain
- **OS**: macOS (Apple Silicon, M-series, 16GB RAM)
- **Access**: SSH only — headless, no display

### Server Management
```bash
# Check if server is running
ssh parasjain@192.168.0.130 "ps aux | grep uvicorn | grep -v grep"

# Restart server
ssh parasjain@192.168.0.130 "
  kill \$(ps aux | grep 'uvicorn server:app' | grep -v grep | awk '{print \$2}') 2>/dev/null
  sleep 1
  nohup /Users/parasjain/ai-chat/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080 > /tmp/ai-chat.log 2>&1 &
  echo 'Server restarted'
"

# View logs
ssh parasjain@192.168.0.130 "tail -50 /tmp/ai-chat.log"
```

### Ollama Management
```bash
# Check Ollama is running
ssh parasjain@192.168.0.130 "curl -s http://localhost:11434/api/tags | python3 -c 'import sys,json; [print(m[\"name\"]) for m in json.load(sys.stdin)[\"models\"]]'"

# Restart Ollama
ssh parasjain@192.168.0.130 "pkill ollama; sleep 2; nohup ollama serve > /tmp/ollama.log 2>&1 &"
```

### Resource Monitoring
```bash
# CPU + Memory snapshot
ssh parasjain@192.168.0.130 "top -l 1 -n 5 | head -20"

# Disk usage
ssh parasjain@192.168.0.130 "df -h /Users/parasjain/ai-chat/"

# Check projects folder size
ssh parasjain@192.168.0.130 "du -sh /Users/parasjain/ai-chat/projects/*"
```

### Python Environment
```bash
# Install a new dependency
ssh parasjain@192.168.0.130 "/Users/parasjain/ai-chat/.venv/bin/pip install package_name"

# Check installed packages
ssh parasjain@192.168.0.130 "/Users/parasjain/ai-chat/.venv/bin/pip list"
```

### Rules
- Always prefer targeted process kills over `killall` or broad patterns
- Never restart the server mid-orchestration — wait for it to complete
- The `.env` file contains secrets — never `cat` it over SSH in shared sessions
