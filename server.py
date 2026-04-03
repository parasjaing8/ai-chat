"""
AI Group Chat Server + Multi-Agent Development Platform
Runs on Mac Mini (192.168.0.130), port 8080
Access from any home network device: http://192.168.0.130:8080
"""
from __future__ import annotations
import asyncio
import json
import logging
import logging.handlers
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

_log_fmt = logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
_log_handler_stream = logging.StreamHandler()
_log_handler_stream.setFormatter(_log_fmt)
_log_handler_file = logging.handlers.RotatingFileHandler(
    Path(__file__).parent / "ai-chat.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=3,
    encoding="utf-8",
)
_log_handler_file.setFormatter(_log_fmt)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_log_handler_stream, _log_handler_file],
)

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_BASE   = "http://localhost:11434"
KEEP_ALIVE    = "10m"
DB_PATH       = Path(__file__).parent / "chat.db"
STATIC_PATH   = Path(__file__).parent / "static"
PROJECTS_DIR  = Path(__file__).parent / "projects"
MEMORY_DIR    = Path(__file__).parent / "memory"
MEMORY_DIR.mkdir(exist_ok=True)
BACKUP_DIR    = Path(__file__).parent / "backups"
CONTEXT_LEN   = 20   # messages sent as context to models
DISPLAY_LEN   = 60   # messages loaded on page open
SERVER_HOST   = os.getenv("SERVER_HOST", "192.168.0.130:8080")

CLAUDE_COST_INPUT_PER_M  = 3.0
CLAUDE_COST_OUTPUT_PER_M = 15.0

_BUILTIN_AGENTS = {"deepseek", "qwen"}

OLLAMA_MODELS: dict[str, str] = {
    "deepseek": "deepseek-coder-v2:16b-lite-instruct-q5_K_S",
    "qwen":     "qwen3.5:9b",
}

AGENT_LABEL: dict[str, str] = {
    "claude":   "Claude",
    "deepseek": "DeepSeek",
    "qwen":     "Qwen",
    "user":     "Paras",
}

CUSTOM_AGENTS_PATH = Path(__file__).parent / "custom_agents.json"


def _load_custom_agents() -> None:
    if not CUSTOM_AGENTS_PATH.is_file():
        return
    try:
        data: dict = json.loads(CUSTOM_AGENTS_PATH.read_text(encoding="utf-8"))
        for key, info in data.items():
            if key in _BUILTIN_AGENTS:
                continue
            OLLAMA_MODELS[key] = info["model"]
            AGENT_LABEL[key] = info.get("label", key.capitalize())
    except Exception as e:
        logging.warning("Failed to load custom_agents.json: %s", e)


def _save_custom_agents() -> None:
    custom = {
        k: {"model": v, "label": AGENT_LABEL.get(k, k.capitalize())}
        for k, v in OLLAMA_MODELS.items()
        if k not in _BUILTIN_AGENTS
    }
    CUSTOM_AGENTS_PATH.write_text(json.dumps(custom, indent=2), encoding="utf-8")


# ── Safe local command execution (SSH-style) ─────────────────────────────────

_EXEC_COMMANDS: dict[str, list[str]] = {
    "ollama-list":    ["ollama", "list"],
    "ollama-ps":      ["ollama", "ps"],
    "disk-usage":     ["df", "-h"],
    "uptime":         ["uptime"],
    "server-log":     ["tail", "-n", "100", "/tmp/ai-chat.log"],
    "server-status":  ["pgrep", "-lf", "uvicorn"],
    "memory":         ["vm_stat"],
}


async def execute_ssh(cmd_key: str, timeout: int = 30) -> dict:
    argv = _EXEC_COMMANDS.get(cmd_key)
    if argv is None:
        allowed = list(_EXEC_COMMANDS.keys())
        raise ValueError(f"Unknown command key {cmd_key!r}. Allowed: {allowed}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"ok": False, "cmd": cmd_key, "stdout": "",
                    "stderr": f"Command timed out after {timeout}s", "returncode": -1}
        return {
            "ok":         proc.returncode == 0,
            "cmd":        cmd_key,
            "stdout":     stdout_b.decode("utf-8", errors="replace").strip(),
            "stderr":     stderr_b.decode("utf-8", errors="replace").strip(),
            "returncode": proc.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "cmd": cmd_key, "stdout": "",
                "stderr": f"Executable not found: {argv[0]!r}", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "cmd": cmd_key, "stdout": "", "stderr": str(exc), "returncode": -1}


