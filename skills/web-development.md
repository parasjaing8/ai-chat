---
name: Web Development
description: HTML/CSS/JS web app and website development for this platform
keywords: html, css, javascript, website, webpage, web app, frontend, ui, ux, dom, responsive, layout, flex, grid, animation, form, button, navbar, modal, dropdown, card, table
---

## Web Development Skill

### Platform Context
Projects are served at `http://{SERVER_HOST}/play/<slug>/` (default: `http://192.168.0.130:8080/play/<slug>/`). All files live in `projects/<slug>/src/`.

### Required Structure
Every web project must have `index.html` as the entry point. Always use **relative asset paths**:
```html
<script src="js/app.js"></script>   <!-- CORRECT -->
<script src="/js/app.js"></script>  <!-- WRONG -->
```

### HTML Boilerplate (always include)
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>App Name</title>
</head>
```

### Modern CSS Patterns
- Use CSS custom properties (`--var-name`) for theme colors
- Flexbox for 1D layouts, Grid for 2D layouts
- `clamp()` for fluid typography: `font-size: clamp(14px, 2vw, 18px)`
- `dvh` units for full-screen mobile layouts

### JS Patterns for This Platform
- Use `window.globals` instead of ES module imports (no bundler)
- Store state in plain JS objects, not classes unless necessary
- Prefer `fetch` for API calls; always `await` and handle errors
- Use `localStorage` for persistence across reloads

### No GUI Assumptions
Never reference: Finder, Explorer, local file paths, desktop apps, system tray, or "open in browser".
Users access everything via the browser at the play URL.
