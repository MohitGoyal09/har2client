/* HAR2Client static Space. Vanilla JS, hand-built SVG charts, no dependencies.
   All numbers come from #site-data (data/site.json); task excerpts load lazily from data/tasks.json.
   Labels and task text are untrusted data: they only go in through textContent / createTextNode. */
(function () {
  'use strict';
  var SITE = JSON.parse(document.getElementById('site-data').textContent);
  var NS = 'http://www.w3.org/2000/svg';
  var EFFORTS = ['low', 'medium', 'high'];
  var REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var SETTINGS = SITE.settings;
  var BENCH = SITE.benchmarks;
  var CELLS = SITE.cells;
  var MODELS = SITE.models;
  var BYID = {};
  BENCH.forEach(function (b) { BYID[b.id] = b; });

  /* ---------- helpers ---------- */
  function build(e, a, kids) {
    Object.keys(a || {}).forEach(function (k) {
      var v = a[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    });
    kids.forEach(function add(c) {
      if (c === null || c === undefined || c === false) return;
      if (Array.isArray(c)) c.forEach(add);
      else e.append(c.nodeType ? c : document.createTextNode(String(c)));
    });
    return e;
  }
  function h(tag, a) { return build(document.createElement(tag), a, [].slice.call(arguments, 2)); }
  function sv(tag, a) { return build(document.createElementNS(NS, tag), a, [].slice.call(arguments, 2)); }
  function $(sel, root) { return (root || document).querySelector(sel); }
  function pct(v) { return v.toFixed(1); }
  function signed(v) { return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1); }
  function tokf(v) { return Math.round(v).toLocaleString('en-US'); }
  function tokk(v) { return v >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : String(Math.round(v)); }
  function settingLabel(s) {
    var m = MODELS[s.model];
    return s.effort ? m.label + ' ' + s.effort : m.label + ' (thinking on)';
  }
  function settingByKey(k) { return SETTINGS.filter(function (s) { return s.key === k; })[0]; }
  function color(s) { return MODELS[s.model].color; }
  function metricVal(key, bid, metric) { var c = CELLS[key][bid]; return metric === 'tokens' ? c.tokens : c.pass; }
  function fmtMetric(v, metric) { return metric === 'tokens' ? tokf(v) : pct(v); }

  function seg(items, current, onPick, small, label) {
    var wrap = h('div', { class: 'seg' + (small ? ' seg-sm' : ''), role: 'group', 'aria-label': label });
    items.forEach(function (it) {
      var b = h('button', { type: 'button', 'aria-pressed': String(it.id === current), text: it.label });
      b.addEventListener('click', function () {
        [].forEach.call(wrap.children, function (x) { x.setAttribute('aria-pressed', 'false'); });
        b.setAttribute('aria-pressed', 'true');
        onPick(it.id);
      });
      wrap.append(b);
    });
    return wrap;
  }

  /* ---------- tooltip (one element, values lead, labels follow) ---------- */
  var tip = document.getElementById('tip');
  function tipShow(x, y, c) {
    tip.replaceChildren();
    if (c.title) tip.append(h('div', { class: 'tip-t', text: c.title }));
    (c.rows || []).forEach(function (r) {
      tip.append(h('div', { class: 'tip-r' },
        h('i', { style: r.c ? null : 'visibility:hidden' }),
        h('span', { text: r.l }),
        h('b', { text: r.v })));
      if (r.c) tip.lastChild.firstChild.style.background = r.c;
    });
    if (c.note) tip.append(h('div', { class: 'tip-n', text: c.note }));
    tip.hidden = false;
    var w = tip.offsetWidth, hgt = tip.offsetHeight;
    var left = Math.min(Math.max(8, x + 14), window.innerWidth - w - 8);
    var top = y + 16 + hgt > window.innerHeight - 8 ? y - hgt - 12 : y + 16;
    tip.style.left = left + 'px';
    tip.style.top = Math.max(8, top) + 'px';
  }
  function tipHide() { tip.hidden = true; }
  function bindTip(el, get) {
    el.setAttribute('data-tip', '');
    el.addEventListener('pointermove', function (e) { tipShow(e.clientX, e.clientY, get()); });
    el.addEventListener('pointerleave', tipHide);
    el.addEventListener('focus', function () { var r = el.getBoundingClientRect(); tipShow(r.left + r.width / 2, r.top + r.height / 2, get()); });
    el.addEventListener('blur', tipHide);
  }
  document.addEventListener('pointerdown', function (e) { if (!e.target.closest || !e.target.closest('[data-tip]')) tipHide(); });
  window.addEventListener('scroll', tipHide, { passive: true });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') tipHide(); });

  /* ---------- responsive redraw ---------- */
  function fit(box, draw) {
    var last = 0, pending = false;
    function run() {
      pending = false;
      var w = Math.floor(box.clientWidth);
      if (!w || w === last) return;
      last = w;
      box.replaceChildren(draw(w));
    }
    new ResizeObserver(function () { if (!pending) { pending = true; requestAnimationFrame(run); } }).observe(box);
    run();
  }

  /* ---------- chart frame with table-view twin ---------- */
  function dataTable(head, rows, caption) {
    return h('div', { class: 'table-scroll' }, h('table', { class: 'data-table' },
      h('caption', { class: 'sr', text: caption }),
      h('thead', null, h('tr', null, head.map(function (t) { return h('th', { scope: 'col', text: t }); }))),
      h('tbody', null, rows.map(function (r) {
        return h('tr', null, r.map(function (c, i) { return i === 0 ? h('th', { scope: 'row', text: c }) : h('td', { text: c }); }));
      }))));
  }
  function legendList(items) {
    return h('ul', { class: 'legend' }, items.map(function (it) {
      var k = h('i', { class: 'key ' + (it.kind || '') });
      if (it.color) k.style.background = it.color;
      return h('li', null, k, it.label);
    }));
  }
  /* spec: {title, sub, legend, tools[], chart(box), table() -> node}. Returns {el, rerender} */
  function frame(spec) {
    var box = h('div', { class: 'chart' });
    var tbox = h('div', { hidden: true });
    var on = false;
    var btn = h('button', { type: 'button', class: 'tbtn', 'aria-pressed': 'false', text: 'Table view' });
    btn.addEventListener('click', function () {
      on = !on;
      btn.setAttribute('aria-pressed', String(on));
      box.hidden = on;
      tbox.hidden = !on;
      if (on) tbox.replaceChildren(spec.table());
      else box.dispatchEvent(new Event('h2c-show'));
    });
    var head = h('div', { class: 'frame-head' },
      h('div', null, h('span', { class: 'frame-title', text: spec.title }), spec.sub ? h('span', { class: 'frame-sub', text: spec.sub }) : null),
      h('div', { class: 'frame-tools' }, spec.tools || [], btn));
    var el = h('figure', { class: 'frame' }, head, spec.legend ? h('div', { class: 'legend-row' }, spec.legend) : null, box, tbox, spec.after || null);
    spec.chart(box);
    return {
      el: el, box: box,
      refresh: function () { if (on) tbox.replaceChildren(spec.table()); else spec.chart(box); },
    };
  }

  /* ---------- 1. signature chart: the missing piece ---------- */
  function initSignature() {
    var root = document.getElementById('sig');
    var state = 'withheld';
    var rows = [];
    var tracks = [];
    var list = h('ol', { class: 'sig-rows' });
    SITE.signature.forEach(function (c) {
      var bar = h('i', { class: 'sig-bar' });
      var ghost = h('i', { class: 'sig-ghost' });
      var dot = h('i', { class: 'sig-dot' });
      var track = h('div', { class: 'sig-track' }, bar, ghost, dot);
      var what = h('span', { class: 'sig-what' });
      var num = h('span', { class: 'sig-num' });
      var unit = h('small', { text: '%' });
      var val = h('div', { class: 'sig-val' }, num, unit);
      var sub = h('span', { class: 'sig-sub' });
      var li = h('li', { class: 'sig-row', tabindex: '0' },
        h('div', { class: 'sig-label' }, h('span', { class: 'sig-case', text: c.case }), what, sub), val, track);
      bindTip(li, function () {
        var rowsT = [{ l: 'Withheld', v: pct(c.withheld) + '%', c: '#a8c7ee' }];
        c.supplied.forEach(function (s) { rowsT.push({ l: 'Supplied: ' + s.label, v: pct(s.value) + '%', c: MODELS.beam.color }); });
        return { title: c.case + ', Beam high', rows: rowsT, note: c.scope };
      });
      list.append(li);
      rows.push({ c: c, bar: bar, ghost: ghost, dot: dot, what: what, num: num, sub: sub, track: track });
      tracks.push(track);
    });
    function place(r, v, node, animate) {
      var w = r.track.clientWidth;
      node.style.transform = 'translateX(' + (w * v / 100) + 'px)';
    }
    function paint() {
      root.dataset.state = state;
      rows.forEach(function (r) {
        var c = r.c, sup = state === 'supplied';
        var v = sup ? c.supplied[0].value : c.withheld;
        r.num.textContent = pct(v);
        r.what.textContent = sup ? c.supplied_text : c.withheld_text;
        r.bar.style.transform = 'scaleX(' + (v / 100) + ')';
        place(r, v, r.dot);
        place(r, c.withheld, r.ghost);
        r.sub.textContent = sup
          ? signed(c.supplied[0].value - c.withheld) + ' points' + (c.supplied.length > 1 ? ' \u00b7 evidence version ' + pct(c.supplied[1].value) + '%' : '')
          : '';
      });
    }
    var toggle = seg([{ id: 'withheld', label: 'Withheld' }, { id: 'supplied', label: 'Supplied' }], state, function (id) { state = id; paint(); }, false, 'Missing piece');
    var axis = h('div', { class: 'sig-axis', 'aria-hidden': 'true' }, ['0', '25', '50', '75', '100'].map(function (t) { return h('span', { text: t }); }));
    root.append(
      h('div', { class: 'sig-head' }, h('span', { class: 'sig-kicker', text: 'Beam, high effort. Pass rate on hidden tests, %' }), toggle),
      list, axis,
      h('p', { class: 'sig-foot', text: 'Hover or focus a row for the counts. Ghost circle: the withheld score. Case 1 uses the ' + SITE.signature[0].n + ' tasks with a hidden header; cases 2 and 3 use all tasks.' }));
    new ResizeObserver(function () { rows.forEach(function (r) { var sup = state === 'supplied'; place(r, sup ? r.c.supplied[0].value : r.c.withheld, r.dot); place(r, r.c.withheld, r.ghost); }); }).observe(tracks[0]);
    requestAnimationFrame(function () { paint(); });
  }

  /* ---------- 3. leaderboard ---------- */
  function initLeaderboard() {
    var root = document.getElementById('lb');
    var st = { effort: 'all', metric: 'pass', col: null, dir: 'desc' };
    var wrap = h('div', { class: 'lb-wrap' });
    var tools = h('div', { class: 'lb-tools' },
      h('div', null, h('span', { class: 'lbl', text: 'Effort' }), seg([{ id: 'all', label: 'All' }, { id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }], st.effort, function (id) { st.effort = id; draw(); }, true, 'Filter by effort')),
      h('div', null, h('span', { class: 'lbl', text: 'Show' }), seg([{ id: 'pass', label: 'Pass rate' }, { id: 'tokens', label: 'Mean output tokens' }], st.metric, function (id) { st.metric = id; draw(); }, true, 'Metric')));
    var note = h('p', { class: 'lb-note' });
    root.append(tools, wrap, note);
    var multi = [];
    SETTINGS.forEach(function (s) { BENCH.forEach(function (b) { var c = CELLS[s.key][b.id]; if (c.runs > 1) multi.push(settingLabel(s) + ' / ' + b.short + ': ' + c.runs); }); });
    function draw() {
      var rows = SETTINGS.filter(function (s) { return st.effort === 'all' || s.effort === st.effort; });
      if (st.col) {
        rows = rows.slice().sort(function (a, b) {
          var va = metricVal(a.key, st.col, st.metric), vb = metricVal(b.key, st.col, st.metric);
          return st.dir === 'desc' ? vb - va : va - vb;
        });
      }
      var best = {};
      BENCH.forEach(function (b) { best[b.id] = Math.max.apply(null, SETTINGS.map(function (s) { return CELLS[s.key][b.id].pass; })); });
      var thead = h('tr', null, h('th', { scope: 'col', text: 'Setting' }));
      BENCH.forEach(function (b) {
        var sorted = st.col === b.id ? (st.dir === 'desc' ? 'descending' : 'ascending') : null;
        var btn = h('button', { type: 'button', text: b.label });
        btn.addEventListener('click', function () {
          if (st.col === b.id && st.dir === 'desc') st.dir = 'asc';
          else if (st.col === b.id) { st.col = null; st.dir = 'desc'; } else { st.col = b.id; st.dir = 'desc'; }
          draw();
        });
        thead.append(h('th', { scope: 'col', 'aria-sort': sorted }, btn));
      });
      var prev = null;
      var tbody = h('tbody', null, rows.map(function (s) {
        var tr = h('tr', { class: !st.col && prev && prev !== s.model ? 'grp-start' : null });
        prev = s.model;
        var dot = h('i'); dot.style.background = color(s);
        tr.append(h('th', { scope: 'row' }, h('span', { class: 'who' }, dot, settingLabel(s))));
        BENCH.forEach(function (b) {
          var c = CELLS[s.key][b.id];
          var isBest = st.metric === 'pass' && Math.abs(c.pass - best[b.id]) < 1e-9;
          var txt = fmtMetric(st.metric === 'tokens' ? c.tokens : c.pass, st.metric) + (c.runs > 1 ? '†' : '');
          var td = h('td', { class: 'num' + (isBest ? ' best' : ''), tabindex: '0' }, h('span', { text: txt }));
          bindTip(td, function () {
            var rowsT = st.metric === 'pass'
              ? [{ l: 'pass rate', v: pct(c.pass) + '%', c: color(s) }, { l: '95% CI', v: pct(c.lo) + ' to ' + pct(c.hi) }, { l: 'mean output tokens', v: tokf(c.tokens) }]
              : [{ l: 'mean output tokens', v: tokf(c.tokens), c: color(s) }, { l: 'pass rate', v: pct(c.pass) + '%' }];
            return { title: settingLabel(s) + ' · ' + b.label, rows: rowsT, note: c.n + ' tasks, ' + c.runs + (c.runs > 1 ? ' runs, mean' : ' run') };
          });
          tr.append(td);
        });
        return tr;
      }));
      var table = h('table', { class: 'lb' },
        h('caption', { class: 'sr', text: (st.metric === 'pass' ? 'Pass rate, %' : 'Mean output tokens') + ' by setting and benchmark' }),
        h('thead', null, thead), tbody);
      wrap.replaceChildren(h('div', { class: 'table-scroll' }, table));
      note.textContent = (st.metric === 'pass' ? 'Bold and underlined: best of all seven settings in that column. ' : '') +
        '† Mean of 3 runs per task (Beam high, cases 1 and 3); every other cell is one run. Case 1 is the Tier B build set (' + BYID.c1.n + ' tasks), case 2 uses the same tasks, case 3 has ' + BYID.c3a.n +
        ' repair tasks. Qwen runs with thinking on and has no effort level, so it shows only under All.';
    }
    draw();
  }

  /* ---------- 4. effort small multiples ---------- */
  function niceMax(v) { var p = Math.pow(10, Math.floor(Math.log10(v))); var f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; }
  function initEffort() {
    var root = document.getElementById('effort-chart');
    var f = SITE.effort_facts;
    document.getElementById('effort-facts').textContent =
      'Case 3 without the hint, Beam high minus low: ' + signed(f.case3_gain) + ' points (95% CI ' + signed(f.case3_lo) + ' to ' + signed(f.case3_hi) + ') for ' + f.case3_tokens.toFixed(1) +
      ' times the output tokens. Case 1: ' + signed(f.case1_gain) + ' points (' + signed(f.case1_lo) + ' to ' + signed(f.case1_hi) + ') for ' + f.case1_tokens.toFixed(1) + ' times. The pre-registered H17 expected under 5 points, so it was rejected: effort helps. The contract hint adds ' +
      signed(f.hint_gain_high) + ' points at high effort.';
    var metric = 'pass';
    var shared = { idx: null, panels: [] };
    function setIdx(i) { shared.idx = i; shared.panels.forEach(function (p) { p(i); }); }
    var beam = SETTINGS.filter(function (s) { return s.model === 'beam'; });
    var gpt = SETTINGS.filter(function (s) { return s.model === 'gptoss'; });
    var qwen = SETTINGS.filter(function (s) { return s.model === 'qwen'; })[0];

    function panel(b, w) {
      var H = 210, m = { l: 36, r: 62, t: 14, b: 28 };
      var pw = w - m.l - m.r, ph = H - m.t - m.b;
      var all = beam.concat(gpt, [qwen]).map(function (s) { return metricVal(s.key, b.id, metric); });
      var ymax = metric === 'pass' ? 100 : niceMax(Math.max.apply(null, all) * 1.05);
      function X(i) { return m.l + 10 + (pw - 20) * i / 2; }
      function Y(v) { return m.t + ph - ph * v / ymax; }
      var svg = sv('svg', { width: w, height: H, viewBox: '0 0 ' + w + ' ' + H, role: 'group', 'aria-label': b.label + ', ' + (metric === 'pass' ? 'pass rate' : 'mean output tokens') + ' by effort' });
      [0, 0.5, 1].forEach(function (t) {
        var y = Y(ymax * t);
        svg.append(sv('line', { class: t === 0 ? 'axis-line' : 'grid-line', x1: m.l, x2: w - m.r + 6, y1: y, y2: y }),
          sv('text', { class: 'axis-text', x: m.l - 6, y: y + 4, 'text-anchor': 'end', text: metric === 'pass' ? String(Math.round(ymax * t)) : (t === 0 ? '0' : tokk(ymax * t)) }));
      });
      EFFORTS.forEach(function (e, i) { svg.append(sv('text', { class: 'axis-text', x: X(i), y: H - 8, 'text-anchor': 'middle', text: e })); });
      var vline = sv('line', { class: 'axis-line', x1: 0, x2: 0, y1: m.t, y2: m.t + ph, opacity: 0 });
      vline.style.stroke = '#8a8d93';
      svg.append(vline);
      var labels = [];
      var marks = [];
      function addSeries(list, c) {
        var pts = list.map(function (s, i) { return { x: X(i), y: Y(metricVal(s.key, b.id, metric)) }; });
        svg.append(sv('polyline', { points: pts.map(function (p) { return p.x + ',' + p.y; }).join(' '), fill: 'none', stroke: c, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
        pts.forEach(function (p, i) {
          var dot = sv('circle', { cx: p.x, cy: p.y, r: 4.5, fill: c, stroke: '#fcfcfb', 'stroke-width': 2 });
          svg.append(dot); marks.push({ i: i, el: dot, c: c });
        });
        return pts[pts.length - 1];
      }
      var endB = addSeries(beam, MODELS.beam.color);
      var endG = addSeries(gpt, MODELS.gptoss.color);
      var qv = metricVal(qwen.key, b.id, metric);
      var qpts = [0, 1, 2].map(function (i) { return { x: X(i), y: Y(qv) }; });
      svg.insertBefore(sv('line', { x1: qpts[0].x, x2: qpts[2].x, y1: qpts[0].y, y2: qpts[0].y, stroke: MODELS.qwen.color, 'stroke-width': 1.5 }), svg.children[3]);
      var qdot = sv('circle', { cx: qpts[2].x, cy: qpts[2].y, r: 4, fill: MODELS.qwen.color, stroke: '#fcfcfb', 'stroke-width': 2 });
      svg.append(qdot);
      labels.push({ y: endB.y, t: 'Beam', x: endB.x }, { y: endG.y, t: 'GPT-OSS', x: endG.x }, { y: qpts[2].y, t: 'Qwen', x: qpts[2].x });
      labels.sort(function (a, c) { return a.y - c.y; });
      labels.forEach(function (l, k) { l.py = k === 0 ? l.y : Math.max(l.y, labels[k - 1].py + 13); });
      labels.forEach(function (l) { svg.append(sv('text', { class: 'lab', x: l.x + 9, y: l.py + 4, text: l.t })); });
      function tipFor(i) {
        var rowsT = [
          { l: 'Beam', c: MODELS.beam.color, v: fmtMetric(metricVal(beam[i].key, b.id, metric), metric) },
          { l: 'GPT-OSS', c: MODELS.gptoss.color, v: fmtMetric(metricVal(gpt[i].key, b.id, metric), metric) },
          { l: 'Qwen, thinking on (reference)', c: MODELS.qwen.color, v: fmtMetric(qv, metric) },
        ];
        return { title: b.label + ' · ' + EFFORTS[i] + ' effort', rows: rowsT, note: metric === 'pass' ? 'Beam 95% CI ' + pct(CELLS[beam[i].key][b.id].lo) + ' to ' + pct(CELLS[beam[i].key][b.id].hi) : 'mean output tokens per call' };
      }
      var frameEl = sv('rect', { class: 'panel-frame', x: -4, y: -4, width: w + 8, height: H + 8, rx: 4 });
      svg.insertBefore(frameEl, svg.firstChild);
      var g = svg;
      g.setAttribute('tabindex', '0');
      function paint(i) {
        vline.setAttribute('opacity', i === null ? 0 : 1);
        if (i !== null) { vline.setAttribute('x1', X(i)); vline.setAttribute('x2', X(i)); }
        marks.forEach(function (mk) { mk.el.setAttribute('r', i === mk.i ? 6 : 4.5); });
      }
      shared.panels.push(paint);
      EFFORTS.forEach(function (e, i) {
        var r = sv('rect', { class: 'hit', x: X(i) - (pw - 20) / 4, y: m.t - 6, width: (pw - 20) / 2, height: ph + 12 });
        r.addEventListener('pointermove', function (ev) { setIdx(i); tipShow(ev.clientX, ev.clientY, tipFor(i)); });
        r.addEventListener('pointerleave', function () { setIdx(null); tipHide(); });
        svg.append(r);
      });
      var cur = 2;
      svg.addEventListener('focus', function () { setIdx(cur); var r = svg.getBoundingClientRect(); tipShow(r.left + X(cur), r.top + 40, tipFor(cur)); });
      svg.addEventListener('blur', function () { setIdx(null); tipHide(); });
      svg.addEventListener('keydown', function (ev) {
        if (ev.key === 'ArrowRight') cur = Math.min(2, cur + 1); else if (ev.key === 'ArrowLeft') cur = Math.max(0, cur - 1); else return;
        ev.preventDefault(); setIdx(cur); var r = svg.getBoundingClientRect(); tipShow(r.left + X(cur), r.top + 40, tipFor(cur));
      });
      svg.setAttribute('data-tip', '');
      return svg;
    }

    var grid = h('div', { class: 'multiples' });
    var boxes = BENCH.map(function (b) {
      var box = h('div', { class: 'chart' });
      grid.append(h('div', { class: 'panel-box' }, h('div', { class: 'panel-title', text: b.label }), h('div', { class: 'frame-sub', text: b.n + ' tasks' }), box));
      return { b: b, box: box, ro: false };
    });
    function drawAll() {
      shared.panels = [];
      boxes.forEach(function (x) {
        var w = Math.floor(x.box.clientWidth);
        if (!w) return;
        x.box.replaceChildren(panel(x.b, w));
        x.last = w;
      });
    }
    var obs = new ResizeObserver(function () {
      var ch = boxes.some(function (x) { return Math.floor(x.box.clientWidth) !== x.last; });
      if (ch) requestAnimationFrame(drawAll);
    });
    boxes.forEach(function (x) { obs.observe(x.box); });

    var fr = frame({
      title: 'Pass rate and output tokens by effort, Beam against GPT-OSS',
      sub: 'Qwen is drawn as a flat reference line: it has one setting, thinking on. Hover a panel to compare the same effort in all five.',
      legend: legendList([{ label: 'Beam', color: MODELS.beam.color }, { label: 'GPT-OSS-120B', color: MODELS.gptoss.color }, { label: 'Qwen3.5-397B-A17B, thinking on (reference)', color: MODELS.qwen.color }]),
      tools: [seg([{ id: 'pass', label: 'Pass rate' }, { id: 'tokens', label: 'Output tokens' }], metric, function (id) { metric = id; drawAll(); }, true, 'Metric')],
      chart: function (box) { box.replaceChildren(grid); requestAnimationFrame(drawAll); },
      table: function () {
        return dataTable(['Setting'].concat(BENCH.map(function (b) { return b.label; })),
          SETTINGS.map(function (s) { return [settingLabel(s)].concat(BENCH.map(function (b) { return fmtMetric(metricVal(s.key, b.id, metric), metric); })); }),
          (metric === 'pass' ? 'Pass rate, %' : 'Mean output tokens') + ' by setting');
      },
    });
    fr.box.addEventListener('h2c-show', function () { drawAll(); });
    // metric toggle must also refresh a visible table
    root.append(fr.el);
    root.querySelector('.seg').addEventListener('click', function () { fr.refresh(); if (!fr.box.hidden) drawAll(); });
  }

  /* ---------- 5. dumbbells: what extra information buys ---------- */
  function initGains() {
    var root = document.getElementById('gains');
    var specs = [
      { title: 'Case 3: without the hint to with the hint', from: 'c3a', to: 'c3b', fromL: 'no hint', toL: 'hint' },
      { title: 'Case 2: no probes to up to 5 probes', from: 'c2a', to: 'c2b', fromL: 'no probes', toL: '5 probes' },
    ];
    var grid = h('div', { class: 'gains-grid' });
    specs.forEach(function (sp) {
      function draw(w) {
        var rowH = 34, gap = 10, m = { l: 112, r: 52, t: 6, b: 26 };
        var groups = SETTINGS.map(function (s, i) { return i > 0 && SETTINGS[i - 1].model !== s.model ? gap : 0; });
        var ys = []; var acc = m.t;
        SETTINGS.forEach(function (s, i) { acc += groups[i]; ys.push(acc + rowH / 2); acc += rowH; });
        var H = acc + m.b, pw = w - m.l - m.r;
        function X(v) { return m.l + pw * v / 100; }
        var svg = sv('svg', { width: w, height: H, viewBox: '0 0 ' + w + ' ' + H, role: 'group', 'aria-label': sp.title });
        [0, 25, 50, 75, 100].forEach(function (t) {
          svg.append(sv('line', { class: t === 0 ? 'axis-line' : 'grid-line', x1: X(t), x2: X(t), y1: m.t, y2: H - m.b + 4 }),
            sv('text', { class: 'axis-text', x: X(t), y: H - 8, 'text-anchor': 'middle', text: String(t) }));
        });
        SETTINGS.forEach(function (s, i) {
          var a = CELLS[s.key][sp.from], b = CELLS[s.key][sp.to], y = ys[i], c = color(s);
          var g = sv('g', { tabindex: '0', class: 'mk', 'aria-label': settingLabel(s) + ' ' + pct(a.pass) + ' to ' + pct(b.pass) });
          g.append(
            sv('rect', { class: 'hit', x: 0, y: y - rowH / 2, width: w, height: rowH }),
            sv('line', { x1: X(a.pass), x2: X(b.pass), y1: y, y2: y, stroke: c, 'stroke-width': 3, 'stroke-linecap': 'round' }),
            sv('circle', { cx: X(a.pass), cy: y, r: 5, fill: '#fcfcfb', stroke: c, 'stroke-width': 2.5 }),
            sv('circle', { cx: X(b.pass), cy: y, r: 5.5, fill: c, stroke: '#fcfcfb', 'stroke-width': 2 }),
            sv('text', { class: 'lab', x: X(Math.max(a.pass, b.pass)) + 11, y: y + 4, text: signed(b.pass - a.pass) }));
          var ld = sv('circle', { cx: 8, cy: y, r: 4.5, fill: c });
          svg.append(ld, sv('text', { class: 'lab-soft', x: 20, y: y + 4, text: settingLabel(s).replace(' (thinking on)', '') }), g);
          bindTip(g, function () {
            return { title: settingLabel(s) + ' · ' + sp.title.split(':')[0], rows: [
              { l: sp.toL, v: pct(b.pass) + '%', c: c }, { l: sp.fromL, v: pct(a.pass) + '%', c: c }, { l: 'change', v: signed(b.pass - a.pass) + ' points' }],
              note: sp.toL + ' 95% CI ' + pct(b.lo) + ' to ' + pct(b.hi) + '; ' + sp.fromL + ' ' + pct(a.lo) + ' to ' + pct(a.hi) };
          });
        });
        return svg;
      }
      var fr = frame({
        title: sp.title,
        sub: 'Pass rate, %. The number at the end is the change in points.',
        legend: legendList([{ label: sp.fromL, kind: 'key-dot key-hollow' }, { label: sp.toL, kind: 'key-dot', color: '#464a51' }]),
        chart: function (box) { fit(box, draw); },
        table: function () {
          return dataTable(['Setting', sp.fromL, sp.toL, 'Change'], SETTINGS.map(function (s) {
            var a = CELLS[s.key][sp.from].pass, b = CELLS[s.key][sp.to].pass;
            return [settingLabel(s), pct(a), pct(b), signed(b - a)];
          }), sp.title);
        },
      });
      grid.append(fr.el);
    });
    root.append(grid);
  }

  /* ---------- 6. Beam in detail: grouped columns ---------- */
  function initBeam() {
    var root = document.getElementById('beam-detail');
    var D = SITE.detail;
    var tint = '#a8c7ee', dark = MODELS.beam.color;
    function columns(items, label) {
      /* items: [{effort, a, b}] a = withheld (light), b = supplied (dark) */
      return function (w) {
        var H = 220, m = { l: 30, r: 4, t: 24, b: 28 }, ph = H - m.t - m.b, pw = w - m.l - m.r;
        function Y(v) { return m.t + ph - ph * v / 100; }
        var svg = sv('svg', { width: w, height: H, viewBox: '0 0 ' + w + ' ' + H, role: 'group', 'aria-label': label });
        [0, 25, 50, 75, 100].forEach(function (t) {
          svg.append(sv('line', { class: t === 0 ? 'axis-line' : 'grid-line', x1: m.l, x2: w - m.r, y1: Y(t), y2: Y(t) }),
            sv('text', { class: 'axis-text', x: m.l - 6, y: Y(t) + 4, 'text-anchor': 'end', text: String(t) }));
        });
        var bw = Math.min(24, Math.max(14, pw / 3 / 2 - 8));
        items.forEach(function (it, i) {
          var cx = m.l + pw * (i + 0.5) / 3;
          svg.append(sv('text', { class: 'axis-text', x: cx, y: H - 8, 'text-anchor': 'middle', text: it.effort }));
          [{ v: it.a.v, x: cx - bw - 1, c: tint, l: it.a.l }, { v: it.b.v, x: cx + 1, c: dark, l: it.b.l }].forEach(function (bar) {
            var top = Y(bar.v), bh = Math.max(0, Y(0) - top), r = Math.min(4, bh);
            var d = 'M' + bar.x + ',' + Y(0) + 'V' + (top + r) + 'Q' + bar.x + ',' + top + ' ' + (bar.x + r) + ',' + top + 'H' + (bar.x + bw - r) + 'Q' + (bar.x + bw) + ',' + top + ' ' + (bar.x + bw) + ',' + (top + r) + 'V' + Y(0) + 'Z';
            var g = sv('g', { tabindex: '0', class: 'mk', 'aria-label': it.effort + ' effort, ' + bar.l + ' ' + pct(bar.v) });
            var path = sv('path', { d: d, fill: bar.c });
            g.append(sv('rect', { class: 'hit', x: bar.x - 1, y: m.t - 18, width: bw + 2, height: ph + 18 }), path,
              sv('text', { class: 'lab', x: bar.x + bw / 2, y: top - 5, 'text-anchor': 'middle', text: pct(bar.v) }));
            svg.append(g);
            bindTip(g, function () {
              return { title: it.effort + ' effort', rows: [{ l: bar.l, v: pct(bar.v) + '%', c: bar.c }].concat(it.extra || []), note: it.note };
            });
            g.addEventListener('pointerenter', function () { path.setAttribute('stroke', '#15161a'); path.setAttribute('stroke-width', '1.5'); });
            g.addEventListener('pointerleave', function () { path.removeAttribute('stroke'); });
            g.addEventListener('focus', function () { path.setAttribute('stroke', '#15161a'); path.setAttribute('stroke-width', '1.5'); });
            g.addEventListener('blur', function () { path.removeAttribute('stroke'); });
          });
        });
        return svg;
      };
    }
    function panel(cfg) {
      return frame({
        title: cfg.title, sub: cfg.sub,
        legend: legendList([{ label: cfg.aL, kind: 'key-bar', color: tint }, { label: cfg.bL, kind: 'key-bar', color: dark }]),
        chart: function (box) { fit(box, columns(cfg.items, cfg.title)); },
        table: function () {
          return dataTable(['Effort', cfg.aL, cfg.bL].concat(cfg.extraHead || []), cfg.items.map(function (it) { return [it.effort, pct(it.a.v), pct(it.b.v)].concat(it.tableExtra || []); }), cfg.title);
        },
        after: h('p', { class: 'under' }, cfg.under),
      });
    }
    var c1 = EFFORTS.map(function (e) {
      var d = D.case1[e];
      return { effort: e, a: { v: d.hidden, l: 'Header hidden' }, b: { v: d.visible, l: 'Header visible' },
        extra: [{ l: 'gap', v: signed(-(d.visible - d.hidden)) + ' points' }, { l: 'gap 95% CI', v: signed(d.diff_lo) + ' to ' + signed(d.diff_hi) }],
        note: d.n_hidden + ' hidden-header and ' + d.n_visible + ' visible-header tasks, ' + d.runs + (d.runs > 1 ? ' runs, mean' : ' run'), tableExtra: [] };
    });
    var c3 = EFFORTS.map(function (e) {
      var d = D.case3[e];
      return { effort: e, a: { v: d.base, l: 'No hint' }, b: { v: d.hint, l: 'Hint' },
        extra: [{ l: 'full repair, no hint', v: pct(d.base_full) + '%' }, { l: 'full repair, hint', v: pct(d.hint_full) + '%' }],
        note: d.n + ' tasks, ' + d.runs + (d.runs > 1 ? ' runs, mean' : ' run'), tableExtra: [pct(d.base_full), pct(d.hint_full)] };
    });
    var c2 = EFFORTS.map(function (e) {
      var d = D.case2[e];
      return { effort: e, a: { v: d.placebo, l: 'No probes' }, b: { v: d.p5, l: 'Up to 5 probes' },
        extra: [{ l: 'fully solved, no probes', v: pct(d.placebo_full) + '%' }, { l: 'fully solved, 5 probes', v: pct(d.p5_full) + '%' }],
        note: d.n + ' tasks, ' + d.runs + ' run', tableExtra: [pct(d.placebo_full), pct(d.p5_full)] };
    });
    function share(items, ka, kb) {
      return items.map(function (e) { return [h('b', { text: e.effort }), ' ', pct(e.a2) + ' to ' + pct(e.b2) + '%']; });
    }
    var p1 = panel({ title: 'Case 1: header hidden or visible', sub: 'Pass rate, %. Tier B split by the hidden-header switch.', aL: 'Header hidden', bL: 'Header visible', items: c1,
      under: ['Hidden minus visible, in points: ', EFFORTS.map(function (e, i) { return [h('b', { text: signed(-(D.case1[e].visible - D.case1[e].hidden)) }), ' at ' + e, i < 2 ? ', ' : '. ']; }), 'The gap narrows with effort but does not close.'] });
    var p2 = panel({ title: 'Case 2: no probes or 5 probes', sub: 'Pass rate, %. Same 50 tasks, one run each.', aL: 'No probes', bL: 'Up to 5 probes', items: c2, extraHead: ['Solved fully, no probes %', 'Solved fully, 5 probes %'],
      under: ['Fully solved (every test passes), no probes to 5 probes: ', EFFORTS.map(function (e, i) { return [h('b', { text: pct(D.case2[e].placebo_full) + ' to ' + pct(D.case2[e].p5_full) }), i < 2 ? '; ' : '.']; })] });
    var p3 = panel({ title: 'Case 3: no hint or hint', sub: 'Pass rate, %. 64 repair tasks.', aL: 'No hint', bL: 'Hint', items: c3, extraHead: ['Full repair, no hint %', 'Full repair, hint %'],
      under: ['Full-repair share (every test passes), no hint to hint: ', EFFORTS.map(function (e, i) { return [h('b', { text: pct(D.case3[e].base_full) + ' to ' + pct(D.case3[e].hint_full) }), i < 2 ? '; ' : '.']; })] });
    root.append(h('div', { class: 'detail-grid' }, p1.el, p2.el, p3.el));
  }

  /* ---------- 7. task explorer ---------- */
  function initExplorer() {
    var root = document.getElementById('explorer');
    var SUITES = [
      { id: 'all', label: 'All' }, { id: 'tier_a', label: 'Tier A' }, { id: 'tier_b', label: 'Tier B (cases 1, 2)' },
      { id: 'heal_v3', label: 'Case 3 repair' }, { id: 'heal_v3_hint', label: 'Case 3 with hint' },
    ];
    var SUITE_NAME = { tier_a: 'Case 1, Tier A (real API spec)', tier_b: 'Cases 1 and 2, Tier B', heal_v3: 'Case 3, base task', heal_v3_hint: 'Case 3, with contract hint' };
    var PAGE = 60;
    root.append(h('div', { class: 'ex-skeleton', text: 'Loading tasks' }));
    var started = false;
    function start() {
      if (started) return; started = true;
      fetch('data/tasks.json').then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(mount).catch(function () {
        root.replaceChildren(h('p', { class: 'ex-empty', text: 'The task list could not be loaded. It is in the dataset repo.' }));
      });
    }
    new IntersectionObserver(function (es, o) { if (es[0].isIntersecting) { start(); o.disconnect(); } }, { rootMargin: '800px' }).observe(root);
    function mount(data) {
      var tasks = data.tasks, st = { suite: 'all', q: '', shown: PAGE, sel: 0 }, current = null;
      var search = h('input', { type: 'search', placeholder: 'Search by name, id or switch', 'aria-label': 'Search tasks' });
      var chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filter by case' });
      var count = h('p', { class: 'ex-count', 'aria-live': 'polite' });
      var list = h('ul', { class: 'ex-list' });
      var more = h('button', { type: 'button', class: 'tbtn', text: 'Show more' });
      var detail = h('div', { class: 'ex-detail', 'aria-live': 'polite' });
      SUITES.forEach(function (s) {
        var n = s.id === 'all' ? tasks.length : data.suites[s.id];
        var b = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(s.id === st.suite), text: s.label + ' ' + n });
        b.addEventListener('click', function () { st.suite = s.id; st.shown = PAGE; [].forEach.call(chips.children, function (c) { c.setAttribute('aria-pressed', 'false'); }); b.setAttribute('aria-pressed', 'true'); render(); });
        chips.append(b);
      });
      search.addEventListener('input', function () { st.q = search.value.trim().toLowerCase(); st.shown = PAGE; render(); });
      more.addEventListener('click', function () { st.shown += PAGE; render(); });
      function filtered() {
        return tasks.filter(function (t) {
          if (st.suite !== 'all' && t.suite !== st.suite) return false;
          if (!st.q) return true;
          return (t.id + ' ' + t.title + ' ' + t.features.join(' ')).toLowerCase().indexOf(st.q) !== -1;
        });
      }
      function show(t) {
        current = t;
        var reqRows = t.requests.map(function (r) {
          return h('tr', null, h('td', { text: r[0] }), h('td', { class: 'u', text: r[1] }), h('td', { text: String(r[2]) }), h('td', { text: r[3] }));
        });
        var parts = [
          h('h3', { text: t.title }),
          h('p', { class: 'ex-meta', text: SUITE_NAME[t.suite] + ' · ' + t.id + ' · ' + t.n_requests + ' requests in the recording' }),
          h('p', { class: 'ex-meta', text: t.features.filter(function (f) { return f.indexOf('tier:') !== 0 && f.indexOf('case:') !== 0; }).join(' · ') }),
          h('h4', { text: 'Goal the model reads' }),
          h('pre', { class: 'goal', tabindex: '0', text: t.goal }),
          h('h4', { text: 'Requests in the recording' + (t.n_requests > t.requests.length ? ' (first ' + t.requests.length + ' of ' + t.n_requests + ')' : '') }),
          h('div', { class: 'req', tabindex: '0' }, h('table', null, h('thead', null, h('tr', null, ['Method', 'URL', 'Status', 'Type'].map(function (x) { return h('th', { scope: 'col', text: x }); }))), h('tbody', null, reqRows))),
          t.excerpt ? h('h4', { text: 'One exchange, shortened' }) : null,
          t.excerpt ? h('pre', { class: 'excerpt', tabindex: '0', text: t.excerpt }) : null];
        detail.replaceChildren.apply(detail, parts.filter(Boolean));
      }
      function render() {
        var items = filtered();
        count.textContent = items.length + ' of ' + tasks.length + ' tasks';
        var vis = items.slice(0, st.shown);
        list.replaceChildren.apply(list, vis.map(function (t) {
          var b = h('button', { type: 'button', 'aria-current': String(current === t) },
            h('span', { class: 't', text: t.title }), h('span', { class: 'm', text: SUITE_NAME[t.suite] + ' · ' + t.id }));
          b.addEventListener('click', function () { show(t); [].forEach.call(list.querySelectorAll('button'), function (x) { x.setAttribute('aria-current', 'false'); }); b.setAttribute('aria-current', 'true'); });
          return h('li', null, b);
        }));
        if (!items.length) list.append(h('li', { class: 'ex-empty', text: 'No task matches.' }));
        more.hidden = items.length <= st.shown;
        if (!current && items.length) { show(items[0]); var f = list.querySelector('button'); if (f) f.setAttribute('aria-current', 'true'); }
      }
      root.replaceChildren(h('div', { class: 'ex-list-wrap' }, h('div', { class: 'ex-controls' }, search, chips), count, list, h('div', { class: 'ex-more' }, more)), detail);
      render();
    }
  }

  /* ---------- nav scroll-spy, copy buttons ---------- */
  function initNav() {
    var links = [].slice.call(document.querySelectorAll('.bar-nav a'));
    var map = {};
    links.forEach(function (a) { map[a.getAttribute('href').slice(1)] = a; });
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        links.forEach(function (a) { a.removeAttribute('aria-current'); });
        var a = map[e.target.id]; if (a) { a.setAttribute('aria-current', 'true'); a.scrollIntoView({ block: 'nearest', inline: 'center' }); }
      });
    }, { rootMargin: '-20% 0px -70% 0px' });
    Object.keys(map).forEach(function (id) { var s = document.getElementById(id); if (s) io.observe(s); });
  }
  function initCopy() {
    [].forEach.call(document.querySelectorAll('.copy'), function (b) {
      b.addEventListener('click', function () {
        var text = document.getElementById(b.getAttribute('data-copy')).textContent;
        function done() { b.textContent = 'Copied'; setTimeout(function () { b.textContent = 'Copy'; }, 1600); }
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
        else fallback();
        function fallback() {
          var ta = h('textarea', { 'aria-hidden': 'true' }); ta.value = text; document.body.append(ta); ta.select();
          try { document.execCommand('copy'); done(); } catch (e) { b.textContent = 'Press Ctrl+C'; }
          ta.remove();
        }
      });
    });
  }

  document.getElementById('nboot').textContent = SITE.n_boot.toLocaleString('en-US');
  initSignature();
  initLeaderboard();
  initEffort();
  initGains();
  initBeam();
  initExplorer();
  initNav();
  initCopy();
})();
