---
name: Python Scripting
description: Python automation scripts, file processing, data handling, and utility development
keywords: python, script, automation, file, csv, json, parse, process, loop, function, class, async, asyncio, subprocess, pathlib, regex, argparse, utility, tool, pipeline
---

## Python Scripting Skill

### Environment Context
- **Python**: available at `/Users/parasjain/ai-chat/.venv/bin/python3` (Mac Mini)
- **Packages**: fastapi, uvicorn, httpx, python-dotenv, psutil (already installed)
- **Working dir**: `/Users/parasjain/ai-chat/`

### File Operations (use pathlib)
```python
from pathlib import Path

# Read / write text
content = Path("file.txt").read_text(encoding="utf-8")
Path("output.txt").write_text(content, encoding="utf-8")

# Iterate files
for f in Path("projects").rglob("*.html"):
    print(f.relative_to(Path("projects")))

# Safe directory creation
Path("new/nested/dir").mkdir(parents=True, exist_ok=True)
```

### JSON Handling
```python
import json

data = json.loads(Path("data.json").read_text())
Path("out.json").write_text(json.dumps(data, indent=2))
```

### Subprocess (run shell commands)
```python
import subprocess

result = subprocess.run(
    ["git", "log", "--oneline", "-5"],
    cwd="/Users/parasjain/ai-chat/projects/my-project",
    capture_output=True, text=True, timeout=10
)
print(result.stdout)
```

### Async Pattern (matches server.py style)
```python
import asyncio
import httpx

async def fetch(url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()

asyncio.run(fetch("https://api.example.com/data"))
```

### Rules
- Always use `pathlib.Path` over `os.path`
- Always specify `encoding="utf-8"` in file operations
- Use `subprocess.run(..., capture_output=True, timeout=10)` for shell calls
- Scripts that run on the Mac Mini should be placed in `/Users/parasjain/ai-chat/`
