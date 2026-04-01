# Orchestration Flow

## Overview

When a user submits a project build or fix request, `run_orchestration()` executes a 5-phase pipeline. All output streams in real-time to the client via WebSocket.

## Build Pipeline Phases

### 1. Planning
- Claude receives the project goal + system context
- Returns a JSON list of atomic tasks: `[{ "title": "...", "description": "...", "assigned_to": "claude|deepseek|qwen" }]`
- Tasks are saved to the `tasks` DB table

### 2. Task Execution
For each task:
- Assigned agent (Claude/DeepSeek/Qwen) receives: task description + existing project files + last N messages
- Response is parsed by `extract_files_from_response()` for code files
- Files written to `projects/<slug>/src/`
- Git auto-commit: `"Task N: <title>"`
- Task marked `completed` in DB

### 3. Evaluation
- Claude reviews each task's output
- If output is incomplete or incorrect, task is flagged for re-execution
- Max retry attempts prevent infinite loops

### 4. Testing
- `run_test_phase()`: Claude reviews the entire project source
- Checks for: missing files, undefined variable references, broken imports, syntax errors
- If bugs found → auto-fix attempt (git commit: `"fix: <feedback[:60]>"`)
- Lessons extracted from successful fixes → appended to memory files

### 5. Completion
- Summary message sent to client with:
  - Total tasks, elapsed time
  - Token counts per agent
  - Estimated Claude API cost
  - Play URL: `http://<SERVER_HOST>/play/<slug>/`
- Final git commit: `"docs: project complete"`
- Project status updated to `completed`

## Fix Flow (user-triggered)

```
User sends fix feedback
    → Claude analyzes feedback + full project source
    → Writes corrected files
    → Git commit: "fix: <feedback[:60]>"
    → run_test_phase() on updated code
    → Lessons extracted if fix succeeded
    → Summary returned
```

## Intent Detection

The WebSocket handler detects user intent before routing:
- **General chat**: routes to selected agent(s) for conversational response
- **New project**: triggers full orchestration pipeline
- **Continue project**: runs fix flow on existing project

## Agent Assignment Logic

Tasks are assigned based on `assigned_to` field from Claude's plan:
- `"claude"` → `call_claude()`
- `"deepseek"` → `call_ollama("deepseek", ...)`
- `"qwen"` → `call_ollama("qwen", ...)`

In group chat mode, `@claude`, `@deepseek`, `@qwen` mentions route to specific agents. Without a mention, Claude responds by default.
