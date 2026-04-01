"""
AI Group Chat Server + Multi-Agent Development Platform
Runs on Mac Mini (192.168.0.130), port 8080
Access from any home network device: http://192.168.0.130:8080
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import textwrap
import time
from datetime import datetime
from pathlib import Path

import psutil

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_BASE   = "http://localhost:11434"
KEEP_ALIVE    = "2m"
DB_PATH       = Path(__file__).parent / "chat.db"
STATIC_PATH   = Path(__file__).parent / "static"
PROJECTS_DIR  = Path(__file__).parent / "projects"
MEMORY_DIR    = Path(__file__).parent / "memory"
MEMORY_DIR.mkdir(exist_ok=True)
CONTEXT_LEN   = 20   # messages sent as context to models
DISPLAY_LEN   = 60   # messages loaded on page open
# Server host — override via SERVER_HOST env var (used in agent system prompts and play URLs)
SERVER_HOST   = os.getenv("SERVER_HOST", "192.168.0.130:8080")

# Claude pricing for cost estimation ($ per million tokens, as of 2025)
CLAUDE_COST_INPUT_PER_M  = 3.0
CLAUDE_COST_OUTPUT_PER_M = 15.0

OLLAMA_MODELS = {
    "deepseek": "deepseek-coder-v2:16b-lite-instruct-q5_K_S",
    "qwen":     "qwen3.5:9b",
}

AGENT_LABEL = {
    "claude":   "Claude",
    "deepseek": "DeepSeek",
    "qwen":     "Qwen",
    "user":     "Paras",
}

SYSTEM_PROMPTS = {
    "claude": (
        "You are Claude, an AI assistant in a collaborative group chat. "
        "Other AI participants: DeepSeek (coding specialist), Qwen (reasoning specialist). "
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
        "You are Qwen, a reasoning and analysis specialist in a group chat. "
        "Other participants: Claude (orchestrator), DeepSeek (coding). "
        "User is Paras, an indie founder. "
        "Focus on logical analysis, planning, and problem decomposition. Be concise."
    ),
}

# ── OrchStats — token & time tracking across one orchestration run ────────────

class OrchStats:
    """Tracks token usage and timing across an orchestration / fix run."""

    def __init__(self):
        self.start_time: float = time.time()
        self.by_agent: dict[str, dict] = {}

    def record(self, agent: str, input_tok: int, output_tok: int) -> None:
        if agent not in self.by_agent:
            self.by_agent[agent] = {"tasks": 0, "input_tokens": 0, "output_tokens": 0}
        self.by_agent[agent]["tasks"] += 1
        self.by_agent[agent]["input_tokens"]  += input_tok
        self.by_agent[agent]["output_tokens"] += output_tok

    def elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        return f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"

    def claude_tokens(self) -> tuple[int, int]:
        """Returns (input, output) for Claude only."""
        c = self.by_agent.get("claude", {})
        return c.get("input_tokens", 0), c.get("output_tokens", 0)

    def local_tokens(self) -> int:
        total = 0
        for agent, data in self.by_agent.items():
            if agent != "claude":
                total += data.get("input_tokens", 0) + data.get("output_tokens", 0)
        return total

    def total_tasks(self) -> int:
        return sum(d["tasks"] for d in self.by_agent.values())

    def to_summary(self) -> dict:
        inp, out = self.claude_tokens()
        local = self.local_tokens()
        cost_usd = (inp * CLAUDE_COST_INPUT_PER_M + out * CLAUDE_COST_OUTPUT_PER_M) / 1_000_000
        return {
            "elapsed":        self.elapsed(),
            "by_agent":       self.by_agent,
            "claude_input":   inp,
            "claude_output":  out,
            "local_tokens":   local,
            "cost_usd":       round(cost_usd, 4),
            "total_tasks":    self.total_tasks(),
        }


# ── Memory system ─────────────────────────────────────────────────────────────

UNIVERSAL_LESSONS_PATH = MEMORY_DIR / "universal_lessons.md"


def _lessons_path(project: dict) -> Path:
    return Path(project["folder_path"]) / "lessons.md"


def read_universal_lessons(limit: int = 8) -> str:
    """Return last N universal lessons as a string."""
    if not UNIVERSAL_LESSONS_PATH.exists():
        return ""
    lines = UNIVERSAL_LESSONS_PATH.read_text(encoding="utf-8").strip().splitlines()
    # Each lesson starts with "- " — grab last `limit` of them
    lessons = [l for l in lines if l.startswith("- ")]
    return "\n".join(lessons[-limit:])


def read_project_lessons(project: dict, limit: int = 5) -> str:
    """Return last N lessons for this specific project."""
    p = _lessons_path(project)
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    lessons = [l for l in lines if l.startswith("- ")]
    return "\n".join(lessons[-limit:])


def _count_project_lessons(project: dict) -> int:
    p = _lessons_path(project)
    if not p.exists():
        return 0
    return sum(1 for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("- "))


def append_lesson(project: dict, lesson: str, universal: bool = False) -> None:
    """Append a lesson to project lessons.md and optionally to universal."""
    now = datetime.utcnow().strftime("%Y-%m-%d")
    entry = f"- [{now}] [{project['name']}] {lesson.strip()}\n"
    p = _lessons_path(project)
    with open(p, "a", encoding="utf-8") as f:
        f.write(entry)
    if universal:
        with open(UNIVERSAL_LESSONS_PATH, "a", encoding="utf-8") as f:
            f.write(entry)


async def extract_and_save_lesson(project: dict, feedback: str, fix_summary: str) -> str | None:
    """Ask Claude to distill a reusable lesson from a bug fix. Returns lesson text."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    prompt = (
        f"A bug was fixed in project '{project['name']}'.\n"
        f"User feedback: {feedback}\n"
        f"What was fixed: {fix_summary[:500]}\n\n"
        f"Write ONE concise lesson (max 20 words) that would help avoid this bug in future projects. "
        f"Start with a verb. Example: 'Always initialize canvas game loop with requestAnimationFrame, not setInterval.' "
        f"Respond with ONLY the lesson, nothing else."
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 60,
                      "messages": [{"role": "user", "content": prompt}]},
            )
        if r.status_code != 200:
            return None
        lesson = r.json().get("content", [{}])[0].get("text", "").strip()
        if not lesson:
            return None
        # Every 3rd project lesson also goes universal
        count = _count_project_lessons(project)
        universal = (count % 3 == 0)
        append_lesson(project, lesson, universal=universal)
        return lesson
    except Exception as e:
        logging.warning("extract_lesson failed: %s", e)
        return None


