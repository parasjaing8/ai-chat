"""Orchestration — intent detection, planning, evaluation, execution, memory/lessons."""
from __future__ import annotations
import asyncio
import json
import logging
import re
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from db import (
    create_project, get_all_tasks, get_last_project_goal, get_project,
    get_resumable_tasks, list_projects, load_history, load_project_messages,
    reset_stuck_tasks, save_message, save_project_message, save_tasks,
    update_project_status, update_task,
)
from files_io import (
    append_devlog, extract_files_from_response, git_commit,
    list_project_files, read_project_files, write_project_files,
)
from models import (
    OrchStats, build_claude_messages, check_claude_online,
    master_json_call, master_text_call, parse_mentions,
    stream_claude, stream_master, stream_ollama,
)
from skills_mod import load_skills


# These are injected by server.py via configure()
SERVER_HOST: str = "192.168.0.130:8080"
MEMORY_DIR: Path = Path("memory")
CONTEXT_LEN: int = 20

# Callbacks injected from server.py
_get_master_model = lambda: "qwen"
_is_claude_available = lambda: False
_get_enabled_agents = lambda: ["deepseek", "qwen"]
_get_config = lambda: {}


def configure(*, server_host: str, memory_dir: Path, context_len: int,
              get_master_model, is_claude_available, get_enabled_agents,
              get_config) -> None:
    global SERVER_HOST, MEMORY_DIR, CONTEXT_LEN
    global _get_master_model, _is_claude_available, _get_enabled_agents, _get_config
    SERVER_HOST = server_host
    MEMORY_DIR = memory_dir
    CONTEXT_LEN = context_len
    _get_master_model = get_master_model
    _is_claude_available = is_claude_available
    _get_enabled_agents = get_enabled_agents
    _get_config = get_config


# ── Memory system ─────────────────────────────────────────────────────────────

def _universal_lessons_path() -> Path:
    return MEMORY_DIR / "universal_lessons.md"


def _lessons_path(project: dict) -> Path:
    return Path(project["folder_path"]) / "lessons.md"


def read_universal_lessons(limit: int = 8) -> str:
    p = _universal_lessons_path()
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    lessons = [l for l in lines if l.startswith("- ")]
    return "\n".join(lessons[-limit:])


def read_project_lessons(project: dict, limit: int = 5) -> str:
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
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = f"- [{now}] [{project['name']}] {lesson.strip()}\n"
    p = _lessons_path(project)
    with open(p, "a", encoding="utf-8") as f:
        f.write(entry)
    if universal:
        with open(_universal_lessons_path(), "a", encoding="utf-8") as f:
            f.write(entry)


async def extract_and_save_lesson(project: dict, feedback: str, fix_summary: str) -> str | None:
    system = (
        "You extract concise coding lessons from bug fixes. "
        "Respond with ONLY the lesson (max 20 words), starting with a verb. "
        "Example: 'Always initialize canvas game loop with requestAnimationFrame, not setInterval.'"
    )
    prompt = (
        f"A bug was fixed in project '{project['name']}'.\n"
        f"User feedback: {feedback}\n"
        f"What was fixed: {fix_summary[:500]}\n\n"
        f"Write ONE concise lesson (max 20 words) to avoid this bug in future projects. "
        f"Respond with ONLY the lesson, nothing else."
    )
    try:
        lesson = await master_text_call(system, prompt, max_tokens=80)
        if not lesson:
            return None
        count = _count_project_lessons(project)
        universal = (count % 3 == 0)
        append_lesson(project, lesson, universal=universal)
        return lesson
    except Exception as e:
        logging.warning("extract_lesson failed: %s", e)
        return None


# ── Intent detection ──────────────────────────────────────────────────────────

async def _claude_classify(system: str, message: str, max_tokens: int = 30) -> str:
    return await master_json_call(system, message, max_tokens) or ""


