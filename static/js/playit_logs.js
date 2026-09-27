(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var MAX_LINES = 2000;

  var logEl = $('log');
  var autoscroll = true;
  var manualPause = false;
  var filter = '';
  var queue = [];
  var frame = 0;
  var source = null;

  function classify(entry) {
    if (entry.s === 'panel') { return 'l-panel'; }
    if (entry.s === 'setup') { return 'l-cmd'; }
    var text = entry.t;
    var cls = entry.s === 'history' ? 'l-hist' : '';
    if (/error|failed|crash/i.test(text)) { return 'l-err'; }
    if (/warn/i.test(text)) { return 'l-warn'; }
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
    source = new EventSource('/api/playit/logs/stream');
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
    var res = await MDev.api('/api/playit/logs/clear', { method: 'POST' });
    if (res.ok) { clearView(); MDev.toast('Log cleared. The saved log file is untouched.'); }
    else { MDev.toast(res.message, 'error'); }
  });

  $('t-copy').addEventListener('click', async function () {
    var lines = [];
    for (var el = logEl.firstChild; el; el = el.nextSibling) { if (!el.hidden) { lines.push(el.textContent); } }
    if (!lines.length) { MDev.toast('Nothing to copy yet.'); return; }
    var ok = await MDev.copy(lines.join('\n'));
    MDev.toast(ok ? 'Copied ' + lines.length + ' lines.' : 'Could not copy the log text.', ok ? 'info' : 'error');
  });

  syncPauseButton();
  connect();
})();
