# Orchestration Flow

## Overview

When a user submits a project build or fix request, `run_orchestration()` executes a 5-phase pipeline. All output streams in real-time to the client via WebSocket. All structured calls (plan, evaluate, review) go through `_master_json_call()` — routed to Claude or Qwen depending on `_config`.

## Build Pipeline Phases

### 1. Planning
- `claude_plan_project()` calls `_master_json_call()` with a detailed system prompt
- Returns a JSON list of atomic tasks (up to **100 tasks**)
- Each task: `{ task_number, title, description, assigned_to, files_to_create }`
- Tasks saved to the `tasks` DB table
- System prompt guides: simple projects → 1–3 tasks; complex → as many as needed

### 2. Task Execution (`_execute_task`)
For each task:
- Skills loaded: `_load_skills(task.description)` matches keywords → injects into system prompt
- Assigned agent receives: task description + existing project files + skills context
- Response parsed by `extract_files_from_response()` for code files
- Files written to `projects/<slug>/src/`
- Git auto-commit: `"Task N: <title>"`
- Task marked `done` in DB
- One retry if `claude_evaluate_task()` rejects the output

### 3. Evaluation
- `claude_evaluate_task()` calls `_master_json_call()` with task spec + agent output
- Returns `{"approved": true/false, "feedback": "..."}
- If not approved: one retry with feedback injected into prompt

### 4. Testing (`run_test_phase`)
- `_master_json_call()` reviews full project source for concrete bugs (7 specific check types)
- If "LGTM ✓": proceed
- If bugs found: `stream_master()` generates fixes → files rewritten → git commit

### 5. Completion
- `claude_project_summary()` via `_master_json_call()` writes 2–4 sentence summary
- Summary sent to client with play URL
- Final git commit: `"docs: project complete"`
- Project status updated to `completed`
- Token/time stats sent: `orch_stats` message

## Fix Flow (user-triggered)

```
User sends fix feedback
    → _build_worker_system(project, task_context=feedback)  ← skills injected
    → stream_master() streams the fix
    → files extracted + written
    → git commit: "fix: <feedback[:60]>"
    → run_test_phase() on updated code
    → extract_and_save_lesson() via _master_json_call()
    → fix_complete message returned
```

## Agent Assignment Logic

Tasks are assigned based on `assigned_to` field from the planner:
- `"claude"` → `stream_claude()` (if available), else falls back to `stream_ollama("qwen")`
- `"deepseek"` → `stream_ollama("deepseek", ...)`
- `"qwen"` → `stream_ollama("qwen", ...)`

Orchestration/planning/eval always use `get_master_model()` → either Claude or Qwen.

## Intent Detection

`_claude_classify()` → `_master_json_call()`. Works with Claude or Qwen.

- **General chat**: routes to selected agent(s) for conversational response
- **New project**: `intent_detected` message → user confirms → `run_orchestration()`
- **Continue project**: routes to `run_fix_task()` or `run_orchestration(resume=True)`
- **Project query**: `stream_master()` answers using devlog + task list context

## Master Model Fallback

When `get_master_model()` returns `"qwen"`:
- All `_master_json_call()` calls go to `_ollama_json_call("qwen", ...)`
- All `stream_master()` calls go to `stream_ollama("qwen", ...)`
- DeepSeek still handles `assigned_to: "deepseek"` tasks
- Claude worker tasks (`assigned_to: "claude"`) still attempt Claude; if unavailable, `stream_ollama("qwen")` is used as fallback in `_execute_task`

## Confirmation Mechanism

When intent is ambiguous, the server sends:
```json
{ "type": "intent_detected", "intent": "project_new"|"project_continue", "original_message": "..." }
```
The client shows a confirmation dialog. User confirms → client sends `start_orchestration` or `fix_project`.
