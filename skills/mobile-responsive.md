---
name: Mobile Responsive Design
description: Responsive layouts, touch events, and mobile-first CSS patterns
keywords: mobile, responsive, touch, swipe, gesture, media query, breakpoint, viewport, tablet, phone, portrait, landscape, tap, pinch, scroll, overflow
---

## Mobile Responsive Design Skill

### Mobile-First CSS
Start with mobile styles, add desktop with `min-width`:
```css
.container { padding: 12px; }              /* mobile default */
@media (min-width: 768px) {
  .container { padding: 24px; max-width: 1200px; margin: 0 auto; }
}
```

### Touch Event Handling
```javascript
let touchStartX = 0, touchStartY = 0;

element.addEventListener('touchstart', e => {
  touchStartX = e.touches[0].clientX;
  touchStartY = e.touches[0].clientY;
  e.preventDefault(); // prevent scroll-while-touching
}, { passive: false });

element.addEventListener('touchend', e => {
  const dx = e.changedTouches[0].clientX - touchStartX;
  const dy = e.changedTouches[0].clientY - touchStartY;
  if (Math.abs(dx) > Math.abs(dy)) {
    if (dx > 30) handleSwipeRight();
    else if (dx < -30) handleSwipeLeft();
  }
});
```

### Game Touch Controls (D-pad pattern)
```html
<div class="touch-controls">
  <button id="btn-up">▲</button>
  <div class="row">
    <button id="btn-left">◀</button>
    <button id="btn-right">▶</button>
  </div>
  <button id="btn-down">▼</button>
</div>
```
```css
.touch-controls { display: none; }
@media (pointer: coarse) { .touch-controls { display: flex; flex-direction: column; align-items: center; } }
```

### Viewport Units
- `100dvh` — dynamic viewport height (accounts for mobile browser chrome)
- `svh` — small viewport height (always visible area)
- `vw` — full viewport width

### Safe Area (iPhone notch)
```css
padding: env(safe-area-inset-top) env(safe-area-inset-right)
         env(safe-area-inset-bottom) env(safe-area-inset-left);
```

### Rules
- Always include `<meta name="viewport" content="width=device-width, initial-scale=1.0">`
- Use `em`/`rem` for font sizes, not `px`
- Minimum touch target size: 44×44px
- Test at 375px width (iPhone SE) as the smallest common breakpoint
