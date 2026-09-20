// Mock one-origin server for fe_edit_agent_e2e_test.
//
// Plain Node http (no deps). Serves:
//   - GET /                       → frontend/dist/frontend/browser/index.html (SPA shell)
//   - GET /<asset>                → static file from dist
//   - GET  /api/health            → happy-path health
//   - GET  /api/agents            → 7 agents with shared-prefix collisions
//   - GET  /api/sources           → 4 sources (S1-S4)
//   - GET  /api/sources/{id}      → one source
//   - POST /api/sources           → capture POST body, return synthesized Source
//   - PUT  /api/sources/{id}      → capture PUT body, return synthesized Source
//   - DELETE /api/sources/{id}    → 200 (no-op)
//   - GET  /api/settings/plane    → { enabled: false }
//   - GET  /api/migration/availability → { postgres_env_set: false }
//   - GET  /api/notifications/stream → 200 text/event-stream, stay open with heartbeats
//
// Every captured PUT/POST body is appended verbatim to a JSON log file so the
// harness can read them deterministically (no in-memory race).

'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const DIST_DIR = path.resolve(__dirname, '../../../frontend/dist/frontend/browser');
const EVIDENCE_DIR = process.env.FE_SMOKE_EVIDENCE_DIR ||
  '/tmp/fe-edit-agent-smoke-20260916';
const CAPTURE_FILE = path.join(EVIDENCE_DIR, 'captured-requests.jsonl');

// ── fixtures ─────────────────────────────────────────────────────────────
const AGENTS = [
  { id: 'agent-leader',       name: 'leader',       description: '', icon: 'star',     color: '#fbbf24' },
  { id: 'agent-coder',        name: 'coder',        description: '', icon: 'code',     color: '#10b981' },
  { id: 'agent-code-reviewer',name: 'code-reviewer',description: '', icon: 'check',    color: '#3b82f6' },
  { id: 'agent-reviewer',     name: 'reviewer',     description: '', icon: 'eye',      color: '#ef4444' },
  { id: 'agent-tester',       name: 'tester',       description: '', icon: 'lab',      color: '#a855f7' },
  { id: 'agent-wanderer',     name: 'wanderer',     description: '', icon: 'compass',  color: '#06b6d4' },
  { id: 'agent-maintenancer', name: 'maintenancer', description: '', icon: 'wrench',   color: '#f97316' },
];

const SOURCES = [
  // S1 — slack, default_agent preselected to leader (the core regression target)
  {
    source_id: 'src-slack-s1',
    source_type: 'slack',
    name: 'S1 Slack Production',
    config: { default_agent: 'agent-leader', channel_require_mention: true },
    credentials: {},
    enabled: true,
    autostart: true,
    status: 'stopped',
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-10T10:00:00Z',
    has_credentials: true,
  },
  // S2 — telegram, default_agent null (the null → select scenario)
  {
    source_id: 'src-telegram-s2',
    source_type: 'telegram',
    name: 'S2 Telegram Bot',
    config: { default_agent: null, polling_enabled: true, polling_timeout: 30 },
    credentials: {},
    enabled: true,
    autostart: true,
    status: 'stopped',
    created_at: '2026-09-02T10:00:00Z',
    updated_at: '2026-09-11T10:00:00Z',
    has_credentials: true,
  },
  // S3 — slack, default_agent preselected to wanderer (DIFFERENT default from S1)
  {
    source_id: 'src-slack-s3',
    source_type: 'slack',
    name: 'S3 Slack Staging',
    config: { default_agent: 'agent-wanderer', channel_require_mention: false },
    credentials: {},
    enabled: true,
    autostart: true,
    status: 'stopped',
    created_at: '2026-09-03T10:00:00Z',
    updated_at: '2026-09-12T10:00:00Z',
    has_credentials: true,
  },
  // S4 — webhook (no default_agent — exercise a non-agent source type)
  {
    source_id: 'src-webhook-s4',
    source_type: 'webhook',
    name: 'S4 Webhook Receiver',
    config: { webhook_url: '/hook/s4' },
    credentials: {},
    enabled: true,
    autostart: true,
    status: 'stopped',
    created_at: '2026-09-04T10:00:00Z',
    updated_at: '2026-09-13T10:00:00Z',
    has_credentials: false,
  },
];

// ── request capture (JSONL) ───────────────────────────────────────────────
function ensureEvidenceDir() {
  if (!fs.existsSync(EVIDENCE_DIR)) {
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  }
  // Start fresh on each server boot.
  fs.writeFileSync(CAPTURE_FILE, '');
}

function captureRequest(line) {
  ensureEvidenceDir();
  fs.appendFileSync(CAPTURE_FILE, JSON.stringify(line) + '\n');
}

// ── static ───────────────────────────────────────────────────────────────
function serveStatic(req, res) {
  const safe = (p) => p.replace(/^\/+/, '');
  let rel = safe(req.url.split('?')[0]);
  if (rel === '' || rel === '/') {
    rel = 'index.html';
  }
  const filePath = path.join(DIST_DIR, rel);
  if (!filePath.startsWith(DIST_DIR)) {
    res.writeHead(403).end();
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      // SPA fallback — Angular client-side routing
      fs.readFile(path.join(DIST_DIR, 'index.html'), (e2, d2) => {
        if (e2) {
          res.writeHead(404).end('not found');
          return;
        }
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end(d2);
      });
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    const mime = (
      ext === '.js'  ? 'application/javascript' :
      ext === '.mjs' ? 'application/javascript' :
      ext === '.css' ? 'text/css' :
      ext === '.html'? 'text/html; charset=utf-8' :
      ext === '.json'? 'application/json' :
      ext === '.svg' ? 'image/svg+xml' :
      ext === '.woff'? 'font/woff' :
      ext === '.woff2'? 'font/woff2' :
      ext === '.ico' ? 'image/x-icon' :
      'application/octet-stream'
    );
    res.writeHead(200, { 'Content-Type': mime }).end(data);
  });
}

