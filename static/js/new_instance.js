(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var form = $('form');
  var nameInput = $('name');
  var versionSelect = $('version');
  var buildSelect = $('build');
  var createBtn = $('create');
  var errorBox = $('form-error');

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  async function loadVersions() {
    var res = await MDev.api('/api/paper/versions');
    if (!res.ok) {
      versionSelect.innerHTML = '<option value="">Unavailable</option>';
      showError(res.message || 'Could not load Paper versions.');
      return;
    }
    versionSelect.innerHTML = '';
    res.versions.forEach(function (v) {
      var opt = document.createElement('option');
      opt.value = v; opt.textContent = v;
      versionSelect.appendChild(opt);
    });
    versionSelect.disabled = false;
    loadBuilds(versionSelect.value);
  }

  async function loadBuilds(version) {
    buildSelect.innerHTML = '<option value="">Recommended (latest)</option>';
    buildSelect.disabled = true;
    if (!version) { return; }
    var res = await MDev.api('/api/paper/builds?version=' + encodeURIComponent(version));
    if (!res.ok) { showError(res.message || 'Could not load Paper builds.'); return; }
    res.builds.forEach(function (b) {
      var opt = document.createElement('option');
      opt.value = b;
      opt.textContent = 'Build ' + b + (b === res.latest ? ' (latest)' : '');
      buildSelect.appendChild(opt);
    });
    buildSelect.disabled = false;
  }

  versionSelect.addEventListener('change', function () { loadBuilds(this.value); });

  function setBusy(busy) {
    createBtn.disabled = busy;
    createBtn.classList.toggle('busy', busy);
  }

  function showProgress(name) {
    form.hidden = true;
    var card = $('progress-card');
    card.hidden = false;
    $('progress-title').textContent = 'Creating "' + name + '"...';
  }

  function setProgress(percent, message) {
    if (percent !== null && percent !== undefined) {
      $('progress-bar').style.width = Math.max(2, percent) + '%';
    }
    $('progress-message').textContent = message || '';
  }

  async function pollJob(jobId, name) {
    var res = await MDev.api('/api/instances/create/' + jobId);
    if (!res.ok) {
      setProgress(100, res.message || 'Something went wrong.');
      return;
    }
    setProgress(res.percent, res.message);
    if (res.status === 'done') {
      $('progress-bar').style.width = '100%';
      var open = $('progress-open');
      open.hidden = false;
      open.href = '/instance/' + encodeURIComponent(name) + '/';
      MDev.toast('"' + name + '" was created.');
      return;
    }
    if (res.status === 'error') {
      $('progress-title').textContent = 'Could not create "' + name + '"';
      MDev.toast(res.message || 'Instance creation failed.', 'error');
      var back = document.createElement('a');
      back.className = 'btn btn-block';
      back.href = '/new-instance';
      back.textContent = 'Back';
      $('progress-card').appendChild(back);
      return;
    }
    setTimeout(function () { pollJob(jobId, name); }, 600);
  }

  form.addEventListener('submit', async function (ev) {
    ev.preventDefault();
    showError('');
    var name = nameInput.value.trim();
    var version = versionSelect.value;
    var build = buildSelect.value || null;
    if (!name) { showError('Enter an instance name.'); return; }
    if (!version) { showError('Choose a Paper version.'); return; }

    setBusy(true);
    var res = await MDev.api('/api/instances', { method: 'POST', body: { name: name, version: version, build: build } });
    setBusy(false);
    if (!res.ok) { showError(res.message || 'Could not create the instance.'); return; }
    showProgress(name);
    pollJob(res.job_id, name);
  });

  loadVersions();
})();
