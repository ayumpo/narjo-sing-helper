"""The browser status page served at GET /; static HTML, no server-side data beyond the version."""

from __future__ import annotations

import html

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Narjo Sing helper</title>
<style>
  :root {
    --bg: #f4f4f7; --fg: #1b1b1f; --card: #ffffff; --border: #dedee4;
    --accent: #5b57d1; --muted: #6b6b76; --danger: #c0392b; --input-bg: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14141a; --fg: #f0f0f2; --card: #1e1e26; --border: #2d2d36;
      --accent: #9490ff; --muted: #9a9aa6; --danger: #ff6b6b; --input-bg: #15151c;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--fg); }
  .wrap { max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 1.4rem; margin: 0 0 4px; }
  h2 { font-size: 1rem; margin: 0 0 12px; }
  .sub { color: var(--muted); margin: 0 0 20px; font-size: .9rem; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
          padding: 16px; margin-bottom: 16px; }
  .card p { font-size: .9rem; line-height: 1.5; margin: 0 0 12px; }
  code { background: var(--bg); padding: 1px 5px; border-radius: 4px; font-size: .85em; }
  label { display: block; font-size: .85rem; margin-bottom: 6px; color: var(--muted); }
  input[type=password] { width: 100%; padding: 8px 10px; border: 1px solid var(--border);
                          border-radius: 8px; background: var(--input-bg); color: var(--fg);
                          font-size: .95rem; margin-bottom: 10px; }
  .remember { display: flex; align-items: center; gap: 6px; font-size: .85rem; color: var(--fg);
              margin-bottom: 12px; }
  .remember input { width: auto; margin: 0; }
  button { background: var(--accent); color: #fff; border: none; border-radius: 8px;
           padding: 9px 16px; font-size: .9rem; cursor: pointer; }
  button:active { opacity: .85; }
  .err { color: var(--danger); font-size: .85rem; margin: 10px 0 0; }
  .hidden { display: none !important; }
  .muted { color: var(--muted); font-size: .85rem; }
  .row { display: flex; justify-content: space-between; gap: 10px; padding: 7px 0;
         border-bottom: 1px solid var(--border); font-size: .85rem; }
  .row:last-child { border-bottom: none; }
  .row span:first-child { color: var(--muted); flex: 0 0 auto; }
  .row span:last-child { text-align: right; overflow-wrap: anywhere; }
  .entry { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: .85rem; }
  .entry:last-child { border-bottom: none; }
  .entry .song { overflow-wrap: anywhere; }
  .entry .meta { color: var(--muted); font-size: .8rem; margin-top: 2px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Narjo Sing helper is running</h1>
  <p class="sub">Version __VERSION__</p>

  <div class="card" id="connect-card">
    <p>In Narjo, open <strong>Settings &rsaquo; Integrations &rsaquo; Sing</strong> and enter this
       address and the access key. The key is in <code>stems/access-key.txt</code> next to your
       compose file, or in the container log line <code>Access key</code>.</p>
    <label for="key-input">Access key</label>
    <input id="key-input" type="password" autocomplete="off" placeholder="paste the access key to see live status">
    <label class="remember"><input type="checkbox" id="remember"> Remember on this browser</label>
    <button id="connect-btn" type="button">Show status</button>
    <p id="auth-error" class="err hidden">Wrong access key</p>
  </div>

  <div id="status-card" class="card hidden">
    <h2>Status</h2>
    <div class="row"><span>State</span><span id="s-state"></span></div>
    <div class="row"><span>Device</span><span id="s-device"></span></div>
    <div class="row"><span>Mode</span><span id="s-mode"></span></div>
    <div class="row"><span>Fast model</span><span id="s-fast"></span></div>
    <div class="row"><span>Best model</span><span id="s-best"></span></div>
    <div class="row"><span>Library files</span><span id="s-library"></span></div>
    <div class="row"><span>Queue length</span><span id="s-queue"></span></div>
  </div>

  <div id="queue-card" class="card hidden">
    <h2>Queue</h2>
    <div id="queue-rows"></div>
    <p id="queue-empty" class="muted hidden">Nothing queued.</p>
  </div>

  <div id="recent-card" class="card hidden">
    <h2>Recently prepared</h2>
    <div id="recent-rows"></div>
    <p id="recent-empty" class="muted hidden">Nothing prepared yet.</p>
  </div>
</div>
<script>
(function () {
  "use strict";
  var KEY_STORAGE = "narjoSingKey";
  var keyInput = document.getElementById("key-input");
  var remember = document.getElementById("remember");
  var authError = document.getElementById("auth-error");
  var statusCard = document.getElementById("status-card");
  var queueCard = document.getElementById("queue-card");
  var recentCard = document.getElementById("recent-card");
  var timer = null;

  try {
    var saved = localStorage.getItem(KEY_STORAGE);
    if (saved) { keyInput.value = saved; remember.checked = true; }
  } catch (e) { /* storage blocked (private mode); the key field just starts empty */ }

  function songLabel(relPath) {
    var parts = String(relPath).split("/").filter(Boolean);
    var last = parts.slice(-3);
    while (last.length < 3) last.unshift("—");
    return last.join(" / ");
  }

  function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function addRow(container, cells) {
    var row = document.createElement("div");
    row.className = "row";
    cells.forEach(function (text) {
      var span = document.createElement("span");
      span.textContent = text;
      row.appendChild(span);
    });
    container.appendChild(row);
  }

  function addEntry(container, song, metaText) {
    var entry = document.createElement("div");
    entry.className = "entry";
    var songEl = document.createElement("div");
    songEl.className = "song";
    songEl.textContent = song;
    var metaEl = document.createElement("div");
    metaEl.className = "meta";
    metaEl.textContent = metaText;
    entry.appendChild(songEl);
    entry.appendChild(metaEl);
    container.appendChild(entry);
  }

  function fmtWhen(seconds) {
    try { return new Date(seconds * 1000).toLocaleString(); } catch (e) { return String(seconds); }
  }

  function fmtEta(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    var s = Math.round(seconds);
    if (s < 60) return s + "s";
    return Math.round(s / 60) + "m";
  }

  function setText(id, text) {
    document.getElementById(id).textContent = text;
  }

  function render(health, queue) {
    setText("s-state", health.state + (health.error ? " (" + health.error + ")" : ""));
    setText("s-device", health.device || "—");
    setText("s-mode", health.mode || "—");
    setText("s-fast", health.fastModel + (health.fastSecondsPerMinute != null ?
      " (" + health.fastSecondsPerMinute + " s/min)" : ""));
    setText("s-best", health.bestModel ? health.bestModel + (health.bestSecondsPerMinute != null ?
      " (" + health.bestSecondsPerMinute + " s/min)" : "") : "—");
    setText("s-library", health.libraryFiles);
    setText("s-queue", health.queueLength);
    statusCard.classList.remove("hidden");

    var queueRows = document.getElementById("queue-rows");
    var queueEmpty = document.getElementById("queue-empty");
    clearChildren(queueRows);
    if (queue.jobs.length === 0) {
      queueEmpty.classList.remove("hidden");
    } else {
      queueEmpty.classList.add("hidden");
      queue.jobs.forEach(function (job) {
        addRow(queueRows, [songLabel(job.relPath), job.model, job.priority, job.state,
          Math.round(job.progress * 100) + "%", fmtEta(job.etaSeconds)]);
      });
    }
    queueCard.classList.remove("hidden");

    var recentRows = document.getElementById("recent-rows");
    var recentEmpty = document.getElementById("recent-empty");
    clearChildren(recentRows);
    if (queue.recent.length === 0) {
      recentEmpty.classList.remove("hidden");
    } else {
      recentEmpty.classList.add("hidden");
      queue.recent.forEach(function (item) {
        addEntry(recentRows, songLabel(item.relPath), (item.quality || "—") + " · " + fmtWhen(item.finished));
      });
    }
    recentCard.classList.remove("hidden");
  }

  function hideCards() {
    statusCard.classList.add("hidden");
    queueCard.classList.add("hidden");
    recentCard.classList.add("hidden");
  }

  function poll() {
    var key = keyInput.value;
    if (!key) return;
    var headers = { "X-Narjo-Sing-Key": key };
    fetch("/v1/health", { headers: headers }).then(function (healthResp) {
      if (healthResp.status === 401) { authError.classList.remove("hidden"); hideCards(); return null; }
      return healthResp.json().then(function (health) {
        return fetch("/v1/queue", { headers: headers }).then(function (queueResp) {
          if (queueResp.status === 401) { authError.classList.remove("hidden"); hideCards(); return null; }
          return queueResp.json().then(function (queue) { return { health: health, queue: queue }; });
        });
      });
    }).then(function (result) {
      if (!result) return;
      authError.classList.add("hidden");
      render(result.health, result.queue);
    }).catch(function () { /* a transient network error leaves the last good render in place */ });
  }

  function startPolling() {
    poll();
    if (timer) clearInterval(timer);
    timer = setInterval(function () {
      if (document.visibilityState === "visible") poll();
    }, 5000);
  }

  document.getElementById("connect-btn").addEventListener("click", function () {
    try {
      if (remember.checked) localStorage.setItem(KEY_STORAGE, keyInput.value);
      else localStorage.removeItem(KEY_STORAGE);
    } catch (e) { /* storage blocked (private mode); the key still works for this page load */ }
    startPolling();
  });

  if (keyInput.value) startPolling();
})();
</script>
</body>
</html>
"""


def render_index(version: str) -> str:
    return _PAGE.replace("__VERSION__", html.escape(version))