async def detect_intent(message: str) -> dict:
    system = textwrap.dedent("""\
        Classify the user message. Respond ONLY with JSON, no other text.
        - "project_new"      : user wants to BUILD/CREATE a new app, game, website, tool
        - "project_continue" : user wants to ADD/FIX/CHANGE something in an existing build
        - "chat"             : question, discussion, status check, anything else

        If project_new, include name.
        Format: {"type":"project_new","name":"Name"} or {"type":"project_continue"} or {"type":"chat"}
    """)
    raw = await _claude_classify(system, message, max_tokens=80)
    if not raw:
        return {"type": "chat"}
    try:
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception:
        return {"type": "chat"}


async def detect_intent_in_project(message: str, project_name: str) -> str:
    system = textwrap.dedent(f"""\
        User is inside project "{project_name}".
        Classify their message as exactly one word:
        query  — asking about status, progress, what was built, how it works
        build  — wants to add features, fix bugs, change or continue building
        chat   — general question unrelated to building this project
        Respond with ONLY that one word.
    """)
    result = await _claude_classify(system, message, max_tokens=20)
    result_lower = result.lower()
    if "build" in result_lower:
        return "build"
    if "query" in result_lower:
        return "query"
    return "chat"


async def stream_project_query(ws: WebSocket, project_id: int, question: str,
                                cancel_event: asyncio.Event | None = None) -> None:
    project = get_project(project_id)
    if not project:
        return

    devlog_path = Path(project["folder_path"]) / "devlog.md"
    devlog = devlog_path.read_text(encoding="utf-8", errors="replace") if devlog_path.exists() else ""
    all_tasks = get_all_tasks(project_id)
    task_lines = "\n".join(
        f"  Task {t['task_number']}: {t['title']} — {t['status']} (→ {t['assigned_to']})"
        for t in all_tasks
    )
    src_files = list_project_files(project)
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

    master = _get_master_model()
    save_project_message(project_id, "user", question)
    await ws.send_json({"type": "user", "content": question, "timestamp": datetime.now(timezone.utc).isoformat()})
    await ws.send_json({"type": "agent_count", "count": 1})
    await ws.send_json({"type": "typing", "agent": master})

    full = ""
    async for chunk in stream_master(history, system_prompt=system, cancel_event=cancel_event):
        await ws.send_json({"type": "chunk", "agent": master, "content": chunk})
        full += chunk

    await ws.send_json({"type": "done", "agent": master})
    if full.strip():
        save_project_message(project_id, master, full.strip())


# ── Claude structured calls ──────────────────────────────────────────────────