# ── Database ──────────────────────────────────────────────────────────────────

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                role      TEXT NOT NULL,
                content   TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                slug        TEXT UNIQUE NOT NULL,
                description TEXT DEFAULT '',
                folder_path TEXT NOT NULL,
                status      TEXT DEFAULT 'active',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS project_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                task_id    INTEGER DEFAULT NULL,
                timestamp  TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id     INTEGER NOT NULL,
                task_number    INTEGER NOT NULL,
                title          TEXT NOT NULL,
                description    TEXT NOT NULL,
                assigned_to    TEXT NOT NULL,
                status         TEXT DEFAULT 'pending',
                files_to_create TEXT DEFAULT '[]',
                output_result  TEXT DEFAULT '',
                created_at     TEXT NOT NULL,
                completed_at   TEXT DEFAULT NULL
            )
        """)


def save_message(role: str, content: str) -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT INTO messages (role, content, timestamp) VALUES (?,?,?)",
            (role, content, datetime.utcnow().isoformat()),
        )


def load_history(limit: int = DISPLAY_LEN) -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT role, content, timestamp FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)]

# ── Project management ────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return slug or "project"


def create_project(name: str, description: str) -> dict:
    slug = slugify(name)
    now = datetime.utcnow().isoformat()
    folder = str(PROJECTS_DIR / slug)

    # Ensure unique slug
    base_slug = slug
    counter = 1
    while True:
        try:
            with sqlite3.connect(DB_PATH) as c:
                c.execute(
                    "INSERT INTO projects (name, slug, description, folder_path, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (name, slug, description, folder, 'active', now, now),
                )
                project_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            break
        except sqlite3.IntegrityError:
            counter += 1
            slug = f"{base_slug}-{counter}"
            folder = str(PROJECTS_DIR / slug)

    # Create folder structure
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / "src").mkdir(exist_ok=True)

    project = {
        "id": project_id,
        "name": name,
        "slug": slug,
        "description": description,
        "folder_path": folder,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }

    init_devlog(project)
    git_init(folder)

    return project


def get_project(project_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT id, name, slug, description, folder_path, status, created_at, updated_at FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "slug": row[2], "description": row[3],
        "folder_path": row[4], "status": row[5], "created_at": row[6], "updated_at": row[7],
    }


def list_projects() -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT id, name, slug, description, folder_path, status, created_at, updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {"id": r[0], "name": r[1], "slug": r[2], "description": r[3],
         "folder_path": r[4], "status": r[5], "created_at": r[6], "updated_at": r[7]}
        for r in rows
    ]


def update_project_status(project_id: int, status: str) -> None:
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?", (status, now, project_id))


def save_project_message(project_id: int, role: str, content: str, task_id: int | None = None) -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT INTO project_messages (project_id, role, content, task_id, timestamp) VALUES (?,?,?,?,?)",
            (project_id, role, content, task_id, datetime.utcnow().isoformat()),
        )


def load_project_messages(project_id: int, limit: int = 30) -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT id, role, content, task_id, timestamp FROM project_messages WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [
        {"id": r[0], "role": r[1], "content": r[2], "task_id": r[3], "timestamp": r[4]}
        for r in reversed(rows)
    ]

# ── Task management ───────────────────────────────────────────────────────────

def save_tasks(project_id: int, tasks: list[dict]) -> list[dict]:
    now = datetime.utcnow().isoformat()
    result = []
    with sqlite3.connect(DB_PATH) as c:
        for t in tasks:
            c.execute(
                "INSERT INTO tasks (project_id, task_number, title, description, assigned_to, status, files_to_create, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (project_id, t["task_number"], t["title"], t["description"],
                 t["assigned_to"], "pending", json.dumps(t.get("files_to_create", [])), now),
            )
            tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            result.append({
                "id": tid, "project_id": project_id, "task_number": t["task_number"],
                "title": t["title"], "description": t["description"],
                "assigned_to": t["assigned_to"], "status": "pending",
                "files_to_create": t.get("files_to_create", []),
                "created_at": now, "completed_at": None,
            })
    return result


def get_pending_tasks(project_id: int) -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT id, task_number, title, description, assigned_to, status, files_to_create, output_result, created_at, completed_at FROM tasks WHERE project_id=? AND status='pending' ORDER BY task_number",
            (project_id,),
        ).fetchall()
    return [_task_row_to_dict(r) for r in rows]


def reset_stuck_tasks(project_id: int) -> int:
    """Reset any in_progress tasks back to pending. Returns count reset."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE tasks SET status='pending' WHERE project_id=? AND status='in_progress'",
            (project_id,),
        )
        return c.execute(
            "SELECT changes()"
        ).fetchone()[0]


def get_resumable_tasks(project_id: int) -> list[dict]:
    """Return pending tasks for a project (for resume). Resets stuck in_progress first."""
    reset_stuck_tasks(project_id)
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT id, task_number, title, description, assigned_to, status, files_to_create, output_result, created_at, completed_at FROM tasks WHERE project_id=? AND status != 'done' ORDER BY task_number",
            (project_id,),
        ).fetchall()
    return [_task_row_to_dict(r) for r in rows]


def get_last_project_goal(project_id: int) -> str:
    """Retrieve the first user message in a project (the original goal)."""
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT content FROM project_messages WHERE project_id=? AND role='user' ORDER BY id ASC LIMIT 1",
            (project_id,),
        ).fetchone()
    return row[0] if row else ""


def get_all_tasks(project_id: int) -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT id, task_number, title, description, assigned_to, status, files_to_create, output_result, created_at, completed_at FROM tasks WHERE project_id=? ORDER BY task_number",
            (project_id,),
        ).fetchall()
    return [_task_row_to_dict(r) for r in rows]


def _task_row_to_dict(r) -> dict:
    try:
        ftc = json.loads(r[6])
    except (json.JSONDecodeError, TypeError):
        ftc = []
    return {
        "id": r[0], "task_number": r[1], "title": r[2], "description": r[3],
        "assigned_to": r[4], "status": r[5], "files_to_create": ftc,
        "output_result": r[7], "created_at": r[8], "completed_at": r[9],
    }


def update_task(task_id: int, **kwargs) -> None:
    if not kwargs:
        return
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(task_id)
    with sqlite3.connect(DB_PATH) as c:
        c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)

# ── File I/O ──────────────────────────────────────────────────────────────────

def extract_files_from_response(content: str) -> list[dict]:
    """Parse LLM output for code blocks with file paths.

    Handles all patterns agents use in practice:
      S1: **`path`** or ### `path` before a code block
      S2: FILE: marker as first line INSIDE a code block
      S3: FILE: marker on its own line BEFORE a code block (deepseek style)
      S4: FILE: marker at top of raw code with no fences (qwen style)
    """
    files: list[dict] = []
    seen: set[str] = set()

    FILE_MARKER = re.compile(
        r'(?:^|\n)[ \t]*(?://|#|<!--|--|;)[ \t]*FILE:[ \t]*([^\n]+?)[ \t]*(?:-->)?[ \t]*\n',
    )

    # ── S3 + S4: any FILE: comment marker ────────────────────────────────────
    for m in FILE_MARKER.finditer(content):
        filename = m.group(1).strip()
        # Agents sometimes prefix with src/ — strip it since write_project_files adds it
        if filename.startswith('src/'):
            filename = filename[4:]
        if not filename or filename in seen:
            continue

        rest = content[m.end():]

        # S3: optional blank lines then a fenced code block
        cb = re.match(r'[ \t]*\n{0,2}[ \t]*```[\w\-]*[ \t]*\n(.*?)```', rest, re.DOTALL)
        if cb:
            code = cb.group(1).rstrip()
        else:
            # S4: raw code — take until next FILE: marker or end of content
            next_marker = FILE_MARKER.search(rest)
            end = next_marker.start() if next_marker else len(rest)
            code = rest[:end].strip()

        if code:
            seen.add(filename)
            files.append({"filename": filename, "content": code})

    if files:
        return files

    # ── S1: **`path`** or ### `path` before code block ───────────────────────
    path_before_re = re.compile(r'(?:\*\*`([^`]+)`\*\*|###\s*`([^`]+)`)\s*\n\s*```')
    for m in path_before_re.finditer(content):
        filename = (m.group(1) or m.group(2)).strip()
        block_start = content.find('```', m.end() - 3)
        if block_start == -1:
            continue
        code_start = content.find('\n', block_start)
        if code_start == -1:
            continue
        code_end = content.find('```', code_start + 1)
        if code_end == -1:
            continue
        code = content[code_start + 1:code_end].rstrip()
        if filename and code and filename not in seen:
            seen.add(filename)
            files.append({"filename": filename, "content": code})

    if files:
        return files

    # ── S2: FILE: as first line inside a fenced code block ───────────────────
    code_block_re = re.compile(r'```[\w\-]*[ \t]*\n(.*?)```', re.DOTALL)
    for m in code_block_re.finditer(content):
        block = m.group(1)
        lines = block.split('\n', 1)
        first_line = lines[0].strip()
        fm = re.match(r'^(?://|#|<!--|--|;)\s*FILE:\s*(.+?)(?:\s*-->)?$', first_line)
        if fm:
            filename = fm.group(1).strip()
            code = (lines[1] if len(lines) > 1 else '').rstrip()
            if filename and code and filename not in seen:
                seen.add(filename)
                files.append({"filename": filename, "content": code})

    return files