// ── json helpers ─────────────────────────────────────────────────────────
function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let buf = '';
    req.setEncoding('utf8');
    req.on('data', (chunk) => { buf += chunk; });
    req.on('end', () => {
      if (!buf) return resolve({});
      try { resolve(JSON.parse(buf)); }
      catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

// ── router ───────────────────────────────────────────────────────────────
async function handleApi(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const p = url.pathname;
  const method = req.method || 'GET';

  res.setHeader('Access-Control-Allow-Origin', '*');

  if (p === '/api/health') {
    return json(res, 200, { status: 'ok', uptime_seconds: 12345, version: 'mock-1.0.0' });
  }
  if (p === '/api/agents' && method === 'GET') {
    return json(res, 200, { agents: AGENTS });
  }
  if (p === '/api/sources' && method === 'GET') {
    return json(res, 200, { sources: SOURCES });
  }
  if (p === '/api/sources' && method === 'POST') {
    const body = await readJsonBody(req).catch(() => ({}));
    captureRequest({ method, path: p, scenario: req.headers['x-smoke-scenario'] || null, body, ts: Date.now() });
    const created = {
      source_id: body.source_id || 'src-new',
      source_type: body.source_type || 'slack',
      name: body.name || 'untitled',
      config: body.config || {},
      credentials: body.credentials || {},
      enabled: body.enabled !== false,
      autostart: body.autostart !== false,
      status: 'stopped',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      has_credentials: !!(body.credentials && Object.keys(body.credentials).length > 0),
    };
    return json(res, 200, created);
  }
  const srcMatch = p.match(/^\/api\/sources\/([^/]+)$/);
  if (srcMatch) {
    const id = decodeURIComponent(srcMatch[1]);
    if (method === 'GET') {
      const s = SOURCES.find((x) => x.source_id === id);
      if (!s) return json(res, 404, { detail: { code: 'NOT_FOUND', message: 'no such source' } });
      return json(res, 200, s);
    }
    if (method === 'PUT') {
      const body = await readJsonBody(req).catch(() => ({}));
      captureRequest({ method, path: p, scenario: req.headers['x-smoke-scenario'] || null, source_id: id, body, ts: Date.now() });
      const updated = {
        ...(SOURCES.find((x) => x.source_id === id) || SOURCES[0]),
        name: body.name ?? (SOURCES.find((x) => x.source_id === id) || SOURCES[0]).name,
        config: body.config ?? {},
        enabled: body.enabled !== false,
        autostart: body.autostart !== false,
        updated_at: new Date().toISOString(),
        has_credentials: body.credentials
          ? Object.keys(body.credentials).length > 0
          : true,
      };
      return json(res, 200, updated);
    }
    if (method === 'DELETE') {
      return json(res, 200, { deleted: true, source_id: id });
    }
  }
  if (p === '/api/settings/plane') {
    return json(res, 200, { enabled: false, url: '' });
  }
  if (p === '/api/migration/availability') {
    return json(res, 200, { postgres_env_set: false });
  }
  if (p === '/api/notifications/stream') {
    // SSE: stay open, send a comment heartbeat every 25s. EventSource will
    // reconnect if we drop, so silence is safe.
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    res.write(': hb\n\n');
    const iv = setInterval(() => res.write(': hb\n\n'), 25000);
    req.on('close', () => clearInterval(iv));
    return; // do NOT end()
  }
  // Default: 404 with detail envelope so FE handlers don't crash hard
  return json(res, 404, { detail: { code: 'NOT_FOUND', message: `no mock for ${p}` } });
}

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

// ── server lifecycle ─────────────────────────────────────────────────────
let server = null;

function start(port = 10180) {
  ensureEvidenceDir();
  server = http.createServer(async (req, res) => {
    try {
      const url = req.url || '/';
      if (url.startsWith('/api/')) {
        await handleApi(req, res);
      } else {
        serveStatic(req, res);
      }
    } catch (err) {
      console.error('[mock_server] handler error:', err);
      try { res.writeHead(500).end('mock server error'); } catch (_) {}
    }
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => {
      const addr = server.address();
      console.log(`[mock_server] listening on http://127.0.0.1:${addr.port}`);
      resolve(addr);
    });
  });
}

function stop() {
  return new Promise((resolve) => {
    if (!server) return resolve();
    server.close(() => resolve());
  });
}

module.exports = { start, stop, AGENTS, SOURCES, DIST_DIR, CAPTURE_FILE, EVIDENCE_DIR };

if (require.main === module) {
  // CLI mode for debugging: `node mock_server.mjs 10180`
  const port = parseInt(process.argv[2] || '10180', 10);
  start(port).catch((e) => {
    console.error('[mock_server] failed to start:', e);
    process.exit(1);
  });
}
