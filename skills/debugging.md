---
name: Debugging
description: Code review, bug identification, root cause analysis, and systematic fix strategies
keywords: debug, bug, fix, error, crash, traceback, exception, issue, problem, broken, not working, undefined, null, typeerror, referenceerror, syntaxerror, review, inspect, diagnose
---

## Debugging Skill

### Systematic Diagnosis Order
1. Read the exact error message — don't guess
2. Identify the file and line number
3. Check what value the variable holds at that point
4. Trace backwards to where it was set
5. Fix the root cause, not the symptom

### Common Web Bugs & Fixes

**`Cannot read properties of null` / `getElementById returns null`**
- The element doesn't exist in the DOM when the script runs
- Fix: move `<script>` to end of `<body>`, or wrap in `DOMContentLoaded`

**`ReferenceError: X is not defined`**
- Script load order wrong, or using ES imports without `type="module"`
- Fix: check `<script>` order; use `window.X` globals instead of imports

**`404 on asset` (image, CSS, JS)**
- Path is absolute (`/js/app.js`) but should be relative (`js/app.js`)
- Fix: remove the leading `/`

**Canvas game doesn't start**
- Game loop never initiates
- Fix: verify `requestAnimationFrame(gameLoop)` is called after all setup

**Fetch returns 403/404**
- Wrong URL, or CORS issue, or missing trailing slash on FastAPI route
- Fix: check the exact URL; FastAPI routes with trailing slashes need `/`

### Code Review Checklist
- [ ] All referenced IDs exist in HTML
- [ ] All imported files actually exist
- [ ] No undefined functions called
- [ ] No ES imports without `type="module"`
- [ ] Canvas game loop starts with `requestAnimationFrame`
- [ ] No hardcoded absolute paths (`/path/to/file`)
- [ ] All async functions are `await`ed

### Fix Strategy
- Change ONE thing at a time and verify
- Don't add workarounds — fix the underlying issue
- If a function is broken, fix the function; don't route around it