def write_project_files(project: dict, files: list[dict]) -> list[str]:
    """Write files to project folder_path/src/, returns list of relative paths written."""
    written = []
    base = (Path(project["folder_path"]) / "src").resolve()
    for f in files:
        raw_name = f.get("filename", "").strip()
        if not raw_name:
            continue
        # Security: block path traversal attempts
        candidate = (base / raw_name).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            logging.warning("Skipping unsafe file path from agent: %s", raw_name)
            continue
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(f["content"], encoding="utf-8")
        written.append(str(candidate.relative_to(base)).replace("\\", "/"))
    return written


def read_project_files(project: dict) -> dict[str, str]:
    """Return {relative_path: content} for all files in src/."""
    result = {}
    src = Path(project["folder_path"]) / "src"
    if not src.exists():
        return result
    for fpath in src.rglob("*"):
        if fpath.is_file():
            try:
                rel = str(fpath.relative_to(src)).replace("\\", "/")
                result[rel] = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    return result

# ── Git operations ────────────────────────────────────────────────────────────

def git_init(folder: str) -> None:
    try:
        subprocess.run(["git", "init"], cwd=folder, capture_output=True, timeout=10)
        # Create .gitignore
        gitignore = Path(folder) / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("__pycache__/\n*.pyc\nnode_modules/\n.env\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=folder, capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=folder, capture_output=True, timeout=10)
    except Exception as e:
        logging.warning("git init failed in %s: %s", folder, e)


def git_commit(folder: str, message: str) -> None:
    try:
        subprocess.run(["git", "add", "-A"], cwd=folder, capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", message], cwd=folder, capture_output=True, timeout=10)
    except Exception as e:
        logging.warning("git commit failed in %s: %s", folder, e)

# ── Devlog ────────────────────────────────────────────────────────────────────

def init_devlog(project: dict) -> None:
    devlog = Path(project["folder_path"]) / "devlog.md"
    devlog.write_text(
        f"# {project['name']} — Development Log\n\n"
        f"Created: {project['created_at']}\n\n"
        f"Description: {project.get('description', '')}\n\n---\n\n",
        encoding="utf-8",
    )


def append_devlog(project: dict, entry: str) -> None:
    devlog = Path(project["folder_path"]) / "devlog.md"
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(devlog, "a", encoding="utf-8") as f:
        f.write(f"### {now}\n\n{entry}\n\n---\n\n")

# ── Context builders ──────────────────────────────────────────────────────────

def build_claude_messages(history: list[dict]) -> list[dict]:
    msgs: list[dict] = []
    ai_pending: list[str] = []
    user_pending: list[str] = []

    def flush_ai():
        if ai_pending:
            msgs.append({"role": "assistant", "content": "\n\n".join(ai_pending)})
            ai_pending.clear()

    def flush_user():
        if user_pending:
            msgs.append({"role": "user", "content": "\n\n".join(user_pending)})
            user_pending.clear()

    for m in history:
        if m["role"] == "user":
            flush_ai()
            user_pending.append(m["content"])
        else:
            flush_user()
            label = AGENT_LABEL.get(m["role"], m["role"])
            ai_pending.append(f"[{label}]: {m['content']}")

    flush_ai()
    flush_user()

    if not msgs or msgs[-1]["role"] != "user":
        return []
    return msgs


def build_ollama_messages(history: list[dict], agent: str, system: str) -> list[dict]:
    msgs = [{"role": "system", "content": system}]
    other_buf: list[str] = []

    def _last_role() -> str:
        for m in reversed(msgs):
            if m["role"] in ("user", "assistant"):
                return m["role"]
        return "system"

    def _append_user(text: str):
        if _last_role() == "user":
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": "user", "content": text})

    def flush_others():
        if other_buf:
            context = "[Group chat context]\n" + "\n\n".join(other_buf)
            _append_user(context)
            other_buf.clear()

    for m in history:
        if m["role"] == "user":
            flush_others()
            _append_user(m["content"])
        elif m["role"] == agent:
            flush_others()
            msgs.append({"role": "assistant", "content": m["content"]})
        else:
            label = AGENT_LABEL.get(m["role"], m["role"])
            other_buf.append(f"[{label}]: {m['content']}")

    flush_others()

    if _last_role() == "assistant":
        msgs.append({"role": "user", "content": "[Your turn to respond.]"})

    return msgs

# ── Claude connectivity ───────────────────────────────────────────────────────

async def check_claude_online() -> bool:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            return r.status_code == 200
    except Exception:
        return False

# ── Mention parser ────────────────────────────────────────────────────────────

def parse_mentions(msg: str, claude_online: bool) -> list[str]:
    lo = msg.lower()
    if "@all" in lo:
        base = ["qwen", "deepseek"]
        return (["claude"] + base) if claude_online else base

    targets: list[str] = []
    if "@claude" in lo and claude_online:
        targets.append("claude")
    if "@deepseek" in lo:
        targets.append("deepseek")
    if "@qwen" in lo:
        targets.append("qwen")

    return targets or (["claude"] if claude_online else ["qwen"])

# ── Streaming ─────────────────────────────────────────────────────────────────

async def stream_claude(history: list[dict], system_prompt: str | None = None,
                        cancel_event: asyncio.Event | None = None,
                        usage: dict | None = None):
    """Stream Claude response; populates usage['input_tokens'] and usage['output_tokens'] if provided."""
    key  = os.getenv("ANTHROPIC_API_KEY", "")
    msgs = build_claude_messages(history)
    if not msgs:
        return
    sys_prompt = system_prompt or SYSTEM_PROMPTS["claude"]
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 4096,
                    "system": sys_prompt,
                    "messages": msgs,
                    "stream": True,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if cancel_event and cancel_event.is_set():
                        return
                    if line.startswith("data: "):
                        try:
                            ev = json.loads(line[6:])
                            etype = ev.get("type", "")
                            if etype == "content_block_delta":
                                text = ev.get("delta", {}).get("text", "")
                                if text:
                                    yield text
                            elif etype == "message_start" and usage is not None:
                                u = ev.get("message", {}).get("usage", {})
                                usage["input_tokens"] = usage.get("input_tokens", 0) + u.get("input_tokens", 0)
                            elif etype == "message_delta" and usage is not None:
                                u = ev.get("usage", {})
                                usage["output_tokens"] = usage.get("output_tokens", 0) + u.get("output_tokens", 0)
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        yield f"\n\n*[Claude error: {e}]*"


async def stream_ollama(agent: str, history: list[dict], system_prompt: str | None = None,
                        cancel_event: asyncio.Event | None = None,
                        usage: dict | None = None):
    """Stream Ollama, filtering <think>...</think>; populates usage dict if provided."""
    model = OLLAMA_MODELS[agent]
    sys_prompt = system_prompt or SYSTEM_PROMPTS[agent]
    msgs  = build_ollama_messages(history, agent, sys_prompt)

    in_think = False
    pending  = ""

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE}/api/chat",
                json={
                    "model":      model,
                    "messages":   msgs,
                    "stream":     True,
                    "keep_alive": KEEP_ALIVE,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if cancel_event and cancel_event.is_set():
                        return
                    if not line:
                        continue
                    try:
                        ev    = json.loads(line)
                        # Capture token counts from final Ollama message
                        if ev.get("done") and usage is not None:
                            usage["input_tokens"]  = usage.get("input_tokens", 0)  + ev.get("prompt_eval_count", 0)
                            usage["output_tokens"] = usage.get("output_tokens", 0) + ev.get("eval_count", 0)
                        chunk = ev.get("message", {}).get("content", "")
                        if not chunk:
                            continue
                        pending += chunk

                        while pending:
                            if in_think:
                                end = pending.find("</think>")
                                if end == -1:
                                    pending = ""
                                    break
                                pending  = pending[end + 8:]
                                in_think = False
                            else:
                                start = pending.find("<think>")
                                if start == -1:
                                    yield pending
                                    pending = ""
                                    break
                                if start > 0:
                                    yield pending[:start]
                                pending  = pending[start + 7:]
                                in_think = True
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        yield f"\n\n*[Ollama error: {e}]*"

# ── Intent detection ──────────────────────────────────────────────────────────

async def _claude_classify(system: str, message: str, max_tokens: int = 30) -> str:
    """Shared low-cost Claude call for intent classification."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": message}]},
            )
        if r.status_code != 200:
            return ""
        return r.json().get("content", [{}])[0].get("text", "").strip()
    except Exception as e:
        logging.warning("_claude_classify error: %s", e)
        return ""


async def detect_intent(message: str) -> dict:
    """Classify a general (non-project-context) message."""
    system = textwrap.dedent("""\
        Classify the user message. Respond ONLY with JSON, no other text.
        - "project_new"      : user wants to BUILD/CREATE a new app, game, website, tool
        - "project_continue" : user wants to ADD/FIX/CHANGE something in an existing build
        - "chat"             : question, discussion, status check, anything else

        If project_new, include name.
        Format: {"type":"project_new","name":"Name"} or {"type":"project_continue"} or {"type":"chat"}
    """)
    raw = await _claude_classify(system, message, max_tokens=80)
    try:
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception:
        return {"type": "chat"}


async def detect_intent_in_project(message: str, project_name: str) -> str:
    """Classify a message sent while a project is active.
    Returns: 'query' | 'build' | 'chat'
    - query : asking about status / progress / what was done
    - build : wants to add/fix/change something (triggers orchestration)
    - chat  : general question unrelated to the project build
    """
    system = textwrap.dedent(f"""\
        User is inside project "{project_name}".
        Classify their message as exactly one word:
        query  — asking about status, progress, what was built, how it works
        build  — wants to add features, fix bugs, change or continue building
        chat   — general question unrelated to building this project
        Respond with ONLY that one word.
    """)
    result = await _claude_classify(system, message, max_tokens=5)
    word = result.lower().strip().rstrip('.')
    return word if word in ("query", "build", "chat") else "chat"


async def stream_project_query(ws: WebSocket, project_id: int, question: str,
                                cancel_event: asyncio.Event | None = None) -> None:
    """Answer a status/question about a project using its devlog + task list."""
    project = get_project(project_id)
    if not project:
        return

    # Build context from devlog + task list
    devlog_path = Path(project["folder_path"]) / "devlog.md"
    devlog = devlog_path.read_text(encoding="utf-8", errors="replace") if devlog_path.exists() else ""
    all_tasks = get_all_tasks(project_id)
    task_lines = "\n".join(
        f"  Task {t['task_number']}: {t['title']} — {t['status']} (→ {t['assigned_to']})"
        for t in all_tasks
    )
    src_files = list(read_project_files(project).keys())
    file_list = ", ".join(src_files) if src_files else "none yet"

    context = (
        f"Project: {project['name']} | Status: {project['status']}\n\n"
        f"Tasks:\n{task_lines or '  (none yet)'}\n\n"
        f"Files created: {file_list}\n\n"
        f"Development log (last 2000 chars):\n{devlog[-2000:]}"
    )

    system = (
        "You are a helpful assistant answering questions about an ongoing development project. "
        "Answer concisely and accurately using the provided context. "
        "If the project is complete, tell the user how to access it."
    )
    history = [{"role": "user", "content": f"{context}\n\nQuestion: {question}"}]

    save_project_message(project_id, "user", question)
    await ws.send_json({"type": "user", "content": question, "timestamp": datetime.utcnow().isoformat()})
    await ws.send_json({"type": "agent_count", "count": 1})
    await ws.send_json({"type": "typing", "agent": "claude"})

    full = ""
    async for chunk in stream_claude(history, system_prompt=system, cancel_event=cancel_event):
        await ws.send_json({"type": "chunk", "agent": "claude", "content": chunk})
        full += chunk

    await ws.send_json({"type": "done", "agent": "claude"})
    if full.strip():
        save_project_message(project_id, "claude", full.strip())

# ── Claude structured calls ──────────────────────────────────────────────────

async def claude_plan_project(project: dict, goal: str) -> list[dict]:
    """Ask Claude to create an atomic task plan. Returns list of task dicts."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return []

    system = textwrap.dedent(f"""\
        You are a senior software architect planning a project for a multi-agent coding system.
        Your job is to break the goal into ATOMIC, INDEPENDENT tasks that local LLMs can execute
        reliably without making integration mistakes.

        AGENT CAPABILITIES:
        - "claude"   : Complex logic, algorithms, full game engines, integration glue code,
                       anything requiring deep reasoning. Use sparingly (costs money).
        - "deepseek" : Individual self-contained files with clear specs. Good at following
                       exact instructions. BAD at cross-file integration.
        - "qwen"     : Config files, HTML structure, simple CSS, documentation only.

        ATOMIC TASK RULES (critical for quality):
        1. Each task creates EXACTLY ONE file (two files max if tightly coupled, e.g. .html + inline css).
        2. Tasks must be INDEPENDENT — no task should import/require code from another task's output.
           If file B needs code from file A, assign both to the SAME task or use window.globals instead of ES imports.
        3. For web projects under ~600 lines: prefer ONE self-contained index.html with ALL CSS and JS inline.
           Only split into multiple files if the project is genuinely large and complex.
        4. The agent writing index.html must also write ALL script/style tags — never reference files
           that a different task will create.
        5. Assign to "claude" if: complex game logic, physics, AI, data structures, algorithms.
           Assign to "deepseek" if: clear isolated file with spec (simple CSS, config, utility).
           Assign to "qwen" only for: plain HTML/CSS structure, README, simple config JSON.

        QUALITY RULES:
        - Max 8 tasks total. Prefer fewer, larger tasks over many tiny ones.
        - Every task description must specify EXACT element IDs, function names, and API contracts
          that other files depend on (so deepseek doesn't guess wrong names).
        - Code must be COMPLETE — no TODOs, no placeholders, no "add logic here" comments.
        - All HTML files: include DOCTYPE, charset UTF-8, viewport meta.

        ENVIRONMENT:
        - Headless Mac Mini, no display. Projects served at http://{SERVER_HOST}/play/<slug>/
        - All asset paths MUST be relative. Never use absolute paths or /path/to/file.
        - Every web project MUST have index.html as entry point.

        Return ONLY a JSON array. No markdown, no explanation, just the array.
        Each task object: {{
          "task_number": int,
          "title": "short title",
          "description": "detailed spec with exact IDs/function names/interfaces the agent must use",
          "assigned_to": "claude"|"deepseek"|"qwen",
          "files_to_create": ["relative/path.ext"]
        }}
    """)

    # Include any accumulated lessons to guide planning
    universal = read_universal_lessons(limit=6)
    proj_lessons = read_project_lessons(project, limit=3)
    lessons_block = ""
    if universal or proj_lessons:
        lessons_block = "\nLEARNED LESSONS (apply these when planning):\n"
        if universal:
            lessons_block += universal + "\n"
        if proj_lessons:
            lessons_block += f"[{project['name']}-specific]\n" + proj_lessons + "\n"

    prompt = (
        f"Project: {project['name']}\n"
        f"Description: {project.get('description', '')}\n"
        f"Goal: {goal}\n"
        f"{lessons_block}\n"
        f"Create the minimal atomic task plan. "
        f"If this is a simple web game/app, use a single task writing one self-contained index.html."
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 2048,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if r.status_code != 200:
                logging.error("claude_plan_project HTTP %d: %s", r.status_code, r.text[:200])
                return []
            data = r.json()
            text = data.get("content", [{}])[0].get("text", "").strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text.strip())
            tasks = json.loads(text)
            # Validate and cap
            if not isinstance(tasks, list):
                logging.error("claude_plan_project returned non-list: %s", text[:200])
                return []
            return tasks[:12]  # hard cap
    except json.JSONDecodeError as e:
        logging.error("claude_plan_project JSON parse error: %s — raw: %s", e, text[:300])
        return []
    except Exception as e:
        logging.error("claude_plan_project error: %s", e)
        return []


async def claude_evaluate_task(project: dict, task: dict, output: str) -> dict:
    """Claude reviews if task output meets requirements."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return {"approved": True, "feedback": ""}

    system = textwrap.dedent("""\
        You evaluate if a coding task was completed correctly.
        Respond ONLY with JSON: {"approved": true/false, "feedback": "brief feedback if not approved"}
        Be lenient — approve if the code is reasonable and addresses the task.
    """)

    prompt = (
        f"Task: {task['title']}\n"
        f"Description: {task['description']}\n"
        f"Files expected: {json.dumps(task.get('files_to_create', []))}\n\n"
        f"Agent output:\n{output[:3000]}"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 200,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if r.status_code != 200:
                return {"approved": True, "feedback": ""}
            data = r.json()
            text = data.get("content", [{}])[0].get("text", "").strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            return json.loads(text)
    except Exception:
        return {"approved": True, "feedback": ""}


async def claude_project_summary(project: dict, goal: str, tasks: list) -> str:
    """Claude writes a summary paragraph about what was built."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return f"Project '{project['name']}' completed with {len(tasks)} tasks."

    task_desc = "\n".join(f"- Task {t['task_number']}: {t['title']} ({t['status']})" for t in tasks)
    slug = project.get("slug", "project")
    prompt = (
        f"Project: {project['name']}\n"
        f"Goal: {goal}\n\n"
        f"Tasks completed:\n{task_desc}\n\n"
        f"Write a brief summary (2-4 sentences) for Paras about what was built and how to use it.\n"
        f"IMPORTANT: Always end your summary with this exact line:\n"
        f"To play/test: open http://{SERVER_HOST}/play/{slug}/ in your browser"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if r.status_code != 200:
                return f"Project '{project['name']}' completed with {len(tasks)} tasks."
            data = r.json()
            return data.get("content", [{}])[0].get("text", "").strip()
    except Exception:
        return f"Project '{project['name']}' completed with {len(tasks)} tasks."

# ── Orchestration loop ────────────────────────────────────────────────────────

async def run_orchestration(ws: WebSocket, project_id: int, goal: str, resume: bool = False,
                            cancel_event: asyncio.Event | None = None) -> None:
    project = get_project(project_id)
    if not project:
        await ws.send_json({"type": "orch_phase", "phase": "error", "msg": "Project not found."})
        return
    stats = OrchStats()

    if resume:
        # ── Resume path: skip planning, pick up incomplete tasks ──────────────
        saved_tasks = get_resumable_tasks(project_id)
        if not saved_tasks:
            await ws.send_json({"type": "orch_phase", "phase": "error", "msg": "No incomplete tasks found. Project may already be complete."})
            return
        # Use the original goal from the first user message
        goal = get_last_project_goal(project_id) or "continue project"
        # Tell client the current full task list so task bar is correct
        all_tasks = get_all_tasks(project_id)
        await ws.send_json({"type": "orch_plan", "tasks": [
            {"id": t["id"], "task_number": t["task_number"], "title": t["title"],
             "assigned_to": t["assigned_to"], "status": t["status"]}
            for t in all_tasks
        ]})
        await ws.send_json({"type": "orch_phase", "phase": "resuming",
                            "msg": f"Resuming — {len(saved_tasks)} task(s) remaining."})
        append_devlog(project, f"## Resumed\nResuming with {len(saved_tasks)} remaining tasks.")
    else:
        # ── Normal path: plan + create tasks ─────────────────────────────────
        save_project_message(project_id, "user", goal)

        # 1. Planning phase
        await ws.send_json({"type": "orch_phase", "phase": "planning", "msg": "Claude is planning the project..."})

        # 2. Get tasks from Claude
        tasks = await claude_plan_project(project, goal)

        # 3. Check for empty
        if not tasks:
            await ws.send_json({"type": "orch_phase", "phase": "error", "msg": "Failed to generate a task plan. Please try again."})
            return

        # 4. Save tasks
        saved_tasks = save_tasks(project_id, tasks)

        # 5. Send plan to client
        task_summary = [
            {"id": t["id"], "task_number": t["task_number"], "title": t["title"],
             "assigned_to": t["assigned_to"], "status": t["status"]}
            for t in saved_tasks
        ]
        await ws.send_json({"type": "orch_plan", "tasks": task_summary})

        # 6. Devlog
        task_list_str = "\n".join(f"{t['task_number']}. {t['title']} (→ {t['assigned_to']})" for t in saved_tasks)
        append_devlog(project, f"## Planning\nGoal: {goal}\n\nTasks:\n{task_list_str}")

    # 7. Execute each task
    for task in saved_tasks:
        if cancel_event and cancel_event.is_set():
            append_devlog(project, "**Cancelled** by user.")
            await ws.send_json({"type": "cancelled"})
            return
        try:
            await _execute_task(ws, project, task, goal, cancel_event=cancel_event, stats=stats)
        except Exception as e:
            logging.error("Task %d failed: %s", task["task_number"], e)
            append_devlog(project, f"**Task {task['task_number']} ERRORED**: {e}")
            update_task(task["id"], status="errored", completed_at=datetime.utcnow().isoformat())
            await ws.send_json({
                "type": "orch_task_done",
                "task_id": task["id"],
                "files": [],
                "error": str(e),
            })

    # 8. Automated test phase (code review + auto-fix)
    if not (cancel_event and cancel_event.is_set()):
        await run_test_phase(ws, project, cancel_event=cancel_event)

    if cancel_event and cancel_event.is_set():
        return

    # 9. Summary
    all_tasks = get_all_tasks(project_id)
    summary = await claude_project_summary(project, goal, all_tasks)

    # 10. Devlog
    append_devlog(project, f"## Summary\n\n{summary}")

    # 11. Final commit
    git_commit(project["folder_path"], "docs: project complete")

    # 12. Update status
    update_project_status(project_id, "completed")

    # 13. Send complete (Open Project button shown by frontend)
    await ws.send_json({"type": "orch_complete", "summary": summary})

    # 14. Send token/time stats
    await ws.send_json({"type": "orch_stats", **stats.to_summary()})


async def _execute_task(ws: WebSocket, project: dict, task: dict, goal: str,
                        cancel_event: asyncio.Event | None = None,
                        stats: OrchStats | None = None) -> None:
    """Execute a single task within the orchestration loop."""
    tid = task["id"]
    tnum = task["task_number"]
    agent = task["assigned_to"]

    # a. Update status
    update_task(tid, status="in_progress")

    # b. Notify client
    await ws.send_json({
        "type": "orch_task_start",
        "task_id": tid,
        "task_number": tnum,
        "title": task["title"],
        "assigned_to": agent,
    })

    # c. Build prompt for worker
    # Only send file context if this task explicitly needs to integrate with existing files.
    # This avoids blowing up the context window on large projects.
    files_to_create = task.get("files_to_create", [])
    existing_files = read_project_files(project)
    file_context = ""
    if existing_files and tnum > 1:
        # Heuristic: include files referenced in the task description or files_to_create,
        # plus index.html (always useful for integration). Cap at 6000 chars total.
        task_text = (task['description'] + " " + " ".join(files_to_create)).lower()
        relevant = {}
        for fpath, fcontent in existing_files.items():
            fname = fpath.lower()
            # Include if filename mentioned in task, or if it's index.html/main entry
            if any(part in task_text for part in [fname, fname.split("/")[-1].replace(".", "")]):
                relevant[fpath] = fcontent
            elif fname in ("index.html", "main.js", "app.js", "game.js", "style.css"):
                relevant[fpath] = fcontent
        if relevant:
            budget = 6000
            file_context = "\n\nRelevant existing files (for reference):\n"
            for fpath, fcontent in relevant.items():
                snippet = fcontent[:min(len(fcontent), budget // max(len(relevant), 1))]
                file_context += f"\n--- {fpath} ---\n{snippet}\n"
                budget -= len(snippet)
                if budget <= 0:
                    file_context += "\n[...truncated for brevity...]\n"
                    break

    worker_system = _build_worker_system(project)

    worker_prompt = (
        f"Task {tnum}: {task['title']}\n\n"
        f"Description:\n{task['description']}\n\n"
        f"Files to create: {json.dumps(files_to_create)}\n"
        f"{file_context}"
    )

    history = [{"role": "user", "content": worker_prompt}]

    # d. Typing indicator
    await ws.send_json({"type": "typing", "agent": agent})

    # e. Stream response
    full = ""
    tok: dict = {}
    if agent == "claude":
        gen = stream_claude(history, system_prompt=worker_system, cancel_event=cancel_event, usage=tok)
    else:
        gen = stream_ollama(agent, history, system_prompt=worker_system, cancel_event=cancel_event, usage=tok)

    async for chunk in gen:
        if cancel_event and cancel_event.is_set():
            break
        await ws.send_json({"type": "chunk", "agent": agent, "content": chunk})
        full += chunk

    # f. Done streaming — record token usage
    await ws.send_json({"type": "done", "agent": agent})
    if stats:
        stats.record(agent,
                     tok.get("input_tokens", len(worker_prompt) // 4),
                     tok.get("output_tokens", len(full) // 4))

    # g. Save to project messages
    save_project_message(project["id"], agent, full.strip(), task_id=tid)

    # h. Extract and write files
    files = extract_files_from_response(full)
    written = write_project_files(project, files) if files else []

    # i. Notify each file
    for fp in written:
        await ws.send_json({"type": "orch_file", "path": fp})

    # j. Git commit
    if written:
        git_commit(project["folder_path"], f"Task {tnum}: {task['title']}")

    # k. Devlog
    files_str = ", ".join(written) if written else "(no files extracted)"
    append_devlog(project, f"**Task {tnum}** ({agent}): {task['title']}\nFiles: {files_str}")

    # l. Evaluate
    evaluation = await claude_evaluate_task(project, task, full)
    if not evaluation.get("approved", True) and evaluation.get("feedback"):
        # ONE retry
        logging.info("Task %d not approved, retrying with feedback", tnum)
        retry_prompt = (
            f"The previous output was reviewed and needs changes:\n"
            f"Feedback: {evaluation['feedback']}\n\n"
            f"Please fix and resubmit. Remember to start each code block's first line with "
            f"// FILE: relative/path/to/file.ext"
        )
        retry_history = [
            {"role": "user", "content": worker_prompt},
            {"role": "assistant", "content": full},
            {"role": "user", "content": retry_prompt},
        ]

        await ws.send_json({"type": "typing", "agent": agent})
        retry_full = ""
        if agent == "claude":
            gen2 = stream_claude(retry_history, system_prompt=worker_system, cancel_event=cancel_event)
        else:
            gen2 = stream_ollama(agent, retry_history, system_prompt=worker_system, cancel_event=cancel_event)

        async for chunk in gen2:
            if cancel_event and cancel_event.is_set():
                break
            await ws.send_json({"type": "chunk", "agent": agent, "content": chunk})
            retry_full += chunk

        await ws.send_json({"type": "done", "agent": agent})
        save_project_message(project["id"], agent, retry_full.strip(), task_id=tid)

        retry_files = extract_files_from_response(retry_full)
        retry_written = write_project_files(project, retry_files) if retry_files else []
        for fp in retry_written:
            await ws.send_json({"type": "orch_file", "path": fp})
        if retry_written:
            git_commit(project["folder_path"], f"Task {tnum} (retry): {task['title']}")
            written = retry_written

    # m. Mark done
    update_task(tid, status="done", completed_at=datetime.utcnow().isoformat())

    # n. Notify client
    await ws.send_json({"type": "orch_task_done", "task_id": tid, "files": written})


def _build_worker_system(project: dict) -> str:
    """Shared system prompt for all worker agents."""
    slug = project.get("slug", "project")
    return textwrap.dedent(f"""\
        You are implementing a specific task for project '{project['name']}'.
        Write clean, production-quality code. Output ONLY file content — no explanations, no preamble.

        FILE MARKER FORMAT (REQUIRED):
        Every file you write must start with a comment on the VERY FIRST LINE:
          JavaScript/TypeScript: // FILE: relative/path/to/file.js
          CSS:                   /* FILE: relative/path/to/file.css */
          HTML:                  <!-- FILE: relative/path/to/file.html -->
          Python:                # FILE: relative/path/to/file.py
        This is how files get saved — if you omit it, nothing gets written.

        QUALITY RULES:
        - Code must be COMPLETE and immediately runnable — NO TODOs, NO placeholders, NO "add logic here".
        - NEVER reference files that you are not creating in this same response.
          If your HTML includes <script src="utils.js">, utils.js must also be in your response.
        - No ES module import/export unless all files use type="module". Default: use window globals.
        - All IDs, class names, and function names must exactly match what the task spec says.
        - For HTML: include <!DOCTYPE html>, <meta charset="UTF-8">, <meta name="viewport">.
        - For games/canvas: include both keyboard AND touch/swipe controls.

        ENVIRONMENT:
        - Headless Mac Mini. No display, no GUI. Users access via browser.
        - Project URL: http://{SERVER_HOST}/play/{slug}/
        - Asset paths MUST be relative (e.g. 'js/game.js' not '/js/game.js').
    """).strip()


async def run_test_phase(ws: WebSocket, project: dict, cancel_event: asyncio.Event | None = None) -> None:
    """Claude reviews all project files for concrete bugs; Claude fixes them if found."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    files = read_project_files(project)
    if not files:
        return

    await ws.send_json({"type": "orch_phase", "phase": "testing",
                        "msg": "🔍 Claude is reviewing the code for bugs..."})

    # Build file inventory for structural checks
    file_list = list(files.keys())
    file_context_parts = []
    budget = 8000
    for path, content in files.items():
        chunk = f"=== {path} ===\n{content}"
        if len(chunk) > budget:
            chunk = chunk[:budget] + "\n[...truncated...]"
        file_context_parts.append(chunk)
        budget -= len(chunk)
        if budget <= 0:
            break
    file_context = "\n\n".join(file_context_parts)

    test_system = textwrap.dedent(f"""\
        You are a code reviewer for a browser web project. Be precise and concrete.
        Files in this project: {json.dumps(file_list)}

        Check ONLY for these CONCRETE, OBJECTIVE bugs (not style suggestions):
        1. HTML <script src="X"> or <link href="X"> where X is NOT in the file list above
        2. JS calls document.getElementById("X") or querySelector("#X")/(".X") where that ID/class
           is NOT present in index.html
        3. Button has onclick="fn()" or addEventListener where fn is never defined in any file
        4. JS uses `import ... from './file'` without type="module" on the script tag in HTML
        5. A variable or function used in file A was supposed to be defined in file B but isn't
        6. Canvas game: game loop never starts (no initial requestAnimationFrame or setInterval call)
        7. Obvious syntax errors (unclosed brackets, undefined variables on first use)

        For each real bug: BUG [file]: <issue> → FIX: <exact fix>
        If everything is correct: respond with exactly "LGTM ✓"
        Do NOT suggest improvements, refactors, or style changes. Only broken things.
    """)

    review_prompt = f"Review this project for the bugs listed:\n\n{file_context}"

    if key:
        # Use Claude for reliable code review
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": "claude-sonnet-4-6", "max_tokens": 1024,
                          "system": test_system,
                          "messages": [{"role": "user", "content": review_prompt}]},
                )
            test_output = r.json().get("content", [{}])[0].get("text", "").strip() if r.status_code == 200 else ""
        except Exception as e:
            logging.warning("test_phase claude error: %s", e)
            test_output = ""
        if test_output:
            await ws.send_json({"type": "chunk", "agent": "claude", "content": test_output})
            await ws.send_json({"type": "done", "agent": "claude"})
            save_project_message(project["id"], "claude", test_output)
    else:
        # Fallback to qwen
        await ws.send_json({"type": "typing", "agent": "qwen"})
        test_output = ""
        async for chunk in stream_ollama("qwen", [{"role": "user", "content": review_prompt}],
                                         system_prompt=test_system, cancel_event=cancel_event):
            if cancel_event and cancel_event.is_set():
                return
            await ws.send_json({"type": "chunk", "agent": "qwen", "content": chunk})
            test_output += chunk
        await ws.send_json({"type": "done", "agent": "qwen"})
        save_project_message(project["id"], "qwen", test_output.strip())

    if not test_output:
        return

    lgtm = "LGTM" in test_output.upper() and "BUG" not in test_output.upper()
    if lgtm:
        await ws.send_json({"type": "orch_phase", "phase": "test_pass",
                            "msg": "✅ Code review passed — no issues found."})
        return

    # Bugs found — Claude fixes them
    await ws.send_json({"type": "orch_phase", "phase": "fixing",
                        "msg": "🔧 Fixing issues found in code review..."})

    fix_system = _build_worker_system(project)
    fix_prompt = (
        f"Code review found these bugs:\n{test_output}\n\n"
        f"Current project files:\n{file_context}\n\n"
        f"Rewrite ONLY the files that need changes to fix all listed bugs. "
        f"Output the complete corrected file content(s) using FILE: markers on the first line."
    )

    await ws.send_json({"type": "typing", "agent": "claude" if key else "deepseek"})
    fix_output = ""
    if key:
        async for chunk in stream_claude([{"role": "user", "content": fix_prompt}],
                                         system_prompt=fix_system, cancel_event=cancel_event):
            if cancel_event and cancel_event.is_set():
                return
            await ws.send_json({"type": "chunk", "agent": "claude", "content": chunk})
            fix_output += chunk
        await ws.send_json({"type": "done", "agent": "claude"})
    else:
        async for chunk in stream_ollama("deepseek", [{"role": "user", "content": fix_prompt}],
                                          system_prompt=fix_system, cancel_event=cancel_event):
            if cancel_event and cancel_event.is_set():
                return
            await ws.send_json({"type": "chunk", "agent": "deepseek", "content": chunk})
            fix_output += chunk
        await ws.send_json({"type": "done", "agent": "deepseek"})

    save_project_message(project["id"], "claude" if key else "deepseek", fix_output.strip())
    fixed = write_project_files(project, extract_files_from_response(fix_output))
    for fp in fixed:
        await ws.send_json({"type": "orch_file", "path": fp})
    if fixed:
        git_commit(project["folder_path"], "fix: code review auto-fixes")
    append_devlog(project, f"## Code Review Fix\nBugs fixed in: {', '.join(fixed) or 'none'}")
    await ws.send_json({"type": "orch_phase", "phase": "test_fixed",
                        "msg": f"✅ Fixed {len(fixed)} file(s) — project is ready."})


async def run_fix_task(ws: WebSocket, project_id: int, feedback: str,
                       cancel_event: asyncio.Event | None = None) -> None:
    """Apply targeted fix / add feature based on user feedback on a completed project."""
    project = get_project(project_id)
    if not project:
        return

    key = os.getenv("ANTHROPIC_API_KEY", "")
    files = read_project_files(project)

    # Build file context, capped to avoid huge prompts
    context_parts, budget = [], 8000
    for path, content in files.items():
        part = f"=== {path} ===\n{content}"
        context_parts.append(part[:budget])
        budget -= len(part)
        if budget <= 0:
            break
    file_context = "\n\n".join(context_parts)

    fix_system = _build_worker_system(project)
    fix_prompt = (
        f"User feedback / request:\n{feedback}\n\n"
        f"Current project files:\n{file_context}\n\n"
        f"Analyze what needs to change, then output the COMPLETE corrected file(s) using FILE: markers. "
        f"Only output files that actually need to change. Make sure every fix is complete and working."
    )

    save_project_message(project_id, "user", feedback)
    await ws.send_json({"type": "user", "content": feedback,
                        "timestamp": datetime.utcnow().isoformat()})
    await ws.send_json({"type": "agent_count", "count": 1})

    fix_stats = OrchStats()
    full = ""
    tok: dict = {}
    agent_used = "claude" if key else "deepseek"
    await ws.send_json({"type": "typing", "agent": agent_used})

    if key:
        async for chunk in stream_claude([{"role": "user", "content": fix_prompt}],
                                          system_prompt=fix_system, cancel_event=cancel_event, usage=tok):
            if cancel_event and cancel_event.is_set():
                await ws.send_json({"type": "cancelled"})
                return
            await ws.send_json({"type": "chunk", "agent": "claude", "content": chunk})
            full += chunk
    else:
        async for chunk in stream_ollama("deepseek", [{"role": "user", "content": fix_prompt}],
                                          system_prompt=fix_system, cancel_event=cancel_event, usage=tok):
            if cancel_event and cancel_event.is_set():
                await ws.send_json({"type": "cancelled"})
                return
            await ws.send_json({"type": "chunk", "agent": "deepseek", "content": chunk})
            full += chunk

    await ws.send_json({"type": "done", "agent": agent_used})
    fix_stats.record(agent_used,
                     tok.get("input_tokens", len(fix_prompt) // 4),
                     tok.get("output_tokens", len(full) // 4))
    save_project_message(project_id, agent_used, full.strip())

    fixed = write_project_files(project, extract_files_from_response(full))
    for fp in fixed:
        await ws.send_json({"type": "orch_file", "path": fp})
    if fixed:
        git_commit(project["folder_path"], f"fix: {feedback[:60]}")
    append_devlog(project, f"## Fix\nFeedback: {feedback}\nFiles updated: {', '.join(fixed) or 'none'}")

    # Extract and save a lesson from this fix
    fix_summary = f"Fixed files: {', '.join(fixed)}. Agent: {agent_used}."
    lesson = await extract_and_save_lesson(project, feedback, fix_summary)

    slug = project.get("slug", "")
    await ws.send_json({"type": "fix_complete", "files_fixed": fixed,
                        "project_slug": slug, "lesson": lesson})
    await ws.send_json({"type": "orch_stats", **fix_stats.to_summary()})


# ── System stats ─────────────────────────────────────────────────────────────

_net_snap: dict = {}

def _init_net_snap() -> None:
    c = psutil.net_io_counters()
    _net_snap["sent"]  = c.bytes_sent
    _net_snap["recv"]  = c.bytes_recv
    _net_snap["ts"]    = time.monotonic()

_init_net_snap()

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI()


@app.on_event("startup")
async def startup():
    init_db()
    PROJECTS_DIR.mkdir(exist_ok=True)


app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


# ── Serve project web apps ───────────────────────────────────────────────────

def _auto_generate_index(src_dir: Path, slug: str) -> str | None:
    """Generate a minimal index.html from whatever files exist in src_dir.
    Returns HTML string, or None if src_dir is completely empty."""
    js_files  = sorted(p.relative_to(src_dir).as_posix()
                       for p in src_dir.rglob("*.js")  if p.is_file())
    css_files = sorted(p.relative_to(src_dir).as_posix()
                       for p in src_dir.rglob("*.css") if p.is_file())

    if not js_files and not css_files:
        return None   # Nothing to load

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
    """Serve project files from projects/<slug>/src/.
    GET /play/car-racing-game/       -> src/index.html
    GET /play/car-racing-game/js/game.js -> src/js/game.js
    """
    parts = slug.strip("/").split("/", 1)
    project_slug = parts[0]
    file_path = parts[1] if len(parts) > 1 else ""

    if not file_path or file_path.endswith("/"):
        file_path = file_path + "index.html" if file_path else "index.html"

    src_dir = PROJECTS_DIR / project_slug / "src"
    target = (src_dir / file_path).resolve()

    # Security: ensure resolved path is inside src_dir
    try:
        target.relative_to(src_dir.resolve())
    except ValueError:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not target.is_file():
        # Auto-generate index.html if it's missing but other files exist
        if file_path == "index.html" and src_dir.exists():
            generated = _auto_generate_index(src_dir, project_slug)
            if generated:
                target.write_text(generated, encoding="utf-8")
                return HTMLResponse(generated)
        from fastapi.responses import JSONResponse
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


@app.get("/status")
async def status():
    return {"claude_online": await check_claude_online()}


@app.get("/settings")
async def get_settings():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    return {
        "claude": {
            "api_key_set": bool(key),
            "api_key_preview": f"sk-ant-...{key[-4:]}" if len(key) > 8 else "",
        },
        "deepseek": {
            "model": OLLAMA_MODELS["deepseek"],
            "keep_alive": KEEP_ALIVE,
            "speed": "40.9 tok/s",
            "size": "11.1 GB",
            "quant": "Q5_K_S",
        },
        "qwen": {
            "model": OLLAMA_MODELS["qwen"],
            "keep_alive": KEEP_ALIVE,
            "speed": "13.3 tok/s",
            "size": "6.6 GB",
            "quant": "Q4_K_M",
        },
    }


@app.post("/settings/apikey")
async def save_apikey(data: dict):
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
        "cpu":        round(cpu, 1),
        "ram_used":   round(mem.used  / 1073741824, 1),
        "ram_total":  round(mem.total / 1073741824, 1),
        "swap_used":  round(swap.used  / 1073741824, 2),
        "swap_total": round(swap.total / 1073741824, 1),
        "rx_kbs":     round(rx_kbs, 1),
        "tx_kbs":     round(tx_kbs, 1),
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
    return read_project_files(p)


@app.delete("/projects/{project_id}")
async def api_delete_project(project_id: int):
    p = get_project(project_id)
    if not p:
        return {"error": "Not found"}

    # Delete folder from disk
    folder = Path(p["folder_path"])
    if folder.exists():
        import shutil
        shutil.rmtree(folder, ignore_errors=True)

    # Delete all DB records for this project
    with sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM project_messages WHERE project_id = ?", (project_id,))
        c.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        c.execute("DELETE FROM projects WHERE id = ?", (project_id,))

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
        """Run coro as a cancellable task; watch recv_q for cancel messages."""
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
                        await recv_q.put(side)  # re-queue so outer loop sees it
                    else:
                        await recv_q.put(side)  # re-queue for later processing
                except asyncio.TimeoutError:
                    pass
            await active_task
        except asyncio.CancelledError:
            pass
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

            # ── Cancel ────────────────────────────────────────────────
            if msg_type == "cancel":
                if active_task and not active_task.done():
                    cancel_event.set()
                    active_task.cancel()
                else:
                    await ws.send_json({"type": "cancelled"})
                continue

            # ── Project list ──────────────────────────────────────────
            if msg_type == "get_projects":
                await ws.send_json({"type": "project_list", "projects": list_projects()})
                continue

            # ── Load project ──────────────────────────────────────────
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

            # ── Start orchestration ───────────────────────────────────
            if msg_type == "start_orchestration":
                pid  = data.get("project_id")
                goal = data.get("goal", "")
                if pid and goal:
                    await _run_task(run_orchestration(ws, pid, goal, cancel_event=cancel_event))
                    if cancel_event.is_set():
                        reset_stuck_tasks(pid)
                        await ws.send_json({"type": "cancelled"})
                continue

            # ── Resume orchestration ──────────────────────────────────
            if msg_type == "resume_orchestration":
                pid = data.get("project_id")
                if pid:
                    await _run_task(run_orchestration(ws, pid, goal="", resume=True, cancel_event=cancel_event))
                    if cancel_event.is_set():
                        reset_stuck_tasks(pid)
                        await ws.send_json({"type": "cancelled"})
                continue

            # ── Fix project (bug report / feature on completed project) ──
            if msg_type == "fix_project":
                pid      = data.get("project_id")
                feedback = data.get("feedback", "").strip()
                if pid and feedback:
                    await _run_task(run_fix_task(ws, pid, feedback, cancel_event))
                    if cancel_event.is_set():
                        await ws.send_json({"type": "cancelled"})
                continue

            # ── Content message (chat or project query/build) ─────────
            content    = data.get("content", "").strip()
            project_id_ctx = data.get("project_id")   # set when client is in project mode
            if not content:
                continue

            claude_online = await check_claude_online()

            # In project context: smart routing
            if project_id_ctx:
                proj = get_project(project_id_ctx)
                if proj and claude_online:
                    routing = await detect_intent_in_project(content, proj["name"])
                else:
                    routing = "chat"

                if routing == "query":
                    # Answer the question using project context
                    await _run_task(stream_project_query(ws, project_id_ctx, content, cancel_event))
                    if cancel_event.is_set():
                        await ws.send_json({"type": "cancelled"})
                    continue

                if routing == "build":
                    prior_msgs = load_project_messages(project_id_ctx, limit=1)
                    prior_tasks = get_all_tasks(project_id_ctx)
                    if not prior_msgs and not prior_tasks:
                        # Fresh project — initial spec, start immediately
                        await _run_task(run_orchestration(ws, project_id_ctx, content, cancel_event=cancel_event))
                        if cancel_event.is_set():
                            reset_stuck_tasks(project_id_ctx)
                            await ws.send_json({"type": "cancelled"})
                    elif proj.get("status") == "completed":
                        # Completed project — treat as bug fix / feature request
                        await _run_task(run_fix_task(ws, project_id_ctx, content, cancel_event))
                        if cancel_event.is_set():
                            await ws.send_json({"type": "cancelled"})
                    else:
                        # In-progress project — ask to confirm continue
                        await ws.send_json({
                            "type":             "intent_detected",
                            "intent":           "project_continue",
                            "original_message": content,
                            "project_id":       project_id_ctx,
                        })
                    continue

                # routing == "chat" — fall through to normal chat below

            # General intent detection (no project context)
            elif claude_online:
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

            # ── Normal chat flow ──────────────────────────────────────
            save_message("user", content)
            await ws.send_json({
                "type":      "user",
                "content":   content,
                "timestamp": datetime.utcnow().isoformat(),
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
