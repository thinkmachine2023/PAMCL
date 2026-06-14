"""
PAMCL Audit Dashboard — local web server for audit log visualization.

Usage:
    python -m pamcl dashboard <audit.jsonl> [--port 8765]

Serves a self-contained HTML dashboard that displays:
  - Summary statistics (events, violations, mode changes)
  - Event timeline with filtering by type and shadow mode
  - Constraint violation severity distribution
  - Setpoint change history
"""

import json
import http.server
import threading
from pathlib import Path
from typing import Any, Dict, List

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PAMCL Audit Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface-hover: #242836;
  --border: #2a2e3a;
  --text-primary: #e4e6eb;
  --text-secondary: #8b8f9a;
  --accent: #4f8cff;
  --accent-dim: rgba(79,140,255,0.12);
  --green: #22c55e;
  --yellow: #eab308;
  --orange: #f97316;
  --red: #ef4444;
  --radius: 10px;
  --font: 'Inter', -apple-system, sans-serif;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font);
  background: var(--bg);
  color: var(--text-primary);
  line-height: 1.6;
  min-height: 100vh;
}
.container { max-width: 1280px; margin: 0 auto; padding: 24px 20px; }
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 20px 0; border-bottom: 1px solid var(--border); margin-bottom: 24px;
}
header h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }
header h1 span { color: var(--accent); }
header .meta { font-size: 13px; color: var(--text-secondary); }

/* Cards */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 28px; }
.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 18px 20px; transition: border-color 0.2s;
}
.card:hover { border-color: var(--accent); }
.card .label { font-size: 12px; font-weight: 500; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
.card .value { font-size: 28px; font-weight: 700; letter-spacing: -0.5px; }
.card .value.green { color: var(--green); }
.card .value.yellow { color: var(--yellow); }
.card .value.orange { color: var(--orange); }
.card .value.red { color: var(--red); }

/* Charts */
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 28px; }
@media (max-width: 768px) { .chart-row { grid-template-columns: 1fr; } }
.chart-box {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px;
}
.chart-box h3 { font-size: 14px; font-weight: 600; margin-bottom: 16px; color: var(--text-secondary); }
.bar-chart { display: flex; align-items: flex-end; gap: 16px; height: 160px; padding-top: 10px; }
.bar-col { display: flex; flex-direction: column; align-items: center; flex: 1; height: 100%; justify-content: flex-end; }
.bar {
  width: 100%; min-height: 4px; border-radius: 4px 4px 0 0;
  transition: height 0.6s ease; position: relative;
}
.bar-label { font-size: 11px; color: var(--text-secondary); margin-top: 8px; text-transform: uppercase; }
.bar-value {
  font-size: 12px; font-weight: 600; margin-bottom: 4px;
}

/* Type distribution */
.type-list { display: flex; flex-direction: column; gap: 10px; }
.type-row { display: flex; align-items: center; gap: 12px; }
.type-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.type-name { font-size: 13px; flex: 1; }
.type-count { font-size: 13px; font-weight: 600; }
.type-bar-bg { flex: 2; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.type-bar-fill { height: 100%; border-radius: 3px; transition: width 0.6s ease; }

/* Filters */
.filters {
  display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; align-items: center;
}
.filters label { font-size: 12px; font-weight: 500; color: var(--text-secondary); }
.filter-btn {
  background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text-secondary); font-size: 12px; padding: 5px 12px; cursor: pointer;
  font-family: var(--font); transition: all 0.15s;
}
.filter-btn:hover { border-color: var(--accent); color: var(--text-primary); }
.filter-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }

/* Table */
.table-wrap {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead { background: rgba(255,255,255,0.03); }
th {
  text-align: left; padding: 10px 14px; font-weight: 600; color: var(--text-secondary);
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
  border-bottom: 1px solid var(--border);
}
td { padding: 10px 14px; border-bottom: 1px solid var(--border); }
tr:last-child td { border-bottom: none; }
tr:hover { background: var(--surface-hover); }
.badge {
  display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px;
  border-radius: 4px; text-transform: uppercase;
}
.badge.setpoint_change { background: rgba(79,140,255,0.15); color: var(--accent); }
.badge.constraint_violation { background: rgba(239,68,68,0.15); color: var(--red); }
.badge.mode_transition { background: rgba(234,179,8,0.15); color: var(--yellow); }
.badge.human_intervention { background: rgba(249,115,22,0.15); color: var(--orange); }
.badge.shadow_controls { background: rgba(139,143,154,0.15); color: var(--text-secondary); }
.badge.config_reload { background: rgba(34,197,94,0.15); color: var(--green); }
.shadow-tag {
  font-size: 10px; background: rgba(139,143,154,0.2); color: var(--text-secondary);
  padding: 1px 5px; border-radius: 3px; margin-left: 6px;
}
.severity-nominal { color: var(--green); }
.severity-caution { color: var(--yellow); }
.severity-alert { color: var(--orange); }
.severity-critical { color: var(--red); }
.empty-state { text-align: center; padding: 40px; color: var(--text-secondary); }
.pagination { display: flex; justify-content: center; gap: 8px; padding: 14px; }
.pagination button {
  background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text-secondary); font-size: 12px; padding: 5px 14px; cursor: pointer;
  font-family: var(--font);
}
.pagination button:hover { border-color: var(--accent); color: var(--text-primary); }
.pagination button.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
.pagination .info { font-size: 12px; color: var(--text-secondary); padding: 5px 10px; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>⚙️ <span>PAMCL</span> Audit Dashboard</h1>
    <div class="meta" id="file-info">Loading...</div>
  </header>

  <div class="cards" id="cards"></div>

  <div class="chart-row">
    <div class="chart-box">
      <h3>Constraint Violation Severity</h3>
      <div class="bar-chart" id="severity-chart"></div>
    </div>
    <div class="chart-box">
      <h3>Event Type Distribution</h3>
      <div class="type-list" id="type-chart"></div>
    </div>
  </div>

  <div class="filters" id="filters">
    <label>Filter:</label>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Type</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody id="event-table"></tbody>
    </table>
    <div class="pagination" id="pagination"></div>
  </div>
</div>

<script>
const EVENTS = __EVENTS_JSON__;
const PAGE_SIZE = 50;
let currentPage = 0;
let activeFilters = new Set();
let showShadow = true;

function init() {
  document.getElementById('file-info').textContent =
    `${EVENTS.length} events — __FILE_NAME__`;

  renderCards();
  renderSeverityChart();
  renderTypeChart();
  renderFilters();
  renderTable();
}

function renderCards() {
  const violations = EVENTS.filter(e => e.event_type === 'constraint_violation');
  const shadow = EVENTS.filter(e => e.shadow === true || e.event_type === 'shadow_controls');
  const modes = EVENTS.filter(e => e.event_type === 'mode_transition');
  const reloads = EVENTS.filter(e => e.event_type === 'config_reload');

  const criticals = violations.filter(e => e.severity === 'CRITICAL').length;

  const cards = [
    { label: 'Total Events', value: EVENTS.length, cls: '' },
    { label: 'Violations', value: violations.length, cls: violations.length > 0 ? 'orange' : 'green' },
    { label: 'Critical', value: criticals, cls: criticals > 0 ? 'red' : 'green' },
    { label: 'Mode Changes', value: modes.length, cls: '' },
    { label: 'Shadow Events', value: shadow.length, cls: '' },
    { label: 'Config Reloads', value: reloads.length, cls: reloads.length > 0 ? 'yellow' : '' },
  ];

  document.getElementById('cards').innerHTML = cards.map(c =>
    `<div class="card"><div class="label">${c.label}</div><div class="value ${c.cls}">${c.value}</div></div>`
  ).join('');
}

function renderSeverityChart() {
  const violations = EVENTS.filter(e => e.event_type === 'constraint_violation');
  const counts = { CAUTION: 0, ALERT: 0, CRITICAL: 0 };
  violations.forEach(e => { if (counts[e.severity] !== undefined) counts[e.severity]++; });

  const max = Math.max(1, ...Object.values(counts));
  const colors = { CAUTION: 'var(--yellow)', ALERT: 'var(--orange)', CRITICAL: 'var(--red)' };

  document.getElementById('severity-chart').innerHTML = Object.entries(counts).map(([sev, count]) => {
    const h = Math.max(4, (count / max) * 140);
    return `<div class="bar-col">
      <div class="bar-value">${count}</div>
      <div class="bar" style="height:${h}px;background:${colors[sev]}"></div>
      <div class="bar-label">${sev}</div>
    </div>`;
  }).join('');
}

function renderTypeChart() {
  const types = {};
  EVENTS.forEach(e => { types[e.event_type] = (types[e.event_type] || 0) + 1; });
  const max = Math.max(1, ...Object.values(types));
  const colors = {
    setpoint_change: 'var(--accent)', constraint_violation: 'var(--red)',
    mode_transition: 'var(--yellow)', human_intervention: 'var(--orange)',
    shadow_controls: 'var(--text-secondary)', config_reload: 'var(--green)',
  };

  document.getElementById('type-chart').innerHTML = Object.entries(types)
    .sort((a, b) => b[1] - a[1])
    .map(([type, count]) => {
      const pct = (count / max) * 100;
      const c = colors[type] || 'var(--accent)';
      return `<div class="type-row">
        <div class="type-dot" style="background:${c}"></div>
        <div class="type-name">${type.replace(/_/g, ' ')}</div>
        <div class="type-bar-bg"><div class="type-bar-fill" style="width:${pct}%;background:${c}"></div></div>
        <div class="type-count">${count}</div>
      </div>`;
    }).join('');
}

function renderFilters() {
  const types = [...new Set(EVENTS.map(e => e.event_type))];
  const container = document.getElementById('filters');
  container.innerHTML = '<label>Filter:</label>';

  types.forEach(type => {
    const btn = document.createElement('button');
    btn.className = 'filter-btn';
    btn.textContent = type.replace(/_/g, ' ');
    btn.onclick = () => {
      if (activeFilters.has(type)) { activeFilters.delete(type); btn.classList.remove('active'); }
      else { activeFilters.add(type); btn.classList.add('active'); }
      currentPage = 0;
      renderTable();
    };
    container.appendChild(btn);
  });

  // Shadow toggle
  const shadowBtn = document.createElement('button');
  shadowBtn.className = 'filter-btn active';
  shadowBtn.textContent = 'show shadow';
  shadowBtn.onclick = () => {
    showShadow = !showShadow;
    shadowBtn.classList.toggle('active', showShadow);
    currentPage = 0;
    renderTable();
  };
  container.appendChild(shadowBtn);
}

function getFilteredEvents() {
  let filtered = EVENTS;
  if (activeFilters.size > 0) {
    filtered = filtered.filter(e => activeFilters.has(e.event_type));
  }
  if (!showShadow) {
    filtered = filtered.filter(e => !e.shadow && e.event_type !== 'shadow_controls');
  }
  return filtered;
}

function renderTable() {
  const filtered = getFilteredEvents();
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  currentPage = Math.min(currentPage, totalPages - 1);
  const start = currentPage * PAGE_SIZE;
  const page = filtered.slice(start, start + PAGE_SIZE);

  const tbody = document.getElementById('event-table');
  if (page.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="empty-state">No events match the current filters.</td></tr>';
  } else {
    tbody.innerHTML = page.map(e => {
      const time = e.timestamp_iso || '';
      const shadow = e.shadow ? '<span class="shadow-tag">shadow</span>' : '';
      const badge = `<span class="badge ${e.event_type}">${e.event_type.replace(/_/g, ' ')}</span>${shadow}`;
      const details = formatDetails(e);
      return `<tr><td style="white-space:nowrap;color:var(--text-secondary)">${time}</td><td>${badge}</td><td>${details}</td></tr>`;
    }).join('');
  }

  // Pagination
  const pag = document.getElementById('pagination');
  if (totalPages <= 1) { pag.innerHTML = ''; return; }
  let html = `<span class="info">${start+1}–${Math.min(start+PAGE_SIZE, filtered.length)} of ${filtered.length}</span>`;
  if (currentPage > 0) html += `<button onclick="currentPage=0;renderTable()">«</button><button onclick="currentPage--;renderTable()">‹</button>`;
  if (currentPage < totalPages - 1) html += `<button onclick="currentPage++;renderTable()">›</button><button onclick="currentPage=${totalPages-1};renderTable()">»</button>`;
  pag.innerHTML = html;
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatDetails(e) {
  switch (e.event_type) {
    case 'setpoint_change':
      return `<b>${escapeHtml(e.variable)}</b>: ${escapeHtml(fmt(e.old_value))} → ${escapeHtml(fmt(e.new_value))} <span style="color:var(--text-secondary)">(${escapeHtml(e.agent_id)}, ${escapeHtml(e.reason)})</span>`;
    case 'constraint_violation':
      const cls = `severity-${(e.severity || '').toLowerCase()}`;
      const viol = (e.violations || []).map(escapeHtml).join('; ');
      return `<span class="${cls}"><b>${escapeHtml(e.severity)}</b></span> ${viol}`;
    case 'mode_transition':
      return `Mode ${escapeHtml(e.from_mode)} → ${escapeHtml(e.to_mode)} <span style="color:var(--text-secondary)">(${escapeHtml(e.reason)})</span>`;
    case 'human_intervention':
      return `<b>${escapeHtml(e.operator_id)}</b>: ${escapeHtml(e.action)} <span style="color:var(--text-secondary)">(${escapeHtml(e.reason)})</span>`;
    case 'shadow_controls':
      const keys = Object.keys(e.controls || {}).slice(0, 5).map(escapeHtml).join(', ');
      const extra = Object.keys(e.controls || {}).length > 5 ? '...' : '';
      return `Step ${escapeHtml(e.step)} — ${keys}${extra}`;
    case 'config_reload':
      return `Rules: ${escapeHtml(e.old_rules)} → ${escapeHtml(e.new_rules)} <span style="color:var(--text-secondary)">(${escapeHtml(e.source)})</span>`;
    default:
      return escapeHtml(JSON.stringify(e).slice(0, 120));
  }
}

function fmt(v) {
  if (typeof v === 'number') return v % 1 === 0 ? v : v.toFixed(4);
  return v;
}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


def load_audit_events(path: str | Path) -> List[Dict[str, Any]]:
    """Load all events from a JSONL audit log file.

    Malformed lines are skipped (see AuditLogger.read_all for rationale).
    """
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return events


def build_dashboard_html(events: List[Dict[str, Any]], filename: str) -> str:
    """Build the complete dashboard HTML with embedded event data."""
    events_json = json.dumps(events, ensure_ascii=False, default=str)
    html = _DASHBOARD_HTML.replace("__EVENTS_JSON__", events_json)
    html = html.replace("__FILE_NAME__", filename)
    return html


def serve_dashboard(
    log_path: str | Path,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """
    Start a local HTTP server serving the audit dashboard.

    Parameters
    ----------
    log_path : str | Path
        Path to the JSONL audit log file.
    port : int
        HTTP server port. Default: 8765.
    open_browser : bool
        Whether to open the browser automatically.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        raise FileNotFoundError(f"Audit log not found: {log_path}")

    events = load_audit_events(log_path)
    html = build_dashboard_html(events, log_path.name)
    html_bytes = html.encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        def log_message(self, format, *args):
            pass  # suppress default logging

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"PAMCL Audit Dashboard: {url}")
    print(f"Loaded {len(events)} events from {log_path.name}")
    print("Press Ctrl+C to stop.\n")

    if open_browser:
        import webbrowser
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.shutdown()
