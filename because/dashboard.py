"""
because local dashboard

Starts a lightweight local HTTP server and opens the dashboard in your
default browser. Shows the most recent exception explanation and context
chain, auto-refreshing every 3 seconds.

Usage::

    because dashboard [--port 7331] [--no-open]
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>because dashboard</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --border: #2a2d3e;
    --text: #e2e8f0;
    --muted: #718096;
    --accent: #7c3aed;
    --accent-light: #a78bfa;
    --green: #22c55e;
    --red: #ef4444;
    --orange: #f97316;
    --yellow: #eab308;
    --blue: #3b82f6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px;
    min-height: 100vh;
  }
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
    position: sticky;
    top: 0;
    z-index: 10;
  }
  header h1 { font-size: 18px; font-weight: 700; letter-spacing: -0.3px; }
  header h1 span { color: var(--accent-light); }
  .status {
    margin-left: auto;
    font-size: 12px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; } 50% { opacity: 0.4; }
  }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  .empty {
    text-align: center;
    padding: 80px 24px;
    color: var(--muted);
  }
  .empty h2 { font-size: 20px; margin-bottom: 12px; color: var(--text); }
  .empty code {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 13px;
  }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
  }
  .card.full { grid-column: 1 / -1; }
  .card h2 {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
  }
  .root-cause { font-size: 16px; line-height: 1.6; color: var(--text); }
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 10px;
    border-radius: 99px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.4px;
    margin-left: 8px;
    vertical-align: middle;
  }
  .badge.high { background: #14532d; color: #86efac; }
  .badge.medium { background: #713f12; color: #fde68a; }
  .badge.low { background: #7f1d1d; color: #fca5a5; }
  .factors { list-style: none; }
  .factors li {
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    color: var(--text);
    line-height: 1.5;
  }
  .factors li:last-child { border-bottom: none; }
  .factors li::before { content: "•"; color: var(--accent-light); margin-right: 8px; }
  .fix {
    background: #1e1b4b;
    border: 1px solid #3730a3;
    border-radius: 8px;
    padding: 14px 16px;
    color: #c7d2fe;
    line-height: 1.6;
  }
  .pattern {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 12px 0;
    border-bottom: 1px solid var(--border);
  }
  .pattern:last-child { border-bottom: none; }
  .pattern-name { font-weight: 600; color: var(--text); }
  .pattern-desc { color: var(--muted); font-size: 13px; margin-top: 2px; }
  .evidence { margin-top: 6px; }
  .evidence li {
    font-size: 12px;
    color: var(--muted);
    padding: 2px 0;
  }
  .evidence li::before { content: "→ "; color: var(--accent-light); }
  .swallowed {
    background: #431407;
    border: 1px solid #9a3412;
    border-radius: 6px;
    padding: 10px 14px;
    margin-bottom: 8px;
    font-family: monospace;
    font-size: 13px;
    color: #fed7aa;
  }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th {
    text-align: left;
    padding: 8px 10px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    color: var(--text);
    vertical-align: top;
  }
  tr:last-child td { border-bottom: none; }
  tr.fail td { color: var(--red); }
  tr.ok td { color: var(--text); }
  .status-dot {
    display: inline-block;
    width: 7px; height: 7px; border-radius: 50%;
    margin-right: 6px;
  }
  .ok .status-dot { background: var(--green); }
  .fail .status-dot { background: var(--red); }
  .mono { font-family: monospace; font-size: 12px; }
  .ts { color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1><span>because</span> dashboard</h1>
  <div class="status"><span class="dot"></span> live · refreshing every 3s</div>
</header>
<main id="root">
  <div class="empty">
    <h2>No data yet</h2>
    <p>Run <code>because explain</code> or call <code>because.explain_async(exc)</code> in your app.</p>
  </div>
</main>
<script>
async function fetchData() {
  try {
    const r = await fetch('/api/last');
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

function badge(confidence) {
  return `<span class="badge ${confidence}">${confidence}</span>`;
}

function render(data) {
  if (!data || (!data.explanation && !data.chain)) {
    document.getElementById('root').innerHTML = `
      <div class="empty">
        <h2>No data yet</h2>
        <p>Run <code>because explain</code> or call <code>because.explain_async(exc)</code> in your app.</p>
      </div>`;
    return;
  }

  const ex = data.explanation || {};
  const chain = data.chain || {};
  const ops = chain.operations || [];
  const swallowed = chain.swallowed || [];
  const patterns = chain.patterns || [];

  let html = '<div class="grid">';

  // Root cause
  if (ex.root_cause) {
    html += `
      <div class="card full">
        <h2>Root cause</h2>
        <div class="root-cause">${esc(ex.root_cause)} ${badge(ex.confidence || 'low')}</div>
      </div>`;
  }

  // Contributing factors
  if (ex.contributing_factors && ex.contributing_factors.length) {
    html += `<div class="card">
      <h2>Contributing factors</h2>
      <ul class="factors">
        ${ex.contributing_factors.map(f => `<li>${esc(f)}</li>`).join('')}
      </ul>
    </div>`;
  }

  // Suggested fix
  if (ex.suggested_fix) {
    html += `<div class="card">
      <h2>Suggested fix</h2>
      <div class="fix">${esc(ex.suggested_fix)}</div>
    </div>`;
  }

  // Patterns
  if (patterns.length) {
    html += `<div class="card full">
      <h2>Pattern matches</h2>
      ${patterns.map(p => `
        <div class="pattern">
          <div>
            <div class="pattern-name">${esc(p.name)} <span class="badge ${p.confidence === 'likely_cause' ? 'high' : 'medium'}">${esc(p.confidence)}</span></div>
            <div class="pattern-desc">${esc(p.description)}</div>
            ${p.evidence && p.evidence.length ? `<ul class="evidence">${p.evidence.map(e => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
          </div>
        </div>`).join('')}
    </div>`;
  }

  // Swallowed exceptions
  if (swallowed.length) {
    html += `<div class="card full">
      <h2>Caught-and-swallowed (${swallowed.length})</h2>
      ${swallowed.map(s => `<div class="swallowed">${esc(s.exc_type)}: ${esc(s.message)}</div>`).join('')}
    </div>`;
  }

  // Operations timeline
  if (ops.length) {
    html += `<div class="card full">
      <h2>Recent operations (${ops.length})</h2>
      <table>
        <thead><tr><th>Status</th><th>Type</th><th>Duration</th><th>Detail</th></tr></thead>
        <tbody>
          ${ops.slice(-50).map(op => {
            const ok = op.success;
            const dur = op.duration_ms != null ? op.duration_ms.toFixed(1) + 'ms' : '—';
            const detail = opDetail(op);
            return `<tr class="${ok ? 'ok' : 'fail'}">
              <td><span class="status-dot"></span>${ok ? 'ok' : 'FAIL'}</td>
              <td class="mono">${esc(op.op_type)}</td>
              <td class="mono">${dur}</td>
              <td class="mono">${esc(detail)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
  }

  html += '</div>';
  document.getElementById('root').innerHTML = html;
}

function opDetail(op) {
  const m = op.metadata || {};
  if (op.op_type === 'db_query') return (m.statement || '').slice(0, 80) + (m.error ? '  error=' + m.error : '');
  if (op.op_type === 'http_request') return (m.method || '') + ' ' + (m.url || '') + (m.error ? '  error=' + m.error : m.status_code ? '  ' + m.status_code : '');
  if (op.op_type === 'exception_swallowed') return m.exc_type || '';
  if (op.op_type === 'log') return '[' + (m.level || '') + '] ' + (m.message || '').slice(0, 80);
  return JSON.stringify(m).slice(0, 80);
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function tick() {
  const data = await fetchData();
  render(data);
}

tick();
setInterval(tick, 3000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/last":
            self._serve_api()
        else:
            self._serve_ui()

    def _serve_ui(self):
        body = _HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_api(self):
        from because.cli import load_last_explanation, load_last_chain
        data = {
            "explanation": load_last_explanation(),
            "chain": load_last_chain(),
        }
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress access log noise


def run(port: int = 7331, open_browser: bool = True) -> None:
    """Start the because dashboard server."""
    server = HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}"

    print(f"because dashboard running at {url}")
    print("Press Ctrl-C to stop.\n")

    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
