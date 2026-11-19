'use strict';
// The charts at the top of the Inbox (Chart.js, self-hosted in static/vendor/chartjs), drawn from /api/connectors/gmail/stats,
// which reads the local copy. Pressing a bar shows the mail behind it: a day, a sender or a topic.
(function () {
  const tiles = document.getElementById('stat-tiles');
  const charts = document.getElementById('stat-charts');
  const INDIGO = '#6366f1', EMERALD = '#34d399', INK = '#475569', GRID = '#e2e8f0';
  const made = {};
  let running = false, again = false;
  const num = n => Number(n).toLocaleString();
  function tile(label, value, note, query) {
    const box = el('button', undefined, 'stat-tile');
    box.type = 'button';
    box.title = 'Show these messages';
    box.append(el('span', label, 'stat-label'), el('strong', num(value), 'stat-value'));
    if (note) box.append(el('span', note, 'meta'));
    if (query != null) box.addEventListener('click', () => window.inboxSearch(query));
    return box;
  }
  const defaults = {
    responsive: true, maintainAspectRatio: false, animation: {duration: 500},
    plugins: {legend: {display: false}, tooltip: {backgroundColor: '#0f172a', padding: 10, cornerRadius: 8, titleFont: {weight: '600'}}},
    onHover: (event, active) => { event.native.target.style.cursor = active.length ? 'pointer' : 'default'; }
  };
  // One chart per canvas, updated in place so a refresh animates instead of flashing.
  function chart(id, config) {
    if (made[id]) { made[id].data = config.data; made[id].options.onClick = config.options.onClick; made[id].update(); return made[id]; }
    const canvas = document.getElementById(id);
    config.options = Object.assign({}, defaults, config.options, {plugins: Object.assign({}, defaults.plugins, config.options.plugins)});
    made[id] = new Chart(canvas, config);
    return made[id];
  }
  function dayLabel(iso) { const [, m, d] = iso.split('-'); return Number(m) + '/' + Number(d); }
  function drawDays(days) {
    const config = {type: 'bar', data: {labels: days.map(d => dayLabel(d.date)), datasets: [
      {label: 'Received', data: days.map(d => d.received), backgroundColor: INDIGO, hoverBackgroundColor: '#4f46e5', borderRadius: 3, borderSkipped: false, maxBarThickness: 24},
      {label: 'Sent', data: days.map(d => d.sent), backgroundColor: EMERALD, hoverBackgroundColor: '#10b981', borderRadius: 3, borderSkipped: false, maxBarThickness: 24}]},
      options: {
        interaction: {mode: 'index', intersect: false},
        scales: {x: {stacked: true, grid: {display: false}, ticks: {color: INK, maxRotation: 0, autoSkipPadding: 14}},
                 y: {stacked: true, beginAtZero: true, grid: {color: GRID}, border: {display: false}, ticks: {color: INK, precision: 0}}},
        plugins: {tooltip: {callbacks: {title: items => days[items[0].dataIndex].date}}},
        onClick: (event, active, c) => {
          const hit = c.getElementsAtEventForMode(event, 'nearest', {intersect: false}, true)[0];
          if (!hit) return;
          const day = days[hit.index];
          window.inboxSearch('on:' + day.date, hit.datasetIndex === 1 ? 'sent' : 'inbox');
        }}};
    chart('chart-days-canvas', config);
  }
  function drawBars(id, rows, labelOf, queryOf) {
    const host = document.getElementById(id + '-box');
    host.style.height = Math.max(120, rows.length * 34 + 16) + 'px';   // One row each; the CSSOM is allowed by the CSP, inline style attributes are not.
    chart(id, {type: 'bar', data: {labels: rows.map(labelOf), datasets: [{data: rows.map(r => r.count), backgroundColor: INDIGO, hoverBackgroundColor: '#4f46e5', borderRadius: 6, borderSkipped: false, barThickness: 16}]},
      options: {indexAxis: 'y',
        scales: {x: {beginAtZero: true, grid: {color: GRID}, border: {display: false}, ticks: {color: INK, precision: 0}},
                 y: {grid: {display: false}, ticks: {color: '#0f172a', callback(value) { const t = this.getLabelForValue(value); return t.length > 22 ? t.slice(0, 21) + '…' : t; }}}},
        plugins: {tooltip: {callbacks: {label: item => num(item.raw) + ' message(s)'}}},
        onClick: (event, active) => { if (active.length) window.inboxSearch(queryOf(rows[active[0].index])); }}});
  }
  async function draw() {
    if (running) { again = true; return; }
    running = true;
    try {
      if (typeof Chart === 'undefined') throw Error('Chart.js did not load');
      const account = sessionStorage.getItem('inboxtend-account') || '';
      const data = await request('/api/connectors/gmail/stats' + (account ? '?account=' + encodeURIComponent(account) : ''));
      const total = data.days.reduce((n, d) => n + d.received + d.sent, 0);
      if (!total && !data.unread) { tiles.hidden = true; charts.hidden = true; return; }
      const last = data.days[data.days.length - 1].date;
      const weekAgo = data.days[data.days.length - 7].date;
      tiles.replaceChildren(tile('Unread', data.unread, 'in the inbox', 'is:unread'), tile('Received today', data.today_received, null, 'on:' + last),
        tile('Received · 7 days', data.week_received, 'since ' + dayLabel(weekAgo)), tile('Sent · 7 days', data.week_sent));
      tiles.hidden = false; charts.hidden = false;
      for (const t of [...tiles.children].slice(2)) { t.disabled = true; t.title = ''; }
      drawDays(data.days);
      document.getElementById('chart-senders-empty').hidden = data.senders.length > 0;
      document.getElementById('chart-senders-box').hidden = !data.senders.length;
      if (data.senders.length) drawBars('chart-senders', data.senders, r => r.name || r.address, r => 'from:' + r.address);
      const topicCard = document.getElementById('chart-topics-card');
      topicCard.hidden = !(userSettings.topics_enabled && data.topics.length);
      if (!topicCard.hidden) drawBars('chart-topics', data.topics, r => (TOPICS[r.code] || [r.code])[0], r => 'topic:' + r.code);
    } catch (error) { charts.hidden = true; tiles.hidden = true; }
    finally { running = false; if (again) { again = false; draw(); } }
  }
  window.refreshStats = draw;
  const start = async () => { try { await sessionReady; await settingsReady; } catch (_) { return; } draw(); };
  start();
  document.getElementById('account-filter').addEventListener('change', () => setTimeout(draw, 0));
})();