# ── Runtime config (mutable via API) ─────────────────────────────────────────

_config: dict = {
    "master_model":    "claude",
    "claude_enabled":  True,
    "disabled_agents": [],
}
_config_lock = asyncio.Lock()


def get_enabled_agents() -> list[str]:
    disabled = set(_config.get("disabled_agents", []))
    all_agents = ["claude"] + list(OLLAMA_MODELS.keys())
    return [a for a in all_agents if a not in disabled]


def get_master_model() -> str:
    if not _config["claude_enabled"] or not os.getenv("ANTHROPIC_API_KEY", ""):
        return "qwen"
    return _config["master_model"]


def is_claude_available() -> bool:
    return _config["claude_enabled"] and bool(os.getenv("ANTHROPIC_API_KEY", ""))


SYSTEM_PROMPTS = {
    "claude": (
        "You are Claude, an AI assistant in a collaborative group chat. "
        "Other AI participants: DeepSeek (coding specialist), Qwen (reasoning/orchestrator backup). "
        "User is Paras, an indie founder building software products. "
        "Plan, coordinate, and respond concisely. Delegate coding to DeepSeek when appropriate."
    ),
    "deepseek": (
        "You are DeepSeek Coder, a coding specialist in a group chat. "
        "Other participants: Claude (orchestrator), Qwen (reasoning). "
        "User is Paras, an indie founder. "
        "Write clean, production-quality code. Build on previous context. Be concise."
    ),
    "qwen": (
        "You are Qwen, an AI assistant capable of acting as orchestrator, planner, code reviewer, "
        "and reasoning specialist. When orchestrating, break tasks into atomic steps, assign them "
        "to the right agents, and evaluate results. Always output valid JSON when asked for "
        "structured data. Other participants: Claude (when available), DeepSeek (coding). "
        "User is Paras, an indie founder. Be concise and precise."
    ),
}

SKILLS_DIR = Path(__file__).parent / "skills"


# ── Backup ────────────────────────────────────────────────────────────────────

def _run_backup() -> dict:
    import shutil
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / ts
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    errors: list[str] = []
    if DB_PATH.is_file():
        try:
            shutil.copy2(DB_PATH, dest / "chat.db")
            copied.append("chat.db")
        except Exception as e:
            errors.append(f"chat.db: {e}")
    if MEMORY_DIR.is_dir():
        try:
            shutil.copytree(MEMORY_DIR, dest / "memory", dirs_exist_ok=True)
            copied.append("memory/")
        except Exception as e:
            errors.append(f"memory/: {e}")
    all_backups = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.name)
    for old in all_backups[:-14]:
        try:
            shutil.rmtree(old)
        except Exception:
            pass
    status = {"timestamp": ts, "copied": copied, "errors": errors}
    if errors:
        logging.warning("Backup completed with errors: %s", errors)
    else:
        logging.info("Backup completed: %s", copied)
    return status


async def _backup_scheduler() -> None:
    await asyncio.sleep(60)
    while True:
        await asyncio.to_thread(_run_backup)
        await asyncio.sleep(24 * 60 * 60)


# ── Configure sub-modules ────────────────────────────────────────────────────

import db as _db
import models as _models
import orchestration as _orch
from skills_mod import SKILLS_DIR as _SKILLS_DIR_MOD, load_skills as _load_skills

# Wire up db module
_db.configure(db_path=DB_PATH, projects_dir=PROJECTS_DIR, display_len=DISPLAY_LEN)

# Wire up models module
_models.configure(
    ollama_base=OLLAMA_BASE,
    keep_alive=KEEP_ALIVE,
    ollama_models=OLLAMA_MODELS,
    agent_label=AGENT_LABEL,
    system_prompts=SYSTEM_PROMPTS,
    claude_cost_input=CLAUDE_COST_INPUT_PER_M,
    claude_cost_output=CLAUDE_COST_OUTPUT_PER_M,
    get_master_model=get_master_model,
    is_claude_available=is_claude_available,
)

