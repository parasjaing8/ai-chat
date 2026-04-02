# Skills System

## Overview

Skills are modular knowledge files in `skills/*.md` that get injected into worker agent system prompts when their keywords match the task context. This allows LLMs to have domain-specific expertise loaded on-demand without bloating every prompt.

## How It Works

1. `_execute_task()` calls `_build_worker_system(project, task_context=task["description"])`
2. `_build_worker_system()` calls `_load_skills(task_context)`
3. `_load_skills()` reads all `skills/*.md` files, checks if any keyword appears in `task_context`
4. Matched skill bodies are appended to the system prompt as `ACTIVE SKILLS:` block
5. The worker agent receives domain knowledge relevant to its specific task

## Skill File Format

```markdown
---
name: Human-readable skill name
description: One-line description of what this skill covers
keywords: keyword1, keyword2, keyword3, compound keyword
---

## Skill Body

Content injected into the system prompt when this skill is activated.
Include patterns, rules, code examples, and platform-specific knowledge.
```

The frontmatter is stripped; only the body is injected.

## Available Skills

| File | Activated by keywords |
|------|-----------------------|
| `ssh-operations.md` | ssh, remote, mac mini, terminal, shell, command |
| `web-development.md` | html, css, javascript, website, web app, frontend |
| `game-development.md` | game, canvas, sprite, animation, physics, arcade |
| `api-development.md` | api, rest, fastapi, endpoint, route, backend |
| `database.md` | database, sqlite, sql, query, schema, table |
| `debugging.md` | debug, bug, fix, error, crash, broken, not working |
| `system-admin.md` | system, admin, process, cpu, memory, service |
| `data-visualization.md` | chart, graph, dashboard, visualization, plot |
| `mobile-responsive.md` | mobile, responsive, touch, swipe, gesture |
| `performance-optimization.md` | performance, optimize, lazy load, cache, memory |
| `python-scripting.md` | python, script, automation, file, csv, subprocess |

## Adding a New Skill

1. Create `skills/my-skill.md` with the frontmatter format above
2. Choose unique, specific keywords that clearly identify when the skill applies
3. Keep the body concise — it's injected into every matching task's system prompt
4. No code changes needed — `_load_skills()` automatically picks it up

## Listing Skills via API

```
GET /skills
→ [{ "file": "game-development.md", "name": "Game Development", "keywords": [...], "description": "..." }]
```
