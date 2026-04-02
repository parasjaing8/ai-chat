---
name: Data Visualization
description: Charts, graphs, dashboards, and canvas-based data rendering
keywords: chart, graph, visualization, dashboard, plot, bar chart, line chart, pie chart, scatter, heatmap, histogram, d3, canvas, svg, data, analytics, metrics, statistics, trend
---

## Data Visualization Skill

### Library Recommendations (CDN, no install needed)
```html
<!-- Chart.js — easiest for standard charts -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>

<!-- Lightweight Charts — for financial/time series -->
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
```

### Chart.js Quick Start
```javascript
const ctx = document.getElementById('myChart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'bar',  // 'line', 'pie', 'doughnut', 'scatter', 'radar'
  data: {
    labels: ['Jan', 'Feb', 'Mar'],
    datasets: [{
      label: 'Revenue',
      data: [1200, 1900, 800],
      backgroundColor: ['#7c3aed', '#0891b2', '#d97706'],
    }]
  },
  options: {
    responsive: true,
    plugins: { legend: { position: 'top' } }
  }
});
```

### Pure Canvas Chart Pattern (no dependencies)
```javascript
function drawBarChart(canvas, data, labels) {
  const ctx = canvas.getContext('2d');
  const { width, height } = canvas;
  const padding = 40;
  const barWidth = (width - padding * 2) / data.length - 4;
  const maxVal = Math.max(...data);

  ctx.clearRect(0, 0, width, height);
  data.forEach((val, i) => {
    const barH = ((val / maxVal) * (height - padding * 2));
    const x = padding + i * (barWidth + 4);
    const y = height - padding - barH;
    ctx.fillStyle = '#7c3aed';
    ctx.fillRect(x, y, barWidth, barH);
    ctx.fillStyle = '#e6edf3';
    ctx.fillText(labels[i], x + barWidth / 2, height - 10);
  });
}
```

### Rules
- Always make charts responsive (`responsive: true` in Chart.js)
- Dark theme colors match the platform UI: bg `#0d1117`, text `#e6edf3`, accent `#7c3aed`
- Include a loading state while data fetches
- Handle empty data gracefully (show "No data yet" placeholder)