async def claude_plan_project(project: dict, goal: str) -> list[dict]:
    system = textwrap.dedent(f"""\
        You are a senior software architect planning a project for a multi-agent coding system.
        Your job is to break the goal into ATOMIC, INDEPENDENT tasks that local LLMs can execute
        reliably without making integration mistakes.

        AGENT ROLES — READ CAREFULLY:
        CURRENTLY ENABLED AGENTS: {", ".join(_get_enabled_agents())}
        Only assign tasks to agents in the enabled list above.

        - "deepseek" : PRIMARY CODER. Assign ALL files with code to deepseek — every .html, .js,
                       .css, .py, .json with logic. DeepSeek is fast and reliable for code.
        - "qwen"     : Use ONLY for pure documentation (README.md). Never assign .html, .js, .css,
                       or any code file to qwen. If you are qwen, do NOT assign tasks to yourself
                       unless deepseek is not in the enabled list.
        - "claude"   : NEVER assign tasks to claude. Claude is planner/orchestrator only.

        ATOMIC TASK RULES (critical for quality):
        1. Each task produces at most ONE file, max ~250 lines of output.
           If a file will be longer, split it: e.g. game-engine.js + game-ui.js.
        2. Tasks must be INDEPENDENT — no task should import/require code from another task's output.
           Use window.globals instead of ES imports across files.
        3. The agent writing index.html must include ALL <script> and <link> tags for files other
           tasks will create — reference them by their relative paths.
        4. For a game or interactive app, always split into at least these tasks:
           a) index.html + CSS (deepseek) — full markup, styles, canvas, touch buttons
           b) game constants + data (deepseek) — shapes, colors, scoring tables
           c) game logic / mechanics (deepseek) — board state, collision, line clearing
           d) rendering (deepseek) — all draw functions
           e) input handling + game loop (deepseek) — keyboard, touch, requestAnimationFrame
        5. Each task description must specify EXACT element IDs, function names, and window.globals
           so deepseek doesn't guess wrong names between files.

        QUALITY RULES:
        - A 400-line Tetris written in 6 focused tasks beats a 1000-line single task that truncates.
        - Code must be COMPLETE — no TODOs, no placeholders, no "add logic here" comments.
        - All HTML files: include DOCTYPE, charset UTF-8, viewport meta.
        - Games must support both keyboard AND touch controls.

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
          "files_to_create": ["relative/path.ext"],
          "depends_on": []
        }}

        PARALLELISM FIELD — "depends_on":
        List the task_numbers that MUST complete before this task starts.
        Leave it empty ([]) if the task is fully independent and can run in parallel with others.
        Example: if task 3 needs index.html from task 1, set "depends_on": [1].
        Tasks in the same dependency wave will execute concurrently — design tasks to be
        independent whenever possible to maximise parallelism.
    """)

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
        f"Create the atomic task plan. "
        f"If this is a simple web game/app, use a single task writing one self-contained index.html. "
        f"For complex projects, use as many tasks as needed — each small and unambiguous."
    )

    text = ""
    try:
        text = await master_json_call(system, prompt, max_tokens=4096)
        if not text:
            logging.error("claude_plan_project: master model returned empty response")
            return []
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text.strip())
        tasks = json.loads(text)
        if not isinstance(tasks, list):
            logging.error("claude_plan_project returned non-list: %s", text[:200])
            return []
        return tasks[:100]
    except json.JSONDecodeError as e:
        logging.error("claude_plan_project JSON parse error: %s — raw: %s", e, text[:300])
        return []
    except Exception as e:
        logging.error("claude_plan_project error: %s", e)
        return []


async def claude_evaluate_task(project: dict, task: dict, output: str) -> dict:
    files_to_create = task.get("files_to_create") or []
    has_file_marker = bool(re.search(r'FILE:', output))
    extracted = extract_files_from_response(output)
    extracted_paths = {f["path"] if "path" in f else f.get("filename", "") for f in extracted}

    if not has_file_marker and not extracted:
        return {"approved": False, "feedback": "No FILE: markers found in output — no files were produced."}

    if files_to_create:
        missing = [f for f in files_to_create if f not in extracted_paths]
        if missing:
            return {"approved": False, "feedback": f"Expected files not produced: {missing}"}

    for f in extracted:
        fname = f.get("path", f.get("filename", ""))
        content = f.get("content", "")
        if fname.endswith((".html", ".htm")) and "<html" not in content.lower():
            return {"approved": False, "feedback": f"HTML file {fname} missing <html> tag — may be incomplete."}

    system = textwrap.dedent("""\
        You evaluate if a coding task was completed correctly.
        Respond ONLY with JSON: {"approved": true/false, "feedback": "brief feedback if not approved"}
        Be lenient — approve if the code is reasonable and addresses the task.
    """)
    prompt = (
        f"Task: {task['title']}\n"
        f"Description: {task['description']}\n"
        f"Files expected: {json.dumps(files_to_create)}\n\n"
        f"Agent output:\n{output[:3000]}"
    )
    try:
        text = await master_json_call(system, prompt, max_tokens=200)
        if not text:
            return {"approved": False, "feedback": "Evaluator returned empty response."}
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception:
        return {"approved": False, "feedback": "Evaluation failed — marking for retry."}


