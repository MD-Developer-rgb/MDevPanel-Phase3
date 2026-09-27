(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var NAME = document.querySelector('[data-instance]').dataset.instance;
  var API = '/api/instances/' + encodeURIComponent(NAME);

  // ---- RAM ----------------------------------------------------------------

  async function loadRam() {
    var res = await MDev.api(API + '/status');
    if (res.ram) { $('ram-min').value = res.ram.min; $('ram-max').value = res.ram.max; }
  }

  $('ram-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var res = await MDev.api(API + '/ram', { method: 'POST', body: { ram_min: $('ram-min').value, ram_max: $('ram-max').value } });
    MDev.toast(res.message || (res.ok ? 'Saved.' : 'Could not save.'), res.ok ? 'info' : 'error');
  });

  // ---- server.properties ----------------------------------------------------

  var fieldsEl = $('props-fields');

  function fieldControl(f) {
    var wrap = document.createElement('label');
    wrap.className = 'field';
    var span = document.createElement('span');
    span.className = 'field-label';
    span.textContent = f.label;
    wrap.appendChild(span);

    var control;
    if (f.kind === 'bool') {
      control = document.createElement('select');
      ['(unset)', 'true', 'false'].forEach(function (v) {
        var opt = document.createElement('option');
        opt.value = v === '(unset)' ? '' : v;
        opt.textContent = v;
        control.appendChild(opt);
      });
      control.value = f.value === null ? '' : f.value;
    } else if (f.kind === 'choice') {
      control = document.createElement('select');
      var blank = document.createElement('option');
      blank.value = ''; blank.textContent = '(unset)';
      control.appendChild(blank);
      f.choices.forEach(function (c) {
        var opt = document.createElement('option');
        opt.value = c; opt.textContent = c;
        control.appendChild(opt);
      });
      control.value = f.value === null ? '' : f.value;
    } else {
      control = document.createElement('input');
      control.type = f.kind === 'int' ? 'number' : 'text';
      control.value = f.value === null ? '' : f.value;
      control.autocomplete = 'off';
    }
    control.dataset.key = f.key;
    control.id = 'prop-' + f.key;
    wrap.appendChild(control);
    return wrap;
  }

  async function loadProperties() {
    var res = await MDev.api(API + '/properties');
    if (!res.ok) { $('props-note').textContent = res.message || 'Could not load server.properties.'; return; }
    fieldsEl.textContent = '';
    res.fields.forEach(function (f) { fieldsEl.appendChild(fieldControl(f)); });
    $('props-advanced').value = res.advanced || '';
    $('props-note').textContent = 'Values shown as (unset) are not in server.properties yet - Minecraft fills in its own defaults.';
  }

  $('props-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    var errorBox = $('props-error');
    errorBox.hidden = true;
    var values = {};
    fieldsEl.querySelectorAll('[data-key]').forEach(function (el) {
      if (el.value !== '') { values[el.dataset.key] = el.value; }
    });
    var res = await MDev.api(API + '/properties', { method: 'POST', body: { fields: values, advanced: $('props-advanced').value } });
    if (!res.ok) { errorBox.textContent = res.message || 'Could not save.'; errorBox.hidden = false; return; }
    MDev.toast(res.message || 'Saved.');
  });

  loadRam();
  loadProperties();
})();