# Wire up orchestration module
_orch.configure(
    server_host=SERVER_HOST,
    memory_dir=MEMORY_DIR,
    context_len=CONTEXT_LEN,
    get_master_model=get_master_model,
    is_claude_available=is_claude_available,
    get_enabled_agents=get_enabled_agents,
    get_config=lambda: _config,
)


# ── Re-exports for backward compatibility (tests + internal references) ──────

from db import (  # noqa: E402, F401
    _db_connect, init_db, save_message, load_history, slugify,
    create_project, get_project, list_projects, update_project_status,
    save_project_message, load_project_messages,
    save_tasks, get_pending_tasks, reset_stuck_tasks, get_resumable_tasks,
    get_last_project_goal, get_all_tasks, update_task,
)
from files_io import (  # noqa: E402, F401
    extract_files_from_response, write_project_files,
    list_project_files, read_project_files,
    git_init, git_commit, init_devlog, append_devlog,
)
from models import (  # noqa: E402, F401
    OrchStats,
    fetch_model_info as _fetch_model_info,
    check_ollama_online, ollama_monitor as _ollama_monitor,
    build_claude_messages, build_ollama_messages,
    check_claude_online, parse_mentions,
    stream_claude, stream_ollama,
    ollama_json_call as _ollama_json_call,
    master_json_call as _master_json_call,
    master_text_call as _master_text_call,
    stream_master,
)
from orchestration import (  # noqa: E402, F401
    read_universal_lessons, read_project_lessons, append_lesson,
    extract_and_save_lesson,
    detect_intent, detect_intent_in_project,
    stream_project_query, claude_plan_project, claude_evaluate_task,
    claude_project_summary,
    safe_send, run_orchestration, run_fix_task, run_test_phase,
)


# ── System stats ─────────────────────────────────────────────────────────────

_net_snap: dict = {}

def _init_net_snap() -> None:
    c = psutil.net_io_counters()
    _net_snap["sent"]  = c.bytes_sent
    _net_snap["recv"]  = c.bytes_recv
    _net_snap["ts"]    = time.monotonic()

_init_net_snap()
_server_start = time.monotonic()

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI()


@app.on_event("startup")
async def startup():
    init_db()
    _load_custom_agents()
    PROJECTS_DIR.mkdir(exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        logging.critical("git not found at startup — project version control will be disabled.")
    ollama_up = await check_ollama_online()
    if ollama_up:
        logging.info("Ollama is reachable at startup")
    else:
        logging.warning("Ollama is NOT reachable at startup — local model calls will fail until it starts")
    asyncio.create_task(_ollama_monitor())
    asyncio.create_task(_backup_scheduler())


app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


# ── Serve project web apps ───────────────────────────────────────────────────

def _auto_generate_index(src_dir: Path, slug: str) -> str | None:
    js_files  = sorted(p.relative_to(src_dir).as_posix()
                       for p in src_dir.rglob("*.js")  if p.is_file())
    css_files = sorted(p.relative_to(src_dir).as_posix()
                       for p in src_dir.rglob("*.css") if p.is_file())

    if not js_files and not css_files:
        return None

    title = slug.replace("-", " ").title()
    css_tags  = "\n  ".join(f'<link rel="stylesheet" href="{f}">' for f in css_files)
    js_tags   = "\n  ".join(f'<script src="{f}"></script>' for f in js_files)
    canvas_tag = '<canvas id="gameCanvas" width="600" height="600"></canvas>' if js_files else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  {css_tags}
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ background:#111; display:flex; flex-direction:column;
           align-items:center; justify-content:center; min-height:100vh; }}
  </style>
</head>
<body>
  {canvas_tag}
  {js_tags}