async def claude_project_summary(project: dict, goal: str, tasks: list) -> str:
    task_desc = "\n".join(f"- Task {t['task_number']}: {t['title']} ({t['status']})" for t in tasks)
    slug = project.get("slug", "project")
    system = "You write concise project summaries for a developer. Be direct and helpful."
    prompt = (
        f"Project: {project['name']}\n"
        f"Goal: {goal}\n\n"
        f"Tasks completed:\n{task_desc}\n\n"
        f"Write a brief summary (2-4 sentences) for Paras about what was built and how to use it.\n"
        f"IMPORTANT: Always end your summary with this exact line:\n"
        f"To play/test: open http://{SERVER_HOST}/play/{slug}/ in your browser"
    )
    play_url = f"http://{SERVER_HOST}/play/{slug}/"
    summary = await master_json_call(system, prompt, max_tokens=500)
    if not summary:
        summary = f"Project '{project['name']}' completed with {len(tasks)} tasks."
    if play_url not in summary:
        summary = summary.rstrip() + f"\n\nTo play/test: {play_url}"
    return summary


# ── Orchestration loop ────────────────────────────────────────────────────────

async def safe_send(ws: WebSocket, payload: dict,
                    cancel_event: asyncio.Event | None = None) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except (WebSocketDisconnect, RuntimeError):
        if cancel_event:
            cancel_event.set()
        return False


def _group_into_waves(tasks: list[dict]) -> list[list[dict]]:
    """Group tasks into execution waves based on depends_on.

    Tasks whose depends_on are all satisfied by previous waves are batched
    together and will execute concurrently via asyncio.gather().
    If a cycle or unresolvable dependency is detected, remaining tasks are
    appended as a single sequential wave to avoid infinite loops.
    """
    done_ids: set[int] = set()
    waves: list[list[dict]] = []
    remaining = list(tasks)
    while remaining:
        wave = [
            t for t in remaining
            if all(dep in done_ids for dep in (t.get("depends_on") or []))
        ]
        if not wave:
            # Cycle/unresolvable — fall back to running all remaining sequentially
            logging.warning("depends_on cycle detected; falling back to sequential execution")
            wave = remaining
        waves.append(wave)
        done_ids.update(t["task_number"] for t in wave)
        wave_set = {id(t) for t in wave}
        remaining = [t for t in remaining if id(t) not in wave_set]
    return waves


