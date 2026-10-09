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
  .card-head { display: flex; justify-content: space-between; align-items: center; gap: 10px;
               margin-bottom: 12px; }
  .card-head h2 { margin: 0; }
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
  button:disabled { opacity: .5; cursor: default; }
  button.small { padding: 5px 10px; font-size: .8rem; }
  button.plain { background: transparent; color: var(--accent); border: 1px solid var(--border); }
  button.danger { background: var(--danger); }
  .err { color: var(--danger); font-size: .85rem; margin: 10px 0 0; }
  .hidden { display: none !important; }
  .muted { color: var(--muted); font-size: .85rem; }
  .note { color: var(--muted); font-size: .85rem; margin: 0 0 10px; }
  .row { display: flex; justify-content: space-between; gap: 10px; padding: 7px 0;
         border-bottom: 1px solid var(--border); font-size: .85rem; }
  .row:last-child { border-bottom: none; }
  .row span:first-child { color: var(--muted); flex: 0 0 auto; }
  .row span:last-child { text-align: right; overflow-wrap: anywhere; }
  .entry { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: .85rem; }
  .entry:last-child { border-bottom: none; }
  .entry .song { overflow-wrap: anywhere; }
  .entry .meta { color: var(--muted); font-size: .8rem; margin-top: 2px; }
  .job { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
  .actions { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; font-size: .8rem; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Narjo Sing helper is running</h1>
  <p class="sub">Version __VERSION__</p>

  <div class="card" id="connect-card">
    <p>In Narjo, open <strong>Settings › Integrations › Sing</strong> and enter this
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
    <div class="row"><span>Computer</span><span id="s-device"></span></div>
    <div class="row"><span>How it works here</span><span id="s-mode"></span></div>
    <div class="row"><span>Fast version</span><span id="s-fast"></span></div>
    <div class="row"><span>Better version</span><span id="s-best"></span></div>
    <div class="row"><span>Songs in your library</span><span id="s-library"></span></div>
    <div class="row"><span>Songs waiting</span><span id="s-queue"></span></div>
  </div>

  <div id="queue-card" class="card hidden">
    <div class="card-head">
      <h2>Being prepared</h2>
      <button id="pause-btn" type="button" class="small">Pause</button>
    </div>
    <p id="paused-note" class="note hidden">Paused. Nothing new starts, and the song in progress waits
       exactly where it is, until you press Resume.</p>
    <div id="queue-rows"></div>
    <p id="queue-empty" class="muted hidden">Nothing is being prepared.</p>
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
  var pauseBtn = document.getElementById("pause-btn");
  var pausedNote = document.getElementById("paused-note");
  var timer = null;
  var lastResult = null;
  var confirmingJob = null;

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
    var s = Math.round(seconds);
    if (s < 60) return s + " s";
    return Math.round(s / 60) + " min";
  }

  function setText(id, text) {
    document.getElementById(id).textContent = text;
  }

  function post(path) {
    return fetch(path, { method: "POST", headers: { "X-Narjo-Sing-Key": keyInput.value } });
  }

  function qualityName(quality) {
    return quality === "best" ? "Best" : "Fast";
  }

  function perSong(secondsPerMinute) {
    var minutes = Math.max(1, Math.ceil(secondsPerMinute * 4 / 60));
    return "about " + minutes + " min for a 4-minute song";
  }

  function stateLabel(health) {
    if (health.state === "benchmarking") return "Measuring this computer's speed (a few minutes the first time)";
    if (health.state === "error") return "Problem: " + (health.error || "unknown");
    return health.paused ? "Paused" : "Ready";
  }

  function modeLabel(mode) {
    if (mode === "single") return "Uses one model for everything";
    if (mode === "two-tier") return "Fast version first, better version later";
    if (mode === "fast-only") return "Fast version only";
    return "—";
  }

  function fastLabel(health) {
    return health.fastModel + (health.fastSecondsPerMinute != null ? " · " + perSong(health.fastSecondsPerMinute) : "");
  }

  function bestLabel(health) {
    var name = health.configuredBestModel || health.bestModel || "—";
    if (health.bestStatus === "measured" && health.bestSecondsPerMinute != null) {
      return name + " · " + perSong(health.bestSecondsPerMinute);
    }
    if (health.bestStatus === "tooSlow") return name + " · too slow on this computer (over 2 hours a song)";
    if (health.bestStatus === "unavailable") return name + " · not available (it failed to install)";
    if (health.bestStatus === "pending") return name + " · measuring…";
    return name;
  }

  function describeJob(job, paused) {
    var parts = [qualityName(job.quality)];
    if (job.state === "running") {
      parts.push((paused ? "paused at " : "preparing, ") + Math.round(job.progress * 100) + "%");
      if (!paused && job.etaSeconds != null) parts.push("about " + fmtEta(job.etaSeconds) + " left");
    } else {
      parts.push(paused ? "waiting (paused)" : "waiting");
    }
    return parts.join(" · ");
  }

  function smallButton(label, style, onClick) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "small " + style;
    button.textContent = label;
    button.addEventListener("click", onClick);
    return button;
  }

  function cancelControl(job) {
    var box = document.createElement("div");
    box.className = "actions";
    if (confirmingJob === job.id) {
      var ask = document.createElement("span");
      ask.textContent = "Cancel this song?";
      box.appendChild(ask);
      box.appendChild(smallButton("Yes", "danger", function () {
        confirmingJob = null;
        post("/v1/jobs/" + encodeURIComponent(job.id) + "/cancel").then(poll, poll);
      }));
      box.appendChild(smallButton("No", "plain", function () {
        confirmingJob = null;
        rerender();
      }));
    } else {
      box.appendChild(smallButton("Cancel", "plain", function () {
        confirmingJob = job.id;
        rerender();
      }));
    }
    return box;
  }

  function addJob(container, job, paused) {
    var entry = document.createElement("div");
    entry.className = "entry job";
    var text = document.createElement("div");
    var songEl = document.createElement("div");
    songEl.className = "song";
    songEl.textContent = songLabel(job.relPath);
    var metaEl = document.createElement("div");
    metaEl.className = "meta";
    metaEl.textContent = describeJob(job, paused);
    text.appendChild(songEl);
    text.appendChild(metaEl);
    entry.appendChild(text);
    entry.appendChild(cancelControl(job));
    container.appendChild(entry);
  }

  function render(health, queue) {
    setText("s-state", stateLabel(health));
    setText("s-device", health.device || "—");
    setText("s-mode", modeLabel(health.mode));
    setText("s-fast", fastLabel(health));
    setText("s-best", bestLabel(health));
    setText("s-library", health.libraryFiles);
    setText("s-queue", health.queueLength);
    statusCard.classList.remove("hidden");

    var paused = !!queue.paused;
    pauseBtn.textContent = paused ? "Resume" : "Pause";
    pausedNote.classList.toggle("hidden", !paused);
    var queueRows = document.getElementById("queue-rows");
    var queueEmpty = document.getElementById("queue-empty");
    clearChildren(queueRows);
    if (!queue.jobs.some(function (job) { return job.id === confirmingJob; })) confirmingJob = null;
    queueEmpty.classList.toggle("hidden", queue.jobs.length !== 0);
    queue.jobs.forEach(function (job) { addJob(queueRows, job, paused); });
    queueCard.classList.remove("hidden");

    var recentRows = document.getElementById("recent-rows");
    var recentEmpty = document.getElementById("recent-empty");
    clearChildren(recentRows);
    recentEmpty.classList.toggle("hidden", queue.recent.length !== 0);
    queue.recent.forEach(function (item) {
      addEntry(recentRows, songLabel(item.relPath),
               (item.quality ? qualityName(item.quality) : "—") + " · " + fmtWhen(item.finished));
    });
    recentCard.classList.remove("hidden");
  }

  function rerender() {
    if (lastResult) render(lastResult.health, lastResult.queue);
  }

  function hideCards() {
    statusCard.classList.add("hidden");
    queueCard.classList.add("hidden");
    recentCard.classList.add("hidden");
  }

  function poll() {
    var key = keyInput.value;
    if (!key) return Promise.resolve();
    var headers = { "X-Narjo-Sing-Key": key };
    return fetch("/v1/health", { headers: headers }).then(function (healthResp) {
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
      lastResult = result;
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

  pauseBtn.addEventListener("click", function () {
    var paused = !!(lastResult && lastResult.queue.paused);
    pauseBtn.disabled = true;
    post(paused ? "/v1/queue/resume" : "/v1/queue/pause").then(poll, poll).then(function () {
      pauseBtn.disabled = false;
    });
  });

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
