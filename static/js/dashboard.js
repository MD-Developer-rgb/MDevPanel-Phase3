(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var NAME = document.querySelector('.dash').dataset.instance;
  var API = '/api/instances/' + encodeURIComponent(NAME);

  var STATES = {
    ONLINE:   { icon: '🟢', label: 'ONLINE' },
    OFFLINE:  { icon: '🔴', label: 'OFFLINE' },
    STARTING: { icon: '🟡', label: 'STARTING...' },
    STOPPING: { icon: '🟠', label: 'STOPPING...' },
    CRASHED:  { icon: '⚠️', label: 'CRASHED' },
    ERROR:    { icon: '⚠️', label: 'ERROR' }
  };

  var prevState = null;
  var uptimeBase = null;
  var pollTimer = null;
  var problemsKey = '';

  function fmtUptime(total) {
    total = Math.max(0, Math.floor(total));
    var h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return [h, m, s].map(function (n) { return String(n).padStart(2, '0'); }).join(':');
  }

  function tickUptime() {
    var text = uptimeBase ? fmtUptime(uptimeBase.sec + (performance.now() - uptimeBase.at) / 1000) : 'Offline';
    document.querySelectorAll('.js-uptime').forEach(function (el) { el.textContent = text; });
  }

  function playersText(s) {
    if (s.players_online === null || s.players_online === undefined) {
      return s.state === 'ONLINE' ? 'Unknown' : '-';
    }
    return s.players_online + ' / ' + (s.max_players === null || s.max_players === undefined ? '?' : s.max_players);
  }

  function renderProblems(problems) {
    var key = JSON.stringify(problems);
    if (key === problemsKey) { return; }
    problemsKey = key;
    var box = $('problems');
    box.textContent = '';
    problems.forEach(function (p) {
      var el = document.createElement('div');
      el.className = 'alert ' + (p.blocking ? 'alert-error' : 'alert-warn');
      var title = document.createElement('strong');
      title.textContent = p.title;
      el.appendChild(title);
      if (p.detail) {
        var pre = document.createElement('pre');
        pre.textContent = p.detail;
        el.appendChild(pre);
      }
      box.appendChild(el);
    });
    box.hidden = problems.length === 0;
  }

  function show(id, visible) { $(id).hidden = !visible; }

  function render(s) {
    var state = s.state;
    var shown = (s.restarting && state === 'OFFLINE') ? 'STARTING' : state;
    var meta = STATES[shown] || STATES.ERROR;

    $('hero').dataset.state = shown.toLowerCase();
    $('status-label').textContent = meta.icon + ' ' + meta.label;

    var sub = '';
    if (s.restarting) { sub = 'Restarting server...'; }
    else if (state === 'ONLINE') { sub = 'Server is running.'; }
    else if (state === 'STARTING') { sub = 'Please wait...'; }
    else if (state === 'STOPPING') { sub = 'Stopping server...'; }
    else if (state === 'OFFLINE') { sub = 'Server is not running.'; }
    else if (state === 'ERROR') { sub = '❌ ' + (s.message || 'Minecraft failed to start.'); }
    else if (state === 'CRASHED') { sub = '❌ ' + (s.message || 'Minecraft stopped unexpectedly.'); }
    $('status-sub').textContent = sub;

    $('hero-players').textContent = playersText(s);
    $('hero-port').textContent = String(s.port);

    var busy = s.restarting;
    var isError = state === 'ERROR' || state === 'CRASHED';
    show('btn-start', !busy && (state === 'OFFLINE' || isError));
    show('btn-restart', !busy && state === 'ONLINE');
    show('btn-stop', !busy && (state === 'ONLINE' || state === 'STARTING'));
    show('btn-force', state === 'STOPPING');
    $('btn-console').textContent = isError ? 'VIEW CONSOLE' : '🖥 CONSOLE';
    $('btn-stop').classList.toggle('wide', state === 'STARTING');

    $('srv-status').textContent = meta.icon + ' ' + meta.label.replace('...', '');
    $('srv-players').textContent = playersText(s);
    $('srv-version').textContent = s.minecraft_version;
    $('srv-software').textContent = s.software;
    $('srv-jar').textContent = s.instance.jar + (s.instance.jar_exists ? '' : ' (not found)');
    $('srv-java').textContent = s.java.ok ? s.java.display : 'Not found';
    $('srv-port').textContent = String(s.port);
    $('srv-ram').textContent = s.ram.min + ' - ' + s.ram.max;
    $('srv-port-note').textContent = s.port_source === 'server.properties'
      ? 'Port read from server.properties.'
      : 'Port shown is the default. Minecraft creates server.properties on first start.';

    renderProblems(s.problems || []);

    uptimeBase = s.uptime_seconds === null ? null : { sec: s.uptime_seconds, at: performance.now() };
    tickUptime();

    if (prevState !== null && prevState !== state) {
      if (state === 'ONLINE') { MDev.toast('🟢 Server is online'); }
      else if (state === 'OFFLINE' && (prevState === 'STOPPING' || prevState === 'ONLINE')) { MDev.toast('🔴 Server stopped'); }
      else if (state === 'CRASHED') { MDev.toast('⚠️ Server crashed. Open the console for details.', 'error'); }
      else if (state === 'ERROR') { MDev.toast('❌ Minecraft failed to start.', 'error'); }
    }
    prevState = state;
  }

  async function poll() {
    clearTimeout(pollTimer);
    var res = await MDev.api(API + '/status');
    var fast = false;
    if (res.unreachable || res.state === undefined) {
      $('status-sub').textContent = res.message || 'Cannot reach the panel.';
    } else {
      render(res);
      fast = res.state === 'STARTING' || res.state === 'STOPPING' || res.restarting;
    }
    pollTimer = setTimeout(poll, fast ? 1000 : 2500);
  }

  function setBusy(btn, on) { btn.classList.toggle('busy', on); btn.disabled = on; }

  var ACTIONS = {
    start:   { url: API + '/start' },
    stop:    { url: API + '/stop',       confirm: 'Stop the Minecraft server?' },
    restart: { url: API + '/restart',    confirm: 'Restart the Minecraft server?' },
    force:   { url: API + '/force-stop', confirm: 'Force stop kills the server immediately and may lose recent world changes. Continue?' }
  };

  async function run(kind, btn) {
    var cfg = ACTIONS[kind];
    if (cfg.confirm && !window.confirm(cfg.confirm)) { return; }
    setBusy(btn, true);
    var res = await MDev.api(cfg.url, { method: 'POST' });
    setBusy(btn, false);
    MDev.toast(res.message || (res.ok ? 'Done.' : 'Something went wrong.'), res.ok ? 'info' : 'error');
    poll();
  }

  $('btn-start').addEventListener('click', function () { run('start', this); });
  $('btn-stop').addEventListener('click', function () { run('stop', this); });
  $('btn-restart').addEventListener('click', function () { run('restart', this); });
  $('btn-force').addEventListener('click', function () { run('force', this); });

  setInterval(tickUptime, 1000);
  poll();
})();