</body>
</html>"""


MIME_MAP = {
    ".html": "text/html",
    ".htm":  "text/html",
    ".css":  "text/css",
    ".js":   "application/javascript",
    ".mjs":  "application/javascript",
    ".json": "application/json",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2":"font/woff2",
    ".mp3":  "audio/mpeg",
    ".wav":  "audio/wav",
    ".ogg":  "audio/ogg",
    ".mp4":  "video/mp4",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".txt":  "text/plain",
    ".xml":  "application/xml",
}

@app.get("/play/{slug:path}")
async def serve_project(slug: str, request: Request):
    parts = slug.strip("/").split("/", 1)
    project_slug = parts[0]
    file_path = parts[1] if len(parts) > 1 else ""

    if not file_path or file_path.endswith("/"):
        file_path = file_path + "index.html" if file_path else "index.html"

    src_dir = PROJECTS_DIR / project_slug / "src"
    target = (src_dir / file_path).resolve()

    try:
        target.relative_to(src_dir.resolve())
    except ValueError:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not target.is_file():
        if file_path == "index.html" and src_dir.exists():
            generated = _auto_generate_index(src_dir, project_slug)
            if generated:
                return HTMLResponse(generated)
        return JSONResponse({"error": "not found", "path": file_path}, status_code=404)

    suffix = target.suffix.lower()
    media_type = MIME_MAP.get(suffix, "application/octet-stream")
    return FileResponse(str(target), media_type=media_type)


@app.get("/")
async def root():
    return FileResponse(str(STATIC_PATH / "index.html"))


@app.get("/config")
async def get_config():
    return {"server_host": SERVER_HOST}


# ── Route helpers ────────────────────────────────────────────────────────────

def _require_localhost(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="This endpoint is restricted to localhost.")


@app.get("/health")
async def health():
    db_ok = True
    try:
        with _db_connect() as c:
            c.execute("SELECT 1")
    except Exception:
        db_ok = False
    return {"ok": db_ok, "uptime_s": round(time.monotonic() - _server_start, 1), "db": "ok" if db_ok else "error"}


@app.post("/backup")
async def trigger_backup(_: None = Depends(_require_localhost)):
    result = await asyncio.to_thread(_run_backup)
    return result


@app.get("/admin/exec/commands")
async def list_exec_commands(_: None = Depends(_require_localhost)):
    return {"commands": {k: " ".join(v) for k, v in _EXEC_COMMANDS.items()}}


@app.post("/admin/exec")
async def admin_exec(data: dict, _: None = Depends(_require_localhost)):
    cmd_key = (data.get("cmd") or "").strip()
    try:
        result = await execute_ssh(cmd_key)
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "allowed": list(_EXEC_COMMANDS)}, status_code=400)
    logging.info("admin/exec: %s → exit %s", cmd_key, result["returncode"])
    return result


@app.get("/status")
async def status():
    return {"claude_online": await check_claude_online()}


@app.get("/settings")
async def get_settings():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    disabled = set(_config.get("disabled_agents", []))

    ds_model = OLLAMA_MODELS["deepseek"]
    qw_model  = OLLAMA_MODELS["qwen"]
    ds_info, qw_info = await asyncio.gather(
        _fetch_model_info(ds_model),
        _fetch_model_info(qw_model),
    )

    return {
        "claude": {
            "api_key_set": bool(key),
            "api_key_preview": f"sk-ant-...{key[-4:]}" if len(key) > 8 else "",
            "enabled": "claude" not in disabled,
        },
        "deepseek": {
            "model": ds_model,
            "keep_alive": KEEP_ALIVE,
            "size": ds_info.get("size", "—"),
            "quant": ds_info.get("quant", "—"),
            "enabled": "deepseek" not in disabled,
        },
        "qwen": {
            "model": qw_model,
            "keep_alive": KEEP_ALIVE,
            "size": qw_info.get("size", "—"),
            "quant": qw_info.get("quant", "—"),
            "enabled": "qwen" not in disabled,
        },
    }


@app.post("/settings/agent")
async def toggle_agent(data: dict):
    agent = data.get("agent", "")
    if agent != "claude" and agent not in OLLAMA_MODELS:
        return {"error": "unknown agent"}
    enabled = bool(data.get("enabled", True))
    async with _config_lock:
        disabled: set = set(_config.get("disabled_agents", []))
        if not enabled:
            disabled.add(agent)
        else:
            disabled.discard(agent)
        _config["disabled_agents"] = list(disabled)
        if agent == "claude":
            _config["claude_enabled"] = enabled
        result_disabled = list(_config["disabled_agents"])
    logging.info("Agent toggle: %s enabled=%s disabled_agents=%s", agent, enabled, result_disabled)
    return {"agent": agent, "enabled": enabled, "disabled_agents": result_disabled}


@app.post("/settings/apikey")
async def save_apikey(data: dict, _: None = Depends(_require_localhost)):
    key = data.get("key", "").strip()
    if not key:
        return {"ok": False, "error": "Key cannot be empty"}
    os.environ["ANTHROPIC_API_KEY"] = key
    env_path = Path(__file__).parent / ".env"
    env_path.write_text(f"ANTHROPIC_API_KEY={key}\n")
    online = await check_claude_online()
    return {"ok": online, "error": None if online else "Key saved but Claude didn't respond — check key validity"}


@app.get("/settings/test/{agent}")
async def test_agent(agent: str):
    if agent == "claude":
        return {"online": await check_claude_online()}
    if agent in OLLAMA_MODELS:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{OLLAMA_BASE}/api/tags")
                names = [m["name"] for m in r.json().get("models", [])]
                return {"online": OLLAMA_MODELS[agent] in names}
        except Exception:
            return {"online": False}
    return {"online": False}


@app.get("/stats")
async def get_stats():
    cpu  = psutil.cpu_percent(interval=None)
    mem  = psutil.virtual_memory()
    swap = psutil.swap_memory()
    net  = psutil.net_io_counters()

    now = time.monotonic()
    dt  = now - _net_snap["ts"]
    if dt > 0:
        rx_kbs = (net.bytes_recv - _net_snap["recv"]) / dt / 1024
        tx_kbs = (net.bytes_sent - _net_snap["sent"]) / dt / 1024
    else:
        rx_kbs = tx_kbs = 0.0
    _net_snap["sent"] = net.bytes_sent
    _net_snap["recv"] = net.bytes_recv
    _net_snap["ts"]   = now

    return {
        "cpu":          round(cpu, 1),
        "ram_used":     round(mem.used  / 1073741824, 1),
        "ram_total":    round(mem.total / 1073741824, 1),
        "swap_used":    round(swap.used  / 1073741824, 2),
        "swap_total":   round(swap.total / 1073741824, 1),
        "rx_kbs":       round(rx_kbs, 1),
        "tx_kbs":       round(tx_kbs, 1),
        "ollama_online": _models._ollama_status["online"],
    }


# ── REST endpoints for projects ──────────────────────────────────────────────

@app.get("/projects")
async def api_list_projects():
    return list_projects()


@app.post("/projects")
async def api_create_project(data: dict):
    name = data.get("name", "").strip()
    if not name:
        return {"error": "Name is required"}
    description = data.get("description", "").strip()
    project = create_project(name, description)
    return project


@app.get("/projects/{project_id}")
async def api_get_project(project_id: int):
    p = get_project(project_id)
    if not p:
        return {"error": "Not found"}
    return p


@app.get("/projects/{project_id}/files")
async def api_get_project_files(project_id: int):
    p = get_project(project_id)
    if not p:
        return {"error": "Not found"}
    src = Path(p["folder_path"]) / "src"
    if not src.exists():
        return []
    result = []
    for fpath in sorted(src.rglob("*")):
        if fpath.is_file():
            try:
                rel = str(fpath.relative_to(src)).replace("\\", "/")
                result.append({"path": rel, "size": fpath.stat().st_size})
            except Exception:
                pass
    return result


@app.get("/projects/{project_id}/files/{file_path:path}")
async def api_get_project_file(project_id: int, file_path: str):
    p = get_project(project_id)
    if not p:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("Not found", status_code=404)
    src = Path(p["folder_path"]) / "src"
    try:
        target = (src / file_path).resolve()
        src_resolved = src.resolve()
        target.relative_to(src_resolved)
    except (ValueError, Exception):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("Forbidden", status_code=403)
    if not target.is_file():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("Not found", status_code=404)
    content = target.read_text(encoding="utf-8", errors="replace")
    return {"path": file_path, "content": content}


@app.delete("/projects/{project_id}")
async def api_delete_project(project_id: int, _: None = Depends(_require_localhost)):
    p = get_project(project_id)
    if not p:
        return {"error": "Not found"}

    folder = Path(p["folder_path"])
    if folder.exists():
        import shutil
        shutil.rmtree(folder, ignore_errors=True)

    with _db_connect() as c:
        c.execute("DELETE FROM project_messages WHERE project_id = ?", (project_id,))
        c.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        c.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    return {"ok": True}


# ── Master model settings ────────────────────────────────────────────────────

@app.get("/settings/master")
async def get_master_settings():
    return {
        "master_model":    _config["master_model"],
        "claude_enabled":  _config["claude_enabled"],
        "effective_master": get_master_model(),
    }


@app.post("/settings/master")
async def set_master_settings(data: dict):
    async with _config_lock:
        if "model" in data and data["model"] in ("claude", "qwen"):
            _config["master_model"] = data["model"]
        if "claude_enabled" in data:
            _config["claude_enabled"] = bool(data["claude_enabled"])
        result = {
            "master_model":    _config["master_model"],
            "claude_enabled":  _config["claude_enabled"],
            "effective_master": get_master_model(),
        }
    logging.info(
        "Master model updated -> master=%s claude_enabled=%s effective=%s",
        result["master_model"], result["claude_enabled"], result["effective_master"]
    )
    return result


# ── Skills ────────────────────────────────────────────────────────────────────

@app.get("/skills")
async def list_skills():
    if not SKILLS_DIR.exists():
        return []
    skills = []
    for sf in sorted(SKILLS_DIR.glob("*.md")):
        try:
            raw = sf.read_text(encoding="utf-8")
            name_m = re.search(r"^name:\s*(.+)$", raw, re.MULTILINE)
            kw_m   = re.search(r"^keywords:\s*(.+)$", raw, re.MULTILINE)
            desc_m = re.search(r"^description:\s*(.+)$", raw, re.MULTILINE)
            skills.append({
                "file":        sf.name,
                "name":        name_m.group(1).strip() if name_m else sf.stem,
                "description": desc_m.group(1).strip() if desc_m else "",
                "keywords":    [k.strip() for k in kw_m.group(1).split(",")] if kw_m else [],
            })
        except Exception:
            pass
    return skills


@app.get("/skills/{filename}")
async def get_skill(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    path = SKILLS_DIR / filename
    if not path.is_file() or path.suffix != ".md":
        return JSONResponse({"error": "Not found"}, status_code=404)
    content = path.read_text(encoding="utf-8")
    return {"file": filename, "content": content}


@app.put("/skills/{filename}")
async def update_skill(filename: str, data: dict, _: None = Depends(_require_localhost)):
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    if not filename.endswith(".md"):
        return JSONResponse({"error": "Filename must end in .md"}, status_code=400)
    content = data.get("content", "")
    if not content.strip():
        return JSONResponse({"error": "Content cannot be empty"}, status_code=400)
    path = SKILLS_DIR / filename
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "file": filename}


# ── Ollama model registry ────────────────────────────────────────────────────

@app.get("/ollama/models")
async def list_ollama_models():
    available: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            if r.status_code == 200:
                available = r.json().get("models", [])
    except Exception:
        pass

    model_to_key = {v: k for k, v in OLLAMA_MODELS.items()}
    result = []
    seen_names: set[str] = set()

    for m in available:
        name = m.get("name", "")
        seen_names.add(name)
        size = m.get("size", 0)
        result.append({
            "name":          name,
            "size_gb":       round(size / 1e9, 1) if size else None,
            "registered_as": model_to_key.get(name),
            "builtin":       model_to_key.get(name) in _BUILTIN_AGENTS,
            "offline":       False,
        })

    for key, model in OLLAMA_MODELS.items():
        if model not in seen_names:
            result.append({
                "name":          model,
                "size_gb":       None,
                "registered_as": key,
                "builtin":       key in _BUILTIN_AGENTS,
                "offline":       True,
            })

    return result


@app.post("/ollama/models")
async def add_ollama_agent(data: dict, _: None = Depends(_require_localhost)):
    key   = data.get("key", "").strip().lower()
    model = data.get("model", "").strip()
    label = data.get("label", "").strip() or key.capitalize()
    if not key or not model:
        return JSONResponse({"error": "key and model are required"}, status_code=400)
    if not re.match(r'^[a-z0-9_-]+$', key):
        return JSONResponse({"error": "key must be lowercase alphanumeric, dash, or underscore"}, status_code=400)
    OLLAMA_MODELS[key] = model
    AGENT_LABEL[key]   = label
    await asyncio.to_thread(_save_custom_agents)
    logging.info("Custom agent registered: %s → %s (%s)", key, model, label)
    return {"ok": True, "key": key, "model": model, "label": label}


@app.delete("/ollama/models/{key}")
async def remove_ollama_agent(key: str, _: None = Depends(_require_localhost)):
    if key in _BUILTIN_AGENTS:
        return JSONResponse({"error": "Cannot remove built-in agents"}, status_code=400)
    if key not in OLLAMA_MODELS:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    del OLLAMA_MODELS[key]
    AGENT_LABEL.pop(key, None)
    async with _config_lock:
        disabled = set(_config.get("disabled_agents", []))
        disabled.discard(key)
        _config["disabled_agents"] = list(disabled)
    await asyncio.to_thread(_save_custom_agents)
    logging.info("Custom agent removed: %s", key)
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    recv_q: asyncio.Queue = asyncio.Queue()
    cancel_event = asyncio.Event()
    active_task: asyncio.Task | None = None

    async def _receiver():
        try:
            while True:
                msg = await ws.receive_json()
                await recv_q.put(msg)
        except Exception:
            await recv_q.put({"type": "__disc__"})

    recv = asyncio.create_task(_receiver())

    async def _run_task(coro):
        nonlocal active_task
        cancel_event.clear()
        active_task = asyncio.create_task(coro)
        try:
            while not active_task.done():
                try:
                    side = await asyncio.wait_for(recv_q.get(), timeout=0.3)
                    if side.get("type") == "cancel":
                        cancel_event.set()
                        active_task.cancel()
                    elif side.get("type") == "__disc__":
                        active_task.cancel()
                        await recv_q.put(side)
                    else:
                        await recv_q.put(side)
                except asyncio.TimeoutError:
                    pass
            await active_task
        except asyncio.CancelledError:
            pass
        except (WebSocketDisconnect, RuntimeError):
            cancel_event.set()
            active_task.cancel()
        finally:
            active_task = None

    try:
        await ws.send_json({"type": "history", "messages": load_history()})
        claude_online = await check_claude_online()
        await ws.send_json({"type": "status", "claude_online": claude_online})

        while True:
            data = await recv_q.get()
            if data.get("type") == "__disc__":
                break

            msg_type = data.get("type", "content")

            if msg_type == "cancel":
                if active_task and not active_task.done():
                    cancel_event.set()
                    active_task.cancel()
                else:
                    await ws.send_json({"type": "cancelled"})
                continue

            if msg_type == "get_projects":
                await ws.send_json({"type": "project_list", "projects": list_projects()})
                continue

            if msg_type == "load_project":
                pid = data.get("project_id")
                proj = get_project(pid)
                if proj:
                    await ws.send_json({
                        "type":     "project_loaded",
                        "project":  proj,
                        "messages": load_project_messages(pid),
                        "tasks":    get_all_tasks(pid),
                    })
                continue

            if msg_type == "start_orchestration":
                pid  = data.get("project_id")
                goal = data.get("goal", "")
                if pid and goal:
                    await _run_task(run_orchestration(ws, pid, goal, cancel_event=cancel_event))
                    if cancel_event.is_set():
                        reset_stuck_tasks(pid)
                        await ws.send_json({"type": "cancelled"})
                continue

            if msg_type == "resume_orchestration":
                pid = data.get("project_id")
                if pid:
                    await _run_task(run_orchestration(ws, pid, goal="", resume=True, cancel_event=cancel_event))
                    if cancel_event.is_set():
                        reset_stuck_tasks(pid)
                        await ws.send_json({"type": "cancelled"})
                continue

            if msg_type == "fix_project":
                pid      = data.get("project_id")
                feedback = data.get("feedback", "").strip()
                if pid and feedback:
                    await _run_task(run_fix_task(ws, pid, feedback, cancel_event))
                    if cancel_event.is_set():
                        await ws.send_json({"type": "cancelled"})
                continue

            content    = data.get("content", "").strip()
            project_id_ctx = data.get("project_id")
            if not content:
                continue

            # /exec shortcut
            if content.startswith("/exec "):
                cmd_key = content[6:].strip()
                try:
                    result = await execute_ssh(cmd_key)
                    output  = result["stdout"] or result["stderr"] or "(no output)"
                    tick    = "\u2713" if result["ok"] else "\u2717"
                    msg_md  = f"```\n{tick} exec: {cmd_key}  [exit {result['returncode']}]\n\n{output}\n```"
                except ValueError:
                    cmds   = ", ".join(f"`{k}`" for k in _EXEC_COMMANDS)
                    msg_md = f"Unknown command key. Available:\n\n{cmds}"
                await ws.send_json({"type": "agent_count", "count": 1})
                await ws.send_json({"type": "chunk",       "agent": "system", "content": msg_md})
                await ws.send_json({"type": "done",        "agent": "system"})
                continue

            claude_online = await check_claude_online()
            master_available = True

            if project_id_ctx:
                prior_tasks_quick = []
                proj = get_project(project_id_ctx)
                if not proj:
                    routing = "chat"
                else:
                    prior_tasks_quick = get_all_tasks(project_id_ctx)
                    if not prior_tasks_quick:
                        routing = "build"
                    else:
                        routing = await detect_intent_in_project(content, proj["name"])

                if routing == "query":
                    await _run_task(stream_project_query(ws, project_id_ctx, content, cancel_event))
                    if cancel_event.is_set():
                        await ws.send_json({"type": "cancelled"})
                    continue

                if routing == "build":
                    if not prior_tasks_quick:
                        await _run_task(run_orchestration(ws, project_id_ctx, content, cancel_event=cancel_event))
                        if cancel_event.is_set():
                            reset_stuck_tasks(project_id_ctx)
                            await ws.send_json({"type": "cancelled"})
                    elif proj.get("status") == "completed":
                        await _run_task(run_fix_task(ws, project_id_ctx, content, cancel_event))
                        if cancel_event.is_set():
                            await ws.send_json({"type": "cancelled"})
                    else:
                        await ws.send_json({
                            "type":             "intent_detected",
                            "intent":           "project_continue",
                            "original_message": content,
                            "project_id":       project_id_ctx,
                        })
                    continue

            elif master_available:
                intent = await detect_intent(content)
                if intent.get("type") == "project_new":
                    await ws.send_json({
                        "type":             "intent_detected",
                        "intent":           "project_new",
                        "name":             intent.get("name", "New Project"),
                        "original_message": content,
                    })
                    continue
                if intent.get("type") == "project_continue":
                    await ws.send_json({
                        "type":             "intent_detected",
                        "intent":           "project_continue",
                        "original_message": content,
                    })
                    continue

            # Normal chat flow
            save_message("user", content)
            await ws.send_json({
                "type":      "user",
                "content":   content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await ws.send_json({"type": "status", "claude_online": claude_online})

            targets = parse_mentions(content, claude_online)
            ctx     = load_history(CONTEXT_LEN)
            logging.info("CHAT routed to: %s", ", ".join(targets))

            await ws.send_json({"type": "agent_count", "count": len(targets)})

            async def _do_chat():
                nonlocal ctx
                for agent in targets:
                    await ws.send_json({"type": "typing", "agent": agent})
                    full = ""
                    gen  = (stream_claude(ctx, cancel_event=cancel_event)
                            if agent == "claude"
                            else stream_ollama(agent, ctx, cancel_event=cancel_event))
                    async for chunk in gen:
                        if cancel_event.is_set():
                            break
                        await ws.send_json({"type": "chunk", "agent": agent, "content": chunk})
                        full += chunk
                    if full.strip():
                        save_message(agent, full.strip())
                        ctx = load_history(CONTEXT_LEN)
                    await ws.send_json({"type": "done", "agent": agent})

            await _run_task(_do_chat())
            if cancel_event.is_set():
                await ws.send_json({"type": "cancelled"})

    except WebSocketDisconnect:
        pass
    finally:
        recv.cancel()
        if active_task and not active_task.done():
            active_task.cancel()
