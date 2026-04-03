"""File I/O, Git operations, and devlog helpers."""
from __future__ import annotations
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


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
        if filename.startswith('src/'):
            filename = filename[4:]
        if not filename or filename in seen:
            continue

        rest = content[m.end():]

        cb = re.match(r'[ \t]*\n{0,2}[ \t]*```[\w\-]*[ \t]*\n(.*?)```', rest, re.DOTALL)
        if cb:
            code = cb.group(1).rstrip()
        else:
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

    if files:
        return files

    # ── Fallback: bare ```html / ```javascript block ─────────────────────────
    lang_map = {"html": "index.html", "javascript": "js/main.js", "js": "js/main.js"}
    bare_re = re.compile(r'```(html|javascript|js)[ \t]*\n(.*?)```', re.DOTALL | re.IGNORECASE)
    for m in bare_re.finditer(content):
        lang = m.group(1).lower()
        code = m.group(2).rstrip()
        if not code:
            continue
        filename = lang_map[lang]
        if filename not in seen:
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


def list_project_files(project: dict) -> list[str]:
    """Return relative paths of all files in src/ without reading their contents."""
    src = Path(project["folder_path"]) / "src"
    if not src.exists():
        return []
    result = []
    for fpath in src.rglob("*"):
        if fpath.is_file():
            try:
                result.append(str(fpath.relative_to(src)).replace("\\", "/"))
            except Exception:
                pass
    return result


def read_project_files(project: dict, paths: list[str] | None = None) -> dict[str, str]:
    """Return {relative_path: content} for files in src/.
    If `paths` is given, only those files are read."""
    result = {}
    src = Path(project["folder_path"]) / "src"
    if not src.exists():
        return result
    candidates = (
        ((src / p) for p in paths)
        if paths is not None
        else src.rglob("*")
    )
    for fpath in candidates:
        fpath = Path(fpath)
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
        gitignore = Path(folder) / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("__pycache__/\n*.pyc\nnode_modules/\n.env\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=folder, capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=folder, capture_output=True, timeout=10)
    except FileNotFoundError:
        logging.critical("git not found — version control disabled. Install git on this machine.")
    except Exception as e:
        logging.warning("git init failed in %s: %s", folder, e)


def git_commit(folder: str, message: str) -> None:
    """Commit all changes. Git errors are logged and written to the project devlog."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=folder, capture_output=True, timeout=10)
        result = subprocess.run(["git", "commit", "-m", message], cwd=folder,
                                capture_output=True, timeout=10, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            logging.warning("git commit non-zero in %s: %s", folder, result.stderr.strip())
            devlog = Path(folder) / "devlog.md"
            if devlog.exists():
                with open(devlog, "a", encoding="utf-8") as f:
                    f.write(f"\n> ⚠️ git commit failed: {result.stderr.strip()}\n\n")
    except FileNotFoundError:
        logging.critical("git not found — commit skipped for %s", folder)
    except Exception as e:
        logging.warning("git commit failed in %s: %s", folder, e)
        devlog = Path(folder) / "devlog.md"
        if devlog.exists():
            with open(devlog, "a", encoding="utf-8") as f:
                f.write(f"\n> ⚠️ git commit exception: {e}\n\n")


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
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(devlog, "a", encoding="utf-8") as f:
        f.write(f"### {now}\n\n{entry}\n\n---\n\n")
