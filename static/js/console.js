(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var NAME = document.querySelector('.console-wrap').dataset.instance;
  var API = '/api/instances/' + encodeURIComponent(NAME);
  var MAX_LINES = 2000;
  var HISTORY_KEY = 'mdev.cmdhistory.' + NAME;

  $('t-download').href = API + '/console/download';

  var logEl = $('log');
  var autoscroll = true;
  var manualPause = false;
  var filter = '';
  var queue = [];
  var frame = 0;
  var source = null;

  function classify(entry) {
    if (entry.s === 'panel') { return 'l-panel'; }
    if (entry.s === 'cmd') { return 'l-cmd'; }
    var text = entry.t;
    var cls = entry.s === 'history' ? 'l-hist' : '';
    if (/^\s+at \S|^Caused by:|^\s*\.\.\. \d+ more/.test(text)) { return 'l-stack'; }
    if (/[ \/](ERROR|FATAL|SEVERE)\]/.test(text)) { return 'l-err'; }
    if (/[ \/]WARN(ING)?\]/.test(text)) { return 'l-warn'; }
    return cls;
  }

  function matches(text) { return !filter || text.toLowerCase().indexOf(filter) !== -1; }

  function flush() {
    frame = 0;
    if (!queue.length) { return; }
    var frag = document.createDocumentFragment();
    queue.forEach(function (entry) {
      var el = document.createElement('div');
      el.className = 'line ' + classify(entry);
      el.textContent = entry.t;
      if (!matches(entry.t)) { el.hidden = true; }
      frag.appendChild(el);
    });
    queue = [];
    logEl.appendChild(frag);
    while (logEl.childElementCount > MAX_LINES) { logEl.removeChild(logEl.firstChild); }
    updateCount();
    if (autoscroll) { logEl.scrollTop = logEl.scrollHeight; }
  }

  function enqueue(entries) {
    queue.push.apply(queue, entries);
    if (!frame) { frame = requestAnimationFrame(flush); }
  }

  function clearView() { queue = []; logEl.textContent = ''; updateCount(); }

  function setConn(on) {
    var el = $('conn');
    el.dataset.on = on ? '1' : '0';
    el.textContent = on ? 'live' : 'reconnecting';
  }

  function connect() {
    source = new EventSource(API + '/console/stream');
    source.onopen = function () { setConn(true); };
    source.addEventListener('lines', function (ev) {
      try { enqueue(JSON.parse(ev.data)); } catch (e) { /* ignore a bad frame */ }
    });
    source.addEventListener('reset', clearView);
    source.onerror = function () {
      setConn(false);
      if (source.readyState === EventSource.CLOSED) { setTimeout(connect, 3000); }
    };
  }

  function atBottom() { return logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40; }
  function syncPauseButton() { $('t-pause').textContent = autoscroll ? '⏸ Pause' : '▶ Resume'; }

  logEl.addEventListener('scroll', function () {
    if (manualPause) { return; }
    var bottom = atBottom();
    if (bottom !== autoscroll) { autoscroll = bottom; syncPauseButton(); }
  }, { passive: true });

  $('t-pause').addEventListener('click', function () {
    manualPause = autoscroll;
    autoscroll = !autoscroll;
    if (autoscroll) { logEl.scrollTop = logEl.scrollHeight; }
    syncPauseButton();
  });

  function updateCount() {
    var badge = $('search-count');
    if (!filter) { badge.hidden = true; return; }
    var n = 0;
    for (var el = logEl.firstChild; el; el = el.nextSibling) { if (!el.hidden) { n++; } }
    badge.textContent = n + (n === 1 ? ' match' : ' matches');
    badge.hidden = false;
  }

  $('search').addEventListener('input', function () {
    filter = this.value.trim().toLowerCase();
    for (var el = logEl.firstChild; el; el = el.nextSibling) { el.hidden = !matches(el.textContent); }
    updateCount();
    if (autoscroll) { logEl.scrollTop = logEl.scrollHeight; }
  });

  $('t-clear').addEventListener('click', async function () {
    var res = await MDev.api(API + '/console/clear', { method: 'POST' });
    if (res.ok) { clearView(); MDev.toast('Console cleared. logs/latest.log is untouched.'); }
    else { MDev.toast(res.message, 'error'); }
  });

  $('t-copy').addEventListener('click', async function () {
    var lines = [];
    for (var el = logEl.firstChild; el; el = el.nextSibling) { if (!el.hidden) { lines.push(el.textContent); } }
    if (!lines.length) { MDev.toast('Nothing to copy yet.'); return; }
    var ok = await MDev.copy(lines.join('\n'));
    MDev.toast(ok ? 'Copied ' + lines.length + ' lines.' : 'Could not copy the console text.', ok ? 'info' : 'error');
  });

  var input = $('cmd');
  var history = [];
  var cursor = 0;
  var draft = '';

  try { history = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]'); } catch (e) { history = []; }
  if (!Array.isArray(history)) { history = []; }
  cursor = history.length;

  function remember(cmd) {
    if (history[history.length - 1] !== cmd) { history.push(cmd); }
    if (history.length > 50) { history = history.slice(-50); }
    cursor = history.length;
    try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch (e) { /* private mode */ }
  }

  function endOfInput() { input.setSelectionRange(input.value.length, input.value.length); }

  function older() {
    if (!history.length || cursor === 0) { return; }
    if (cursor === history.length) { draft = input.value; }
    cursor -= 1;
    input.value = history[cursor];
    endOfInput();
  }

  function newer() {
    if (cursor >= history.length) { return; }
    cursor += 1;
    input.value = cursor === history.length ? draft : history[cursor];
    endOfInput();
  }

  input.addEventListener('keydown', function (ev) {
    if (ev.key === 'ArrowUp') { ev.preventDefault(); older(); }
    else if (ev.key === 'ArrowDown') { ev.preventDefault(); newer(); }
  });
  $('hist-up').addEventListener('click', function () { older(); input.focus(); });
  $('hist-down').addEventListener('click', function () { newer(); input.focus(); });

  $('cmd-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var text = input.value.trim();
    if (!text) { return; }
    remember(text.replace(/^\/+/, '').trim() || text);
    input.value = '';
    draft = '';
    var res = await MDev.api(API + '/console/command', { method: 'POST', body: { command: text } });
    if (!res.ok) { MDev.toast(res.message || 'Command failed.', 'error'); }
    input.focus();
  });

  var LABELS = { ONLINE: '🟢 ONLINE', OFFLINE: '🔴 OFFLINE', STARTING: '🟡 STARTING...', STOPPING: '🟠 STOPPING...', CRASHED: '⚠️ CRASHED', ERROR: '⚠️ ERROR' };

  async function pollState() {
    var res = await MDev.api(API + '/status');
    if (res.state) {
      var pill = $('state-pill');
      pill.dataset.state = res.state.toLowerCase();
      pill.textContent = LABELS[res.state] || res.state;
      var note = $('state-note');
      var bad = res.state === 'ERROR' || res.state === 'CRASHED';
      note.hidden = !bad;
      if (bad) { note.textContent = '❌ ' + (res.message || 'Minecraft failed to start.') + ' Details are in the console below.'; }
    }
    setTimeout(pollState, 2500);
  }

  syncPauseButton();
  connect();
  pollState();
})();
