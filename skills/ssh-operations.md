---
name: SSH Operations
description: Remote command execution, file transfer, and process management on Mac Mini or other SSH hosts
keywords: ssh, remote, mac mini, sbc, raspberry pi, terminal, shell, command, execute, server, headless, scp, rsync, process, kill, restart, nohup, log
---

## SSH Operations Skill

### Target Hosts
- **Mac Mini**: `ssh parasjain@<MAC_MINI_IP>` (default: `192.168.0.130`)
- **Default user**: `parasjain`
- **Working dir**: `/Users/parasjain/ai-chat/`

### Common SSH Command Patterns

**Run a command remotely (non-interactive):**
```bash
ssh parasjain@192.168.0.130 "command here"
```

**Run in background, survive disconnect:**
```bash
ssh parasjain@192.168.0.130 "nohup command > /tmp/output.log 2>&1 &"
```

**Transfer files (SCP):**
```bash
# Local → Remote
scp local_file.py parasjain@192.168.0.130:/Users/parasjain/ai-chat/

# Remote → Local
scp parasjain@192.168.0.130:/path/to/file ./local/
```

**Check if a process is running:**
```bash
ssh parasjain@192.168.0.130 "ps aux | grep 'process_name' | grep -v grep"
```

**Restart the AI chat server:**
```bash
ssh parasjain@192.168.0.130 "kill \$(ps aux | grep 'uvicorn server:app' | grep -v grep | awk '{print \$2}') 2>/dev/null; nohup /Users/parasjain/ai-chat/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080 > /tmp/ai-chat.log 2>&1 &"
```

**Tail server logs** (log file rotates at 10 MB, 3 backups kept beside `server.py`):
```bash
ssh parasjain@192.168.0.130 "tail -f /Users/parasjain/ai-chat/ai-chat.log"
```

### Rules
- Port 8080 is reserved for the AI chat server — never use it for other services
- All projects are served via `/play/<slug>/` — no separate servers needed
- Always use `nohup ... &` for long-running background processes
- Always provide the user the exact SSH command to run, not just instructions