async def run_orchestration(ws: WebSocket, project_id: int, goal: str, resume: bool = False,
                            cancel_event: asyncio.Event | None = None) -> None:
    project = get_project(project_id)
    if not project:
        await ws.send_json({"type": "orch_phase", "phase": "error", "msg": "Project not found."})
        return
    stats = OrchStats()

    if resume:
        saved_tasks = get_resumable_tasks(project_id)
        if not saved_tasks:
            await ws.send_json({"type": "orch_phase", "phase": "error", "msg": "No incomplete tasks found. Project may already be complete."})
            return
        goal = get_last_project_goal(project_id) or "continue project"
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
        save_project_message(project_id, "user", goal)
        await ws.send_json({"type": "user", "content": goal, "timestamp": datetime.now(timezone.utc).isoformat()})

        planner = _get_master_model()
        await ws.send_json({"type": "orch_phase", "phase": "planning",
                            "msg": f"🧠 {planner.capitalize()} is planning the project..."})

        _plan_start = time.monotonic()

        async def _planning_ticker():
            while True:
                await asyncio.sleep(5)
                elapsed = int(time.monotonic() - _plan_start)
                await safe_send(ws, {"type": "orch_phase", "phase": "planning_tick",
                                     "msg": f"Still planning… {elapsed}s elapsed"})

        _ticker_task = asyncio.create_task(_planning_ticker())
        try:
            tasks = await claude_plan_project(project, goal)
        finally:
            _ticker_task.cancel()

        if not tasks:
            await ws.send_json({"type": "orch_phase", "phase": "error", "msg": "Failed to generate a task plan. Please try again."})
            return

        saved_tasks = save_tasks(project_id, tasks)

        task_summary = [
            {"id": t["id"], "task_number": t["task_number"], "title": t["title"],
             "assigned_to": t["assigned_to"], "status": t["status"]}
            for t in saved_tasks
        ]
        await ws.send_json({"type": "orch_plan", "tasks": task_summary})

        task_list_str = "\n".join(f"{t['task_number']}. {t['title']} (→ {t['assigned_to']})" for t in saved_tasks)
        append_devlog(project, f"## Planning\nGoal: {goal}\n\nTasks:\n{task_list_str}")

    # Execute tasks in dependency waves — independent tasks run concurrently
    waves = _group_into_waves(saved_tasks)
    for wave in waves:
        if cancel_event and cancel_event.is_set():
            append_devlog(project, "**Cancelled** by user.")
            await ws.send_json({"type": "cancelled"})
            return
        if len(wave) == 1:
            try:
                await _execute_task(ws, project, wave[0], goal, cancel_event=cancel_event, stats=stats)
            except Exception as e:
                t = wave[0]
                logging.error("Task %d failed: %s", t["task_number"], e)
                append_devlog(project, f"**Task {t['task_number']} ERRORED**: {e}")
                update_task(t["id"], status="errored", completed_at=datetime.now(timezone.utc).isoformat())
                await ws.send_json({"type": "orch_task_done", "task_id": t["id"], "files": [], "error": str(e)})
        else:
            results = await asyncio.gather(
                *[_execute_task(ws, project, t, goal, cancel_event=cancel_event, stats=stats) for t in wave],
                return_exceptions=True,
            )
            for t, result in zip(wave, results):
                if isinstance(result, Exception):
                    logging.error("Task %d failed: %s", t["task_number"], result)
                    append_devlog(project, f"**Task {t['task_number']} ERRORED**: {result}")
                    update_task(t["id"], status="errored", completed_at=datetime.now(timezone.utc).isoformat())
                    await ws.send_json({"type": "orch_task_done", "task_id": t["id"], "files": [], "error": str(result)})

    # Automated test phase
    if not (cancel_event and cancel_event.is_set()):
        await run_test_phase(ws, project, cancel_event=cancel_event)

    if cancel_event and cancel_event.is_set():
        return

    # Summary
    all_tasks = get_all_tasks(project_id)
    summary = await claude_project_summary(project, goal, all_tasks)

    append_devlog(project, f"## Summary\n\n{summary}")
    git_commit(project["folder_path"], "docs: project complete")
    update_project_status(project_id, "completed")

    await ws.send_json({"type": "orch_complete", "summary": summary, "slug": project.get("slug", "")})
    await ws.send_json({"type": "orch_stats", **stats.to_summary()})


