(function () {
  'use strict';

  var cardsEl = document.getElementById('cards');
  var emptyEl = document.getElementById('empty');
  var timer = null;

  var LABELS = {
    ONLINE: { icon: '🟢', text: 'ONLINE' },
    OFFLINE: { icon: '🔴', text: 'OFFLINE' },
    STARTING: { icon: '🟡', text: 'STARTING...' },
    STOPPING: { icon: '🟠', text: 'STOPPING...' },
    CRASHED: { icon: '⚠️', text: 'CRASHED' },
    ERROR: { icon: '⚠️', text: 'ERROR' }
  };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }

  function card(inst) {
    var meta = LABELS[inst.state] || LABELS.ERROR;
    var a = document.createElement('a');
    a.className = 'inst-card';
    a.href = '/instance/' + encodeURIComponent(inst.name) + '/';
    a.dataset.state = inst.state.toLowerCase();

    var head = el('div', 'inst-card-head');
    head.appendChild(el('span', 'inst-name', inst.name));
    var pill = el('span', 'pill pill-sm', meta.icon + ' ' + meta.text);
    pill.dataset.state = inst.state.toLowerCase();
    head.appendChild(pill);
    a.appendChild(head);

    a.appendChild(el('div', 'inst-line muted',
      (inst.software || 'Unknown') + ' ' + (inst.minecraft_version || '')));

    var players = (inst.players_online === null || inst.players_online === undefined)
      ? (inst.state === 'ONLINE' ? 'Unknown' : '-')
      : (inst.players_online + ' / ' + (inst.max_players === null || inst.max_players === undefined ? '?' : inst.max_players));
    a.appendChild(el('div', 'inst-line', 'Players: ' + players));

    var ramLine = el('div', 'inst-line muted', 'RAM: ' + inst.ram.min + ' - ' + inst.ram.max +
      (inst.port ? '  ·  Port: ' + inst.port : ''));
    a.appendChild(ramLine);

    a.appendChild(el('span', 'btn btn-sm inst-open', 'Open'));
    return a;
  }

  async function poll() {
    clearTimeout(timer);
    var res = await MDev.api('/api/instances');
    if (res.ok) {
      cardsEl.textContent = '';
      if (!res.instances.length) {
        emptyEl.hidden = false;
      } else {
        emptyEl.hidden = true;
        res.instances.forEach(function (inst) { cardsEl.appendChild(card(inst)); });
      }
    }
    timer = setTimeout(poll, 4000);
  }

  poll();
})();
