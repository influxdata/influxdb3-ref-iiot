// IIoT dashboard JS: andon board (direct fetch + latency badge) and per-line OEE charts.
// Plain JS, no build step. Loaded after htmx.min.js and uplot.min.js.
//
// Single fetch per poll: /api/v3/engine/andon_board (Processing Engine plugin)
// returns BOTH the current per-line state (cells + current OEE) AND a per-line,
// per-minute history for the last 60 minutes. The cell grid and the breakdown
// charts share a source of truth — same numbers, same plugin.

(function () {
  'use strict';

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'text') e.textContent = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    if (children) children.forEach(function (c) { e.appendChild(c); });
    return e;
  }

  // ------------------------------------------------------------------
  // Andon cells
  // ------------------------------------------------------------------

  function classForState(s) {
    return ({
      running: 'cell-ok',
      idle: 'cell-warn',
      stopped: 'cell-down',
      error: 'cell-down',
      changeover: 'cell-info',
      planned_maintenance: 'cell-info',
    })[s] || 'cell-unknown';
  }

  function renderAndon(data) {
    var shell = document.getElementById('andon-shell');
    if (!shell) return;
    shell.innerHTML = '';
    if (!data || !data.lines) {
      shell.appendChild(el('div', {class: 'andon-empty', text: 'no data'}));
      return;
    }
    data.lines.forEach(function (line) {
      var row = el('div', {class: 'andon-line'});
      row.appendChild(el('div', {class: 'andon-line-label', text: line.line_id}));
      var grid = el('div', {class: 'andon-cells'});
      line.machines.forEach(function (m) {
        var c = el('span', {class: 'andon-cell ' + classForState(m.state), title: m.machine_id + ' · ' + m.state});
        c.textContent = m.station_id || m.machine_id;
        grid.appendChild(c);
      });
      row.appendChild(grid);
      var oeePct = (Math.round((line.oee || 0) * 1000) / 10).toFixed(1) + '%';
      row.appendChild(el('div', {class: 'andon-line-oee', text: 'OEE ' + oeePct}));
      shell.appendChild(row);
    });
  }

  // ------------------------------------------------------------------
  // Per-line OEE breakdown chart — driven by line.history from the same payload
  // ------------------------------------------------------------------
  //
  // Y-axis is tightened to [0.7, 1.02] — nominal A/P/Q values cluster near 1.0
  // and a [0, 1] range buries small dips. The 0.7 floor still shows the kind
  // of drop a downtime cascade produces (Line 2 OEE dropping to 0.4 will be
  // visible as a series dropping below the chart, which is louder than a
  // proportional shrink in a [0, 1] view).

  var Y_RANGE = [0.7, 1.02];

  function buildLineChart(containerId, lineId) {
    var container = document.getElementById(containerId);
    if (!container) return null;
    var opts = {
      width: container.clientWidth || 400,
      height: 180,
      title: lineId + ' — OEE components (Availability · Performance · Quality)',
      scales: {y: {auto: false, range: Y_RANGE}},
      series: [
        {},
        {label: 'Availability', stroke: '#5ec1ff', width: 2},
        {label: 'Performance', stroke: '#a3e635', width: 2, dash: [6, 4]},
        {label: 'Quality',     stroke: '#fbbf24', width: 2, dash: [2, 3]},
      ],
      axes: [
        {scale: 'x'},
        {scale: 'y', values: function (_, t) { return t.map(function (v) { return (v * 100).toFixed(0) + '%'; }); }},
      ],
    };
    var data = [[], [], [], []];
    return new uPlot(opts, data, container);
  }

  function updateChartsFromHistory(charts, lines) {
    lines.forEach(function (line) {
      var c = charts[line.line_id];
      if (!c || !line.history) return;
      var xs = [], a = [], p = [], q = [];
      line.history.forEach(function (h) {
        var ts = Math.floor(new Date(h.bucket).getTime() / 1000);
        if (!isFinite(ts)) return;
        xs.push(ts);
        a.push(h.availability == null ? null : Number(h.availability));
        p.push(h.performance == null ? null : Number(h.performance));
        q.push(h.quality == null ? null : Number(h.quality));
      });
      c.setData([xs, a, p, q]);
    });
  }

  // ------------------------------------------------------------------
  // Single poll loop driving both the cell grid and the breakdown charts
  // ------------------------------------------------------------------

  function startPolling() {
    var panel = document.getElementById('andon-panel');
    if (!panel) return;
    var url = panel.dataset.andonUrl;
    var token = panel.dataset.andonToken;
    var pollMs = parseInt(panel.dataset.andonPollMs, 10) || 2000;

    var charts = {
      L1: buildLineChart('chart-l1', 'L1'),
      L2: buildLineChart('chart-l2', 'L2'),
      L3: buildLineChart('chart-l3', 'L3'),
    };

    function fetchOnce() {
      var t0 = performance.now();
      fetch(url, {headers: {'Authorization': 'Bearer ' + token}})
        .then(function (r) { return r.json(); })
        .then(function (raw) {
          var ms = Math.round(performance.now() - t0);
          var label = document.getElementById('andon-latency');
          if (label) label.textContent = ms;
          // Some Processing Engine versions auto-unwrap a {status, body}
          // wrapper before sending; others pass it through. Handle either.
          var data = (raw && raw.body && raw.body.lines) ? raw.body : raw;
          renderAndon(data);
          if (data && Array.isArray(data.lines)) {
            updateChartsFromHistory(charts, data.lines);
          }
        })
        .catch(function (e) {
          var label = document.getElementById('andon-latency');
          if (label) label.textContent = 'error';
          console.error('andon fetch failed', e);
        });
    }

    fetchOnce();
    setInterval(fetchOnce, pollMs);
  }

  document.addEventListener('DOMContentLoaded', startPolling);
})();