async def _execute_task(ws: WebSocket, project: dict, task: dict, goal: str,
                        cancel_event: asyncio.Event | None = None,
                        stats: OrchStats | None = None) -> None:
    tid = task["id"]
    tnum = task["task_number"]
    agent = task["assigned_to"]

    code_exts = {".html", ".js", ".ts", ".css", ".py", ".json", ".jsx", ".tsx"}
    files_to_create = task.get("files_to_create", [])
    has_code_files = any(
        Path(f).suffix.lower() in code_exts for f in files_to_create
    ) if files_to_create else False
    config = _get_config()
    disabled_set = set(config.get("disabled_agents", []))
    if agent in ("qwen", "claude") and has_code_files and "deepseek" not in disabled_set:
        logging.info("Task %d: rerouting code task from '%s' to 'deepseek'", tnum, agent)
        agent = "deepseek"

    if agent in disabled_set:
        fallback = next((a for a in ["deepseek", "qwen", "claude"] if a not in disabled_set), None)
        if fallback:
            logging.info("Task %d: agent '%s' is disabled, rerouting to '%s'", tnum, agent, fallback)
            await ws.send_json({"type": "status", "message": f"⚙️ {agent} is disabled, routing task to {fallback}..."})
            agent = fallback
        else:
            logging.error("Task %d: all agents disabled, cannot execute", tnum)
            await ws.send_json({"type": "orch_task_done", "task_id": tid, "files": [], "error": "All agents disabled"})
            return

    update_task(tid, status="in_progress")

    await ws.send_json({
        "type": "orch_task_start",
        "task_id": tid,
        "task_number": tnum,
        "title": task["title"],
        "assigned_to": agent,
    })

    # Build prompt for worker
    files_to_create = task.get("files_to_create", [])
    file_context = ""
    if tnum > 1:
        existing_files: dict[str, str] = {}
        all_file_paths = list_project_files(project)
        if all_file_paths:
            task_text = (task['description'] + " " + " ".join(files_to_create)).lower()
            ALWAYS_INCLUDE = {"index.html", "main.js", "app.js", "game.js", "style.css"}
            relevant_paths = [
                p for p in all_file_paths
                if any(part in task_text
                       for part in [p.lower(), p.lower().split("/")[-1].replace(".", "")])
                or p.lower() in ALWAYS_INCLUDE
                or p.lower().split("/")[-1] in ALWAYS_INCLUDE
            ]
            existing_files = read_project_files(project, relevant_paths) if relevant_paths else {}
        if existing_files:
            budget = 6000
            file_context = "\n\nRelevant existing files (for reference):\n"

            ftc_set = {f.lower() for f in files_to_create}
            def _file_priority(fpath: str) -> int:
                fl = fpath.lower()
                if fl in ftc_set or fl.split("/")[-1] in ftc_set:
                    return 0
                if fl in ("index.html", "src/index.html"):
                    return 1
                return 2

            priority_caps = {0: 2500, 1: 2000, 2: 1000}
            for fpath, fcontent in sorted(existing_files.items(), key=lambda kv: _file_priority(kv[0])):
                if budget <= 0:
                    break
                tier = _file_priority(fpath)
                cap  = min(priority_caps[tier], budget)
                snippet = fcontent[:cap]
                file_context += f"\n--- {fpath} ---\n{snippet}\n"
                budget -= len(snippet)
            if budget <= 0:
                file_context += "\n[...truncated for brevity...]\n"

    worker_system = _build_worker_system(project, task_context=task["description"])

    worker_prompt = (
        f"Task {tnum}: {task['title']}\n\n"
        f"Description:\n{task['description']}\n\n"
        f"Files to create: {json.dumps(files_to_create)}\n"
        f"{file_context}"
    )

    history = [{"role": "user", "content": worker_prompt}]

    await ws.send_json({"type": "typing", "agent": agent})

    full = ""
    tok: dict = {}
    if agent == "claude":
        gen = stream_claude(history, system_prompt=worker_system, cancel_event=cancel_event, usage=tok, max_tokens=8192)
    else:
        gen = stream_ollama(agent, history, system_prompt=worker_system, cancel_event=cancel_event, usage=tok)

    async for chunk in gen:
        if cancel_event and cancel_event.is_set():
            break
        await ws.send_json({"type": "chunk", "agent": agent, "content": chunk})
        full += chunk

    await ws.send_json({"type": "done", "agent": agent})

    # Detect Ollama connection failure — fall back to master model
    if agent != "claude" and "[Ollama error:" in full:
        logging.warning("Task %d: %s unavailable, retrying with master model", tnum, agent)
        await ws.send_json({"type": "status", "message": f"⚠️ {agent} unavailable, falling back to master model..."})
        full = ""
        tok = {}
        fallback_agent = _get_master_model()
        await ws.send_json({"type": "typing", "agent": fallback_agent})
        async for chunk in stream_master(history, system_prompt=worker_system, cancel_event=cancel_event, usage=tok, max_tokens=8192):
            if cancel_event and cancel_event.is_set():
                break
            await ws.send_json({"type": "chunk", "agent": fallback_agent, "content": chunk})
            full += chunk
        await ws.send_json({"type": "done", "agent": fallback_agent})
        agent = fallback_agent

    # Check for empty output
    if not full.strip():
        logging.warning("Task %d: %s returned empty output — marking errored", tnum, agent)
        await ws.send_json({
            "type": "orch_phase",
            "phase": f"⚠️ Task {tnum} ({agent}): LLM returned no output — model may be down or unloaded.",
        })
        update_task(tid, status="errored", completed_at=datetime.now(timezone.utc).isoformat())
        await ws.send_json({"type": "orch_task_done", "task_id": tid, "files": []})
        return

    if stats:
        stats.record(agent,
                     tok.get("input_tokens", len(worker_prompt) // 4),
                     tok.get("output_tokens", len(full) // 4))

    save_project_message(project["id"], agent, full.strip(), task_id=tid)

    files = extract_files_from_response(full)
    written = write_project_files(project, files) if files else []

    for fp in written:
        await ws.send_json({"type": "orch_file", "path": fp})

    if written:
        git_commit(project["folder_path"], f"Task {tnum}: {task['title']}")

    files_str = ", ".join(written) if written else "(no files extracted)"
    append_devlog(project, f"**Task {tnum}** ({agent}): {task['title']}\nFiles: {files_str}")

    # Evaluate
    evaluation = await claude_evaluate_task(project, task, full)
    if not evaluation.get("approved", True) and evaluation.get("feedback"):
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

        retry_eval = await claude_evaluate_task(project, task, retry_full)
        if not retry_eval.get("approved", True):
            logging.warning("Task %d: retry also failed evaluation — marking errored", tnum)
            update_task(tid, status="errored",
                        completed_at=datetime.now(timezone.utc).isoformat())
            await ws.send_json({
                "type": "orch_phase",
                "phase": f"⚠️ Task {tnum} failed quality check after retry: {retry_eval.get('feedback', '')}",
            })
            await ws.send_json({"type": "orch_task_done", "task_id": tid, "files": written})
            return

    update_task(tid, status="done", completed_at=datetime.now(timezone.utc).isoformat())
    await ws.send_json({"type": "orch_task_done", "task_id": tid, "files": written})


def _build_worker_system(project: dict, task_context: str = "") -> str:
    slug = project.get("slug", "project")
    skills_block = load_skills(task_context) if task_context else ""
    return textwrap.dedent(f"""\
        You are implementing a specific task for project '{project['name']}'.
        Write clean, production-quality code.

        ⚠️  CRITICAL — FILE MARKER REQUIRED ON FIRST LINE OF EVERY CODE BLOCK:
        The VERY FIRST LINE inside each ``` block MUST be the file path comment:
          HTML:       <!-- FILE: index.html -->
          JavaScript: // FILE: js/game.js
          CSS:        /* FILE: css/style.css */
          Python:     # FILE: script.py
        Without this marker, the file WILL NOT BE SAVED. Do not skip it.
        Do NOT write any explanation text before or after code blocks.

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
    """).strip() + skills_block


async def run_test_phase(ws: WebSocket, project: dict, cancel_event: asyncio.Event | None = None) -> None:
    files = read_project_files(project)
    if not files:
        return

    await ws.send_json({"type": "orch_phase", "phase": "testing",
                        "msg": "🔍 Claude is reviewing the code for bugs..."})

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

    master = _get_master_model()
    await ws.send_json({"type": "typing", "agent": master})
    test_output = await master_json_call(test_system, review_prompt, max_tokens=1024)
    if test_output:
        await ws.send_json({"type": "chunk", "agent": master, "content": test_output})
        await ws.send_json({"type": "done", "agent": master})
        save_project_message(project["id"], master, test_output)

    if not test_output:
        await ws.send_json({"type": "orch_phase", "phase": "test_warn",
                            "msg": "⚠️ Code review LLM returned no output — review skipped."})
        return

    lgtm = "LGTM" in test_output.upper() and "BUG" not in test_output.upper()
    if lgtm:
        await ws.send_json({"type": "orch_phase", "phase": "test_pass",
                            "msg": "✅ Code review passed — no issues found."})
        return

    # Bugs found — fix them
    await ws.send_json({"type": "orch_phase", "phase": "fixing",
                        "msg": "🔧 Fixing issues found in code review..."})

    fix_system = _build_worker_system(project)
    fix_prompt = (
        f"Code review found these bugs:\n{test_output}\n\n"
        f"Current project files:\n{file_context}\n\n"
        f"Rewrite ONLY the files that need changes to fix all listed bugs. "
        f"Output the complete corrected file content(s) using FILE: markers on the first line."
    )

    await ws.send_json({"type": "typing", "agent": master})
    fix_output = ""
    async for chunk in stream_master([{"role": "user", "content": fix_prompt}],
                                      system_prompt=fix_system, cancel_event=cancel_event):
        if cancel_event and cancel_event.is_set():
            return
        await ws.send_json({"type": "chunk", "agent": master, "content": chunk})
        fix_output += chunk
    await ws.send_json({"type": "done", "agent": master})

    save_project_message(project["id"], master, fix_output.strip())
    fixed = write_project_files(project, extract_files_from_response(fix_output))
    for fp in fixed:
        await ws.send_json({"type": "orch_file", "path": fp})
    if fixed:
        git_commit(project["folder_path"], "fix: code review auto-fixes")
    append_devlog(project, f"## Code Review Fix\nBugs fixed in: {', '.join(fixed) or 'none'}")
    await ws.send_json({"type": "orch_phase", "phase": "test_fixed",
                        "msg": f"✅ Fixed {len(fixed)} file(s) — running second review pass..."})

    # Second review pass
    await ws.send_json({"type": "typing", "agent": master})
    second_output = await master_json_call(test_system, review_prompt, max_tokens=1024)
    if second_output:
        await ws.send_json({"type": "chunk", "agent": master, "content": second_output})
        await ws.send_json({"type": "done", "agent": master})
        save_project_message(project["id"], master, second_output)

    second_lgtm = (not second_output) or \
                  ("LGTM" in second_output.upper() and "BUG" not in second_output.upper())
    if second_lgtm:
        await ws.send_json({"type": "orch_phase", "phase": "test_pass",
                            "msg": "✅ Second review passed — project is ready."})
    else:
        await ws.send_json({"type": "orch_phase", "phase": "test_warn",
                            "msg": "⚠️ Some issues may remain after two review passes — check manually."})


async def run_fix_task(ws: WebSocket, project_id: int, feedback: str,
                       cancel_event: asyncio.Event | None = None) -> None:
    project = get_project(project_id)
    if not project:
        return

    files = read_project_files(project)

    context_parts, budget = [], 8000
    for path, content in files.items():
        part = f"=== {path} ===\n{content}"
        context_parts.append(part[:budget])
        budget -= len(part)
        if budget <= 0:
            break
    file_context = "\n\n".join(context_parts)

    fix_system = _build_worker_system(project, task_context=feedback)
    fix_prompt = (
        f"User feedback / request:\n{feedback}\n\n"
        f"Current project files:\n{file_context}\n\n"
        f"Analyze what needs to change, then output the COMPLETE corrected file(s) using FILE: markers. "
        f"Only output files that actually need to change. Make sure every fix is complete and working."
    )

    save_project_message(project_id, "user", feedback)
    await ws.send_json({"type": "user", "content": feedback,
                        "timestamp": datetime.now(timezone.utc).isoformat()})
    await ws.send_json({"type": "agent_count", "count": 1})

    fix_stats = OrchStats()
    full = ""
    tok: dict = {}
    agent_used = _get_master_model()
    await ws.send_json({"type": "typing", "agent": agent_used})

    async for chunk in stream_master([{"role": "user", "content": fix_prompt}],
                                      system_prompt=fix_system, cancel_event=cancel_event, usage=tok):
        if cancel_event and cancel_event.is_set():
            await ws.send_json({"type": "cancelled"})
            return
        await ws.send_json({"type": "chunk", "agent": agent_used, "content": chunk})
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

    fix_summary = f"Fixed files: {', '.join(fixed)}. Agent: {agent_used}."
    lesson = await extract_and_save_lesson(project, feedback, fix_summary)

    if fixed and not (cancel_event and cancel_event.is_set()):
        await run_test_phase(ws, project, cancel_event=cancel_event)

    slug = project.get("slug", "")
    await ws.send_json({"type": "fix_complete", "files_fixed": fixed,
                        "project_slug": slug, "lesson": lesson})
    await ws.send_json({"type": "orch_stats", **fix_stats.to_summary()})
