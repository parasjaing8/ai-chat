"""Database layer — SQLite operations for messages, projects, and tasks."""
from __future__ import annotations
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from files_io import init_devlog, git_init

# These are set by server.py at import time via configure()
DB_PATH: Path = Path("chat.db")
PROJECTS_DIR: Path = Path("projects")
DISPLAY_LEN: int = 60


def configure(*, db_path: Path, projects_dir: Path, display_len: int) -> None:
    """Called once from server.py to inject path configuration."""
    global DB_PATH, PROJECTS_DIR, DISPLAY_LEN
    DB_PATH = db_path
    PROJECTS_DIR = projects_dir
    DISPLAY_LEN = display_len


def _db_connect() -> sqlite3.Connection:
    """Return a SQLite connection with WAL mode, timeout, and optimised sync."""
    conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    with _db_connect() as c:
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        row = c.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
    _run_migrations(current)


_MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "add depends_on column to tasks",
        "ALTER TABLE tasks ADD COLUMN depends_on TEXT DEFAULT '[]';",
    ),
]


def _run_migrations(current_version: int) -> None:
    """Apply any pending migrations sequentially."""
    for version, description, sql in _MIGRATIONS:
        if version <= current_version:
            continue
        logging.info("DB migration %d: %s", version, description)
        with _db_connect() as c:
            c.executescript(sql)
            c.execute("DELETE FROM schema_version")
            c.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def save_message(role: str, content: str) -> None:
    with _db_connect() as c:
        c.execute(
            "INSERT INTO messages (role, content, timestamp) VALUES (?,?,?)",
            (role, content, datetime.now(timezone.utc).isoformat()),
        )


def load_history(limit: int | None = None) -> list[dict]:
    if limit is None:
        limit = DISPLAY_LEN
    with _db_connect() as c:
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
    now = datetime.now(timezone.utc).isoformat()
    folder = str(PROJECTS_DIR / slug)

    base_slug = slug
    counter = 1
    while True:
        try:
            with _db_connect() as c:
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
    with _db_connect() as c:
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
    with _db_connect() as c:
        rows = c.execute(
            "SELECT id, name, slug, description, folder_path, status, created_at, updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {"id": r[0], "name": r[1], "slug": r[2], "description": r[3],
         "folder_path": r[4], "status": r[5], "created_at": r[6], "updated_at": r[7]}
        for r in rows
    ]


def update_project_status(project_id: int, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db_connect() as c:
        c.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?", (status, now, project_id))


def save_project_message(project_id: int, role: str, content: str, task_id: int | None = None) -> None:
    with _db_connect() as c:
        c.execute(
            "INSERT INTO project_messages (project_id, role, content, task_id, timestamp) VALUES (?,?,?,?,?)",
            (project_id, role, content, task_id, datetime.now(timezone.utc).isoformat()),
        )


def load_project_messages(project_id: int, limit: int = 30) -> list[dict]:
    with _db_connect() as c:
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
    now = datetime.now(timezone.utc).isoformat()
    result = []
    with _db_connect() as c:
        for t in tasks:
            c.execute(
                "INSERT INTO tasks (project_id, task_number, title, description, assigned_to, status, files_to_create, created_at, depends_on) VALUES (?,?,?,?,?,?,?,?,?)",
                (project_id, t["task_number"], t["title"], t["description"],
                 t["assigned_to"], "pending", json.dumps(t.get("files_to_create", [])), now,
                 json.dumps(t.get("depends_on", []))),
            )
            tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            result.append({
                "id": tid, "project_id": project_id, "task_number": t["task_number"],
                "title": t["title"], "description": t["description"],
                "assigned_to": t["assigned_to"], "status": "pending",
                "files_to_create": t.get("files_to_create", []),
                "depends_on": t.get("depends_on", []),
                "created_at": now, "completed_at": None,
            })
    return result


def get_pending_tasks(project_id: int) -> list[dict]:
    with _db_connect() as c:
        rows = c.execute(
            "SELECT id, task_number, title, description, assigned_to, status, files_to_create, output_result, created_at, completed_at, depends_on FROM tasks WHERE project_id=? AND status='pending' ORDER BY task_number",
            (project_id,),
        ).fetchall()
    return [_task_row_to_dict(r) for r in rows]


def reset_stuck_tasks(project_id: int) -> int:
    """Reset any in_progress tasks back to pending. Returns count reset."""
    with _db_connect() as c:
        c.execute(
            "UPDATE tasks SET status='pending' WHERE project_id=? AND status='in_progress'",
            (project_id,),
        )
        return c.execute("SELECT changes()").fetchone()[0]


def get_resumable_tasks(project_id: int) -> list[dict]:
    """Return pending tasks for a project (for resume). Resets stuck in_progress first."""
    reset_stuck_tasks(project_id)
    with _db_connect() as c:
        rows = c.execute(
            "SELECT id, task_number, title, description, assigned_to, status, files_to_create, output_result, created_at, completed_at, depends_on FROM tasks WHERE project_id=? AND status != 'done' ORDER BY task_number",
            (project_id,),
        ).fetchall()
    return [_task_row_to_dict(r) for r in rows]


def get_last_project_goal(project_id: int) -> str:
    """Retrieve the first user message in a project (the original goal)."""
    with _db_connect() as c:
        row = c.execute(
            "SELECT content FROM project_messages WHERE project_id=? AND role='user' ORDER BY id ASC LIMIT 1",
            (project_id,),
        ).fetchone()
    return row[0] if row else ""


def get_all_tasks(project_id: int) -> list[dict]:
    with _db_connect() as c:
        rows = c.execute(
            "SELECT id, task_number, title, description, assigned_to, status, files_to_create, output_result, created_at, completed_at, depends_on FROM tasks WHERE project_id=? ORDER BY task_number",
            (project_id,),
        ).fetchall()
    return [_task_row_to_dict(r) for r in rows]


def _task_row_to_dict(r) -> dict:
    try:
        ftc = json.loads(r[6])
    except (json.JSONDecodeError, TypeError):
        ftc = []
    try:
        dep = json.loads(r[10]) if r[10] is not None else []
    except (json.JSONDecodeError, TypeError):
        dep = []
    return {
        "id": r[0], "task_number": r[1], "title": r[2], "description": r[3],
        "assigned_to": r[4], "status": r[5], "files_to_create": ftc,
        "output_result": r[7], "created_at": r[8], "completed_at": r[9],
        "depends_on": dep,
    }


ALLOWED_TASK_COLS = {"status", "output_result", "completed_at"}

def update_task(task_id: int, **kwargs) -> None:
    if not kwargs:
        return
    invalid = set(kwargs) - ALLOWED_TASK_COLS
    if invalid:
        raise ValueError(f"update_task: disallowed column(s): {invalid}")
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(task_id)
    with _db_connect() as c:
        c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
