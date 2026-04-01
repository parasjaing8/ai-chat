# Agent Rules - STRICT

These rules MUST be followed by all AI agents (Claude, DeepSeek, Qwen) when generating code, responses, or instructions for the user.

## Rule 1: No Local File Access Instructions
NEVER tell the user to "open index.html", "double-click a file", "open in Finder", or use any local file manager. The Mac Mini is HEADLESS - there is no display, no mouse, no keyboard.

## Rule 2: Always Provide the Play URL
ALWAYS tell the user the correct URL to access their project:
```
http://192.168.0.130:8080/play/<project-slug>/
```
This is the ONLY way to view web projects.

## Rule 3: Port 8080 is Reserved
NEVER use port 8080 for project-specific servers (Express, Flask, http-server, etc.). Port 8080 is the AI chat server. Do NOT start additional servers on any port - projects are auto-served via the `/play/` route.

## Rule 4: Relative Asset Paths Only
Web projects MUST use relative paths for all assets:
- CORRECT: `src="js/game.js"`, `href="css/style.css"`
- WRONG: `src="/js/game.js"`, `href="/css/style.css"`
- WRONG: `src="C:/path/to/file"` or any absolute path

## Rule 5: No GUI Assumptions
NEVER assume a display, GUI, desktop environment, or local file access exists on the Mac. No references to:
- Opening files in a browser locally
- Using Finder/Explorer
- Desktop notifications
- System tray icons
- GUI dialogs

## Rule 6: Proper HTML Structure
When writing HTML projects, ALWAYS include:
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Project Title</title>
</head>
```

## Rule 7: Complete, Runnable Code
Code must be COMPLETE and RUNNABLE. Never include:
- `// TODO: implement this`
- `// Add your code here`
- `/* placeholder */`
- Stub functions with no implementation
- References to external files that don't exist in the project

## Rule 8: Relative File Paths in Code
All file paths referenced in code (imports, src attributes, fetch URLs) must be relative. Never use absolute filesystem paths.

## Rule 9: index.html Entry Point
Always create an `index.html` as the entry point for web projects. This is what gets served when the user visits `/play/<slug>/`.

## Rule 10: Touch Controls for Games
Canvas/game projects MUST handle keyboard input AND include fallback touch controls for mobile/tablet access. Use `@media (pointer: coarse)` to show touch buttons on touch devices.
