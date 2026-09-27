(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var root = $('playit-root');
  var mcInstances = (root.dataset.mcInstances || '').split(',').filter(Boolean);

  var STATE_LABELS = {
    running: { icon: '🟢', text: 'Running' },
    starting: { icon: '🟡', text: 'Starting...' },
    stopping: { icon: '🟠', text: 'Stopping...' },
    stopped: { icon: '🔴', text: 'Stopped' },
    setup_required: { icon: '🟡', text: 'Waiting for setup' },
    installed: { icon: '⚪', text: 'Not configured' },
    error: { icon: '⚠️', text: 'Error' }
  };

  function show(id, visible) { $(id).hidden = !visible; }
  function setText(id, text) { $(id).textContent = text; }

  var installPolling = false;
  var setupPolling = false;

  function showMsg(text) {
    var el = $('msg');
    el.textContent = text || '';
    el.hidden = !text;
  }

  function render(s) {
    // hide everything, then show exactly what applies
    ['card-not-installed', 'card-setup-needed', 'card-setup-active', 'card-status', 'card-tunnel']
      .forEach(function (id) { show(id, false); });

    if (!s.installed) {
      show('card-not-installed', true);
      setText('ni-platform', s.platform + (s.environment ? ' / ' + s.environment : ''));
      setText('ni-arch', s.architecture);
      setText('ni-version', s.configured_version === 'latest' ? 'Latest stable release' : s.configured_version);
      var unsupported = $('ni-unsupported');
      if (!s.supported) {
        unsupported.textContent = 'No official Playit binary is configured for this platform/architecture. ' +
          'Add one in config.py to enable installation.';
        unsupported.hidden = false;
        $('btn-install').hidden = true;
      } else {
        unsupported.hidden = true;
        $('btn-install').hidden = installPolling;
      }
      show('install-progress', installPolling);
      return;
    }

    if (s.phase === 'setup_required' && s.setup_url) {
      show('card-setup-active', true);
      setText('setup-url', s.setup_url);
      $('btn-open-setup').href = s.setup_url;
      return;
    }

    if (s.phase === 'installed' || (s.phase === 'setup_required' && !s.setup_url)) {
      show('card-setup-needed', true);
      $('btn-setup').disabled = setupPolling;
      $('btn-setup').textContent = setupPolling ? 'Waiting for setup...' : 'Setup Playit';
      return;
    }

    // running / starting / stopping / stopped / error - full status card
    show('card-status', true);
    show('card-tunnel', true);
    var meta = STATE_LABELS[s.phase] || { icon: '⚪', text: s.phase };
    $('card-status').dataset.state = s.phase;
    setText('status-label', meta.icon + ' Agent: ' + meta.text);
    setText('st-phase', s.phase);
    setText('st-secret', s.secret_configured ? 'true' : 'false');
    setText('st-version', s.version || 'Unknown');
    setText('st-platform', s.platform + (s.environment ? ' / ' + s.environment : ''));
    setText('st-arch', s.architecture);
    setText('st-public', s.public_address || 'Unavailable');
    setText('tn-public', s.public_address || 'Unavailable');
    show('btn-copy-public', !!s.public_address);

    show('btn-start', s.phase === 'stopped' || s.phase === 'error');
    show('btn-stop', s.phase === 'running');
    show('btn-restart', s.phase === 'running');

    if (s.message && (s.phase === 'error')) {
      showMsg(s.message);
    } else {
      showMsg('');
    }
  }

  async function poll() {
    var s = await MDev.api('/api/playit/status');
    if (s.ok) { render(s); }
    var fast = s.phase === 'starting' || s.phase === 'stopping' || s.phase === 'setup_required' || installPolling;
    setTimeout(poll, fast ? 1500 : 3000);
  }

  // ---- install ----

  $('btn-install').addEventListener('click', async function () {
    installPolling = true;
    $('btn-install').hidden = true;
    show('install-progress', true);
    var res = await MDev.api('/api/playit/install', { method: 'POST' });
    if (!res.ok) {
      installPolling = false;
      showMsg(res.message || 'Could not start installation.');
      return;
    }
    pollInstall();
  });

  async function pollInstall() {
    var s = await MDev.api('/api/playit/status');
    var job = s.install_job;
    if (job) {
      $('install-bar').style.width = Math.max(2, job.percent || 0) + '%';
      $('install-message').textContent = job.message || '';
      if (job.status === 'done') {
        installPolling = false;
        MDev.toast('Playit installed.');
        return;
      }
      if (job.status === 'error') {
        installPolling = false;
        showMsg(job.message || 'Installation failed.');
        return;
      }
    }
    setTimeout(pollInstall, 500);
  }

  // ---- setup ----

  $('btn-setup').addEventListener('click', async function () {
    setupPolling = true;
    $('btn-setup').disabled = true;
    $('btn-setup').textContent = 'Waiting for setup...';
    var res = await MDev.api('/api/playit/setup', { method: 'POST' });
    if (!res.ok) {
      setupPolling = false;
      showMsg(res.message || 'Could not start setup.');
    }
  });

  $('btn-copy-setup').addEventListener('click', async function () {
    var ok = await MDev.copy($('setup-url').textContent);
    MDev.toast(ok ? 'Setup link copied.' : 'Could not copy the link.', ok ? 'info' : 'error');
  });

  // ---- start/stop/restart ----

  async function run(url, confirmMsg) {
    if (confirmMsg && !window.confirm(confirmMsg)) { return; }
    var res = await MDev.api(url, { method: 'POST' });
    MDev.toast(res.message || (res.ok ? 'Done.' : 'Something went wrong.'), res.ok ? 'info' : 'error');
  }
  $('btn-start').addEventListener('click', function () { run('/api/playit/start'); });
  $('btn-stop').addEventListener('click', function () { run('/api/playit/stop', 'Stop the Playit agent? This will close the tunnel.'); });
  $('btn-restart').addEventListener('click', function () { run('/api/playit/restart', 'Restart the Playit agent?'); });

  $('btn-copy-public').addEventListener('click', async function () {
    var text = $('st-public').textContent;
    if (!text || text === 'Unavailable') { return; }
    var ok = await MDev.copy(text);
    MDev.toast(ok ? 'Copied: ' + text : 'Could not copy.', ok ? 'info' : 'error');
  });

  // ---- Minecraft target dropdown (display only - never edits server.properties) ----

  async function loadTargets() {
    var select = $('mc-target');
    var res = await MDev.api('/api/playit/minecraft-targets');
    select.innerHTML = '';
    if (!res.ok || !res.targets.length) {
      var opt = document.createElement('option');
      opt.textContent = 'No Minecraft instance configured';
      select.appendChild(opt);
      return;
    }
    res.targets.forEach(function (t) {
      var opt = document.createElement('option');
      opt.value = t.local_address;
      opt.textContent = t.name + ' (' + t.local_address + ')';
      select.appendChild(opt);
    });
  }

  loadTargets();
  poll();
})();
