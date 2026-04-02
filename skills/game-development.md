---
name: Game Development
description: Canvas-based games, physics, animation loops, and interactive game mechanics
keywords: game, canvas, sprite, animation, physics, collision, loop, requestanimationframe, player, enemy, score, level, puzzle, snake, tetris, platformer, shooter, arcade, rpg, simulation, particles
---

## Game Development Skill

### Game Loop Pattern (always use requestAnimationFrame)
```javascript
let lastTime = 0;
function gameLoop(timestamp) {
  const dt = (timestamp - lastTime) / 1000; // seconds
  lastTime = timestamp;
  update(dt);
  draw();
  requestAnimationFrame(gameLoop);
}
requestAnimationFrame(gameLoop);
```
Never use `setInterval` for the main game loop — it causes frame tearing.

### Canvas Setup
```javascript
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');

function resize() {
  canvas.width = Math.min(window.innerWidth, 800);
  canvas.height = Math.min(window.innerHeight, 600);
}
window.addEventListener('resize', resize);
resize();
```

### Touch Controls (required for all games)
Every game must support BOTH keyboard and touch:
```javascript
// On-screen D-pad or action buttons
// Show only on touch devices:
// @media (pointer: coarse) { .touch-controls { display: flex; } }

document.getElementById('btn-left').addEventListener('touchstart', e => {
  e.preventDefault();
  keys['ArrowLeft'] = true;
});
document.getElementById('btn-left').addEventListener('touchend', () => {
  keys['ArrowLeft'] = false;
});
```

### AABB Collision Detection
```javascript
function collides(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x &&
         a.y < b.y + b.h && a.y + a.h > b.y;
}
```

### State Machine Pattern
```javascript
const STATE = { MENU: 'menu', PLAYING: 'playing', PAUSED: 'paused', GAMEOVER: 'gameover' };
let state = STATE.MENU;
```

### Performance Rules
- Clear only dirty regions, not the entire canvas, for large scenes
- Pool objects (bullets, particles) instead of creating/destroying
- Use `ctx.save()` / `ctx.restore()` around transformed draw calls
