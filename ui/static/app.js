// IIoT dashboard JS: andon board (direct fetch + latency badge) and per-line OEE charts.
// Plain JS, no build step. Loaded after htmx.min.js and uplot.min.js.

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
  // Andon board — direct fetch to /api/v3/engine/andon_board
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

  function pollAndon() {
    var panel = document.getElementById('andon-panel');
    if (!panel) return;
    var url = panel.dataset.andonUrl;
    var token = panel.dataset.andonToken;
    var pollMs = parseInt(panel.dataset.andonPollMs, 10) || 2000;

    function fetchOnce() {
      var t0 = performance.now();
      fetch(url, {headers: {'Authorization': 'Bearer ' + token}})
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var ms = Math.round(performance.now() - t0);
          var label = document.getElementById('andon-latency');
          if (label) label.textContent = ms;
          renderAndon(data);
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

  // ------------------------------------------------------------------
  // Per-line OEE breakdown — uPlot
  // ------------------------------------------------------------------

  function buildLineChart(containerId, lineId) {
    var container = document.getElementById(containerId);
    if (!container) return null;
    var opts = {
      width: container.clientWidth || 400,
      height: 180,
      title: lineId + ' — OEE components (Availability · Performance · Quality)',
      scales: {y: {auto: false, range: [0, 1.05]}},
      series: [
        {},
        {label: 'Availability', stroke: '#5ec1ff'},
        {label: 'Performance', stroke: '#a3e635'},
        {label: 'Quality', stroke: '#fbbf24'},
      ],
      axes: [
        {scale: 'x'},
        {scale: 'y', values: function (_, t) { return t.map(function (v) { return (v * 100).toFixed(0) + '%'; }); }},
      ],
    };
    var data = [[], [], [], []];
    return new uPlot(opts, data, container);
  }

  function pollOee() {
    var panel = document.getElementById('oee-panel');
    if (!panel) return;
    var pollMs = parseInt(panel.dataset.oeePollMs, 10) || 5000;
    var charts = {
      L1: buildLineChart('chart-l1', 'L1'),
      L2: buildLineChart('chart-l2', 'L2'),
      L3: buildLineChart('chart-l3', 'L3'),
    };

    function bucketize(rows, valueKey) {
      var byLine = {L1: {x: [], y: []}, L2: {x: [], y: []}, L3: {x: [], y: []}};
      rows.forEach(function (r) {
        var lid = r.line_id;
        if (!byLine[lid]) return;
        var ts = Math.floor(new Date(r.bucket).getTime() / 1000);
        byLine[lid].x.push(ts);
        byLine[lid].y.push(r[valueKey] == null ? 0 : Number(r[valueKey]));
      });
      return byLine;
    }

    function fetchOnce() {
      fetch('/partials/oee_breakdown')
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          var aL = bucketize(payload.availability, 'availability');
          var pL = bucketize(payload.performance, 'performance');
          var qL = bucketize(payload.quality, 'quality');
          ['L1', 'L2', 'L3'].forEach(function (lid) {
            var c = charts[lid];
            if (!c) return;
            // Use availability x-axis (assume buckets align across queries)
            var xs = aL[lid].x;
            c.setData([xs, aL[lid].y, pL[lid].y, qL[lid].y]);
          });
        })
        .catch(function (e) { console.error('oee fetch failed', e); });
    }

    fetchOnce();
    setInterval(fetchOnce, pollMs);
  }

  document.addEventListener('DOMContentLoaded', function () {
    pollAndon();
    pollOee();
  });
})();
