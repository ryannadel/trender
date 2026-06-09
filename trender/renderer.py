"""Static interactive HTML report renderer."""

from __future__ import annotations

import json

from jinja2 import Template

from .models import TrendMap


REPORT_TEMPLATE = Template(
    r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trender — {{ trend.topic_query }}</title>
  <style>
    :root { color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }
    body { margin: 0; background: #0b1020; color: #eef2ff; }
    main { max-width: 1180px; margin: 0 auto; padding: 32px; }
    .hero, .panel, .card { background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.12); border-radius: 18px; box-shadow: 0 12px 40px rgba(0,0,0,.25); }
    .hero { padding: 28px; margin-bottom: 18px; }
    h1 { margin: 0 0 8px; font-size: 34px; }
    .muted { color: #a7b0c8; }
    .grid { display: grid; grid-template-columns: 1.2fr .8fr; gap: 18px; }
    .panel { padding: 18px; }
    .controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; margin: 16px 0; }
    label { display: grid; gap: 4px; color: #cbd5e1; font-size: 13px; }
    input, select, button { border-radius: 10px; border: 1px solid rgba(255,255,255,.16); background: #111936; color: #eef2ff; padding: 9px 11px; }
    button { cursor: pointer; }
    button:hover { background: #1d2a57; }
    #trendChart { width: 100%; min-height: 320px; overflow: visible; }
    .axis, .tick { stroke: #475569; stroke-width: 1; }
    .label { fill: #94a3b8; font-size: 11px; }
    .legend { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; color: #cbd5e1; font-size: 12px; }
    .legend span { display: inline-flex; align-items: center; gap: 5px; }
    .swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; margin-top: 18px; }
    .card { padding: 16px; }
    .badge { display: inline-block; border-radius: 999px; padding: 4px 9px; font-size: 12px; background: #23356f; color: #dbeafe; }
    .emerging { background: #14532d; } .rising { background: #1d4ed8; } .fading { background: #7f1d1d; } .stable { background: #3f3f46; }
    a { color: #93c5fd; }
    details { margin-top: 10px; }
    ul { padding-left: 18px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } main { padding: 18px; } }
  </style>
</head>
<body>
<main>
  <section class="hero">
    <h1>Trend map: {{ trend.topic_query }}</h1>
    <div class="muted">Generated {{ trend.generated_at }} · Source window {{ trend.window.start }} → {{ trend.window.end }}</div>
    <p class="muted">Trends are calculated from bucketed source evidence. Each point is the number of original sources in that time bucket that support the topic; velocity compares the recent bucket rate against the earlier bucket rate.</p>
    <div class="controls">
      <label>From <input id="fromDate" type="date" value="{{ trend.window.start }}"></label>
      <label>To <input id="toDate" type="date" value="{{ trend.window.end }}"></label>
      <button data-days="7">7d</button><button data-days="30">30d</button><button data-days="90">90d</button><button data-days="365">1y</button>
      <label>Direction <select id="direction"><option value="">All</option><option>emerging</option><option>rising</option><option>stable</option><option>fading</option></select></label>
      <label>Search <input id="search" placeholder="filter topics or sources"></label>
    </div>
  </section>

  <section class="grid">
    <div class="panel"><svg id="trendChart" viewBox="0 0 900 320" role="img" aria-label="Trend timeline"></svg><div id="legend" class="legend"></div></div>
    <div class="panel">
      <h2>Compare windows</h2>
      <div class="controls">
        <label>A from <input id="aFrom" type="date"></label><label>A to <input id="aTo" type="date"></label>
        <label>B from <input id="bFrom" type="date"></label><label>B to <input id="bTo" type="date"></label>
      </div>
      <div id="comparison" class="muted">Pick two ranges to compare emerging and accelerating topics.</div>
    </div>
  </section>

  <section id="topicCards" class="cards"></section>
</main>
<script>
const TREND = {{ trend_json }};
const sourceById = Object.fromEntries(TREND.sources.map(s => [s.id, s]));
const allTopics = TREND.topics;
const palette = ["#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#fb7185", "#2dd4bf", "#c084fc"];

function inRange(date, from, to) { return (!from || date >= from) && (!to || date <= to); }
function topicCountInRange(topic, from, to) { return topic.time_series.filter(p => inRange(p.date, from, to)).reduce((n, p) => n + p.count, 0); }
function recomputeDirection(topic, from, to) {
  const points = topic.time_series.filter(p => inRange(p.date, from, to));
  if (!points.length) return topic.direction;
  const mid = Math.max(1, Math.floor(points.length / 2));
  const early = points.slice(0, mid).reduce((n, p) => n + p.count, 0);
  const late = points.slice(mid).reduce((n, p) => n + p.count, 0);
  const first = points[0].date;
  const ageDays = (new Date(to || TREND.window.end) - new Date(first)) / 86400000;
  if (ageDays <= 7) return "emerging";
  if (late > early) return "rising";
  if (late < early) return "fading";
  return "stable";
}
function filteredTopics() {
  const from = document.getElementById("fromDate").value;
  const to = document.getElementById("toDate").value;
  const direction = document.getElementById("direction").value;
  const query = document.getElementById("search").value.toLowerCase();
  return allTopics.map(t => ({...t, current_count: topicCountInRange(t, from, to), current_direction: recomputeDirection(t, from, to)}))
    .filter(t => t.current_count > 0)
    .filter(t => !direction || t.current_direction === direction)
    .filter(t => !query || JSON.stringify(t).toLowerCase().includes(query) || t.source_ids.some(id => JSON.stringify(sourceById[id] || {}).toLowerCase().includes(query)))
    .sort((a, b) => b.current_count - a.current_count || b.velocity - a.velocity);
}
function renderChart(topics) {
  const labels = [...new Set(topics.flatMap(t => t.time_series.map(p => p.date)))].sort();
  const selected = topics.slice(0, 8);
  const datasets = selected.map(topic => labels.map(date => (topic.time_series.find(p => p.date === date) || {count: 0}).count));
  const maxY = Math.max(1, ...datasets.flat());
  const svg = document.getElementById("trendChart");
  const width = 900, height = 320, left = 54, right = 20, top = 18, bottom = 48;
  const plotW = width - left - right, plotH = height - top - bottom;
  const x = (i) => left + (labels.length <= 1 ? 0 : (i / (labels.length - 1)) * plotW);
  const y = (v) => top + plotH - (v / maxY) * plotH;
  let markup = `<line class="axis" x1="${left}" y1="${top}" x2="${left}" y2="${top + plotH}"></line><line class="axis" x1="${left}" y1="${top + plotH}" x2="${left + plotW}" y2="${top + plotH}"></line>`;
  for (let i = 0; i <= 4; i++) {
    const value = Math.round((maxY / 4) * i);
    const yy = y(value);
    markup += `<line class="tick" x1="${left - 4}" y1="${yy}" x2="${left + plotW}" y2="${yy}" opacity=".25"></line><text class="label" x="8" y="${yy + 4}">${value}</text>`;
  }
  labels.forEach((label, i) => {
    if (i % Math.ceil(labels.length / 6 || 1) === 0) markup += `<text class="label" x="${x(i) - 24}" y="${height - 15}">${label.slice(5)}</text>`;
  });
  datasets.forEach((values, datasetIndex) => {
    const color = palette[datasetIndex % palette.length];
    const points = values.map((value, i) => `${x(i)},${y(value)}`).join(" ");
    markup += `<polyline fill="none" stroke="${color}" stroke-width="3" points="${points}"></polyline>`;
    values.forEach((value, i) => { if (value > 0) markup += `<circle cx="${x(i)}" cy="${y(value)}" r="3" fill="${color}"><title>${selected[datasetIndex].name}: ${value} on ${labels[i]}</title></circle>`; });
  });
  svg.innerHTML = markup;
  document.getElementById("legend").innerHTML = selected.map((topic, i) => `<span><i class="swatch" style="background:${palette[i % palette.length]}"></i>${escapeHtml(topic.name)}</span>`).join("");
}
function renderCards(topics) {
  document.getElementById("topicCards").innerHTML = topics.map(topic => {
    const sources = topic.source_ids.map(id => sourceById[id]).filter(Boolean);
    return `<article class="card">
      <span class="badge ${topic.current_direction}">${topic.current_direction}</span>
      <h3>${escapeHtml(topic.name)}</h3>
      <p class="muted">${escapeHtml(topic.description || "")}</p>
      <p><strong>${topic.current_count}</strong> bucketed source mentions · diversity ${topic.source_diversity} · momentum ${topic.velocity}</p>
      <details><summary>Key findings</summary><ul>${topic.key_findings.map(f => `<li>${escapeHtml(f)}</li>`).join("")}</ul></details>
      <details><summary>Original sources</summary><ul>${sources.map(s => `<li><a href="${s.url}" target="_blank" rel="noreferrer">${escapeHtml(s.title)}</a> <span class="muted">(${s.source_type}, ${s.published_at})</span></li>`).join("")}</ul></details>
    </article>`;
  }).join("");
}
function compare() {
  const af = aFrom.value, at = aTo.value, bf = bFrom.value, bt = bTo.value;
  if (!af || !at || !bf || !bt) return;
  const rows = allTopics.map(t => ({ name: t.name, a: topicCountInRange(t, af, at), b: topicCountInRange(t, bf, bt) }))
    .filter(r => r.a || r.b).sort((x, y) => (y.b - y.a) - (x.b - x.a)).slice(0, 8);
  comparison.innerHTML = rows.map(r => `<div><strong>${escapeHtml(r.name)}</strong>: ${r.a} → ${r.b} (${r.b - r.a >= 0 ? "+" : ""}${r.b - r.a})</div>`).join("");
}
function update() { const topics = filteredTopics(); renderChart(topics); renderCards(topics); compare(); }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
document.querySelectorAll("input, select").forEach(el => el.addEventListener("input", update));
document.querySelectorAll("button[data-days]").forEach(button => button.addEventListener("click", () => {
  const days = Number(button.dataset.days); const end = new Date(TREND.window.end); const start = new Date(end); start.setDate(end.getDate() - days);
  fromDate.value = start.toISOString().slice(0,10); toDate.value = end.toISOString().slice(0,10); update();
}));
update();
</script>
</body>
</html>"""
)


def render_report(trend: TrendMap) -> str:
    return REPORT_TEMPLATE.render(
        trend=trend,
        trend_json=json.dumps(trend.to_dict(), ensure_ascii=False),
    )

