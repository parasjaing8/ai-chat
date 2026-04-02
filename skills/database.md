---
name: Database
description: SQLite schema design, queries, and data modeling patterns
keywords: database, sqlite, sql, query, schema, table, insert, select, update, delete, migration, index, join, transaction, db, data model, orm
---

## Database Skill

### Project's Database (chat.db)
SQLite at `DB_PATH = Path(__file__).parent / "chat.db"`. Tables:
- `messages` — general chat history
- `projects` — project metadata
- `project_messages` — per-project chat history
- `tasks` — orchestration task records

### Safe SQLite Pattern (always use context manager)
```python
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "chat.db"

def get_item(item_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT id, name, status FROM items WHERE id=?",
            (item_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "status": row[2]}

def create_item(name: str) -> dict:
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT INTO items (name, created_at) VALUES (?,?)", (name, now))
        item_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": item_id, "name": name}
```

### Schema Creation Pattern
```python
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                status     TEXT DEFAULT 'active',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(status)")
```

### Rules
- Always use parameterized queries (`?` placeholders) — never f-strings with user data
- `with sqlite3.connect(DB_PATH) as c:` auto-commits and closes the connection
- For bulk inserts, use `executemany()` inside one `with` block
- Keep queries simple — avoid complex JOINs; Python can do the merging
