---
name: Performance Optimization
description: Web performance, lazy loading, caching, rendering optimization, and memory management
keywords: performance, optimize, fast, slow, lazy load, cache, memory, leak, render, fps, bundle, minify, compress, prefetch, debounce, throttle, worker, indexeddb
---

## Performance Optimization Skill

### Debounce & Throttle
```javascript
// Debounce — wait until user stops (search input, resize)
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// Throttle — fire at most once per interval (scroll, mousemove)
function throttle(fn, ms) {
  let last = 0;
  return (...args) => {
    const now = Date.now();
    if (now - last >= ms) { last = now; fn(...args); }
  };
}
```

### Canvas Performance
```javascript
// Use offscreen canvas for static layers
const bgCanvas = document.createElement('canvas');
const bgCtx = bgCanvas.getContext('2d');
// Draw static background once onto bgCanvas
// Each frame: ctx.drawImage(bgCanvas, 0, 0)  ← much faster than redrawing

// Object pooling for bullets/particles
class Pool {
  constructor(create, size = 50) {
    this.items = Array.from({ length: size }, create);
    this.active = [];
  }
  get() { return this.items.pop() || this.create(); }
  release(item) { item.active = false; this.items.push(item); }
}
```

### DOM Performance
```javascript
// Batch DOM reads, then writes (avoid layout thrashing)
const heights = elements.map(el => el.offsetHeight); // read all first
elements.forEach((el, i) => { el.style.height = heights[i] + 'px'; }); // then write

// Use DocumentFragment for bulk inserts
const frag = document.createDocumentFragment();
items.forEach(item => frag.appendChild(createRow(item)));
container.appendChild(frag); // single reflow
```

### Lazy Loading
```javascript
// Intersection Observer for lazy loading images/content
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.src = e.target.dataset.src;
      observer.unobserve(e.target);
    }
  });
}, { rootMargin: '100px' });
document.querySelectorAll('img[data-src]').forEach(img => observer.observe(img));
```

### Memory Rules
- Remove event listeners when elements are removed: `el.removeEventListener(...)`
- Clear `setInterval`/`setTimeout` when no longer needed
- Null out large objects when done: `cache = null`
- Use `WeakMap`/`WeakSet` for object metadata to avoid memory leaks
