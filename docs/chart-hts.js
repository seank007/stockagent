/*
 * chart-hts.js — 라인 차트를 HTS 스타일 캔들차트로 교체한다.
 *
 * 페이지 인라인 스크립트가 정의한 renderChart(선 차트)를 Canvas 기반 캔들차트로
 * 오버라이드한다(본문 맨 끝에서 로드되어 window.renderChart 재할당).
 *   - 캔들(시가/고가/저가/종가) + 꼬리, 상승=청록/하락=적색
 *   - MA5/MA20 오버레이, 거래량 패널, RSI 패널(70/30 가이드)
 *   - 우측 가격축 · 하단 시간축 · 격자 · 현재가 태그 · 마우스 크로스헤어(OHLC 툴팁)
 * OHLC/거래량이 데이터에 없으면 종가에서 결정론적으로 합성한다(정적 데모용).
 * 반환값은 기존과 동일: {lastClose, changePct, lastRsi, lastMa5, lastMa20}.
 */
(function () {
  "use strict";

  var UP = "#1fd6a8", DOWN = "#ff5d6c", MA5 = "#e0b341", MA20 = "#7d8798",
    RSI = "#5aa3ff", GRID = "#161d27", GRID2 = "#10151d", AXIS = "#5a6577",
    BG = "#0a0e14", PANEL = "#090d13";
  var HEIGHT = 440;

  var INTERVAL_SEC = {
    minute1: 60, minute3: 180, minute5: 300, minute10: 600, minute15: 900,
    minute30: 1800, minute60: 3600, minute240: 14400,
    day: 86400, week: 604800, month: 2592000
  };

  // ---- 결정론적 난수 (합성 OHLC 재현성) ----
  function hashStr(s) {
    var h = 2166136261;
    s = String(s || "seed");
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function num(v) { return (v == null || isNaN(v)) ? null : Number(v); }

  // 종가 배열에서 OHLC + 거래량 합성 (실데이터가 있으면 그대로 사용)
  function buildBars(data, seedKey) {
    var closes = (data.closes || []).map(num).filter(function (v) { return v != null; });
    var n = closes.length;
    var opens = data.opens, highs = data.highs, lows = data.lows, vols = data.volumes;
    var hasOHLC = opens && highs && lows && opens.length === n && highs.length === n && lows.length === n;
    var hasVol = vols && vols.length === n;
    var rand = rng(hashStr(seedKey) ^ n);
    var bars = [];
    for (var i = 0; i < n; i++) {
      var c = closes[i], pc = i ? closes[i - 1] : c;
      var o, hi, lo, vol;
      if (hasOHLC) {
        o = num(opens[i]); hi = num(highs[i]); lo = num(lows[i]);
        if (o == null) o = pc; if (hi == null) hi = Math.max(o, c); if (lo == null) lo = Math.min(o, c);
      } else {
        o = i ? pc : c * (1 - 0.0015);
        var body = Math.abs(c - o);
        var span = body + c * (0.0016 + 0.0042 * rand());
        hi = Math.max(o, c) + span * (0.25 + 0.75 * rand());
        lo = Math.min(o, c) - span * (0.25 + 0.75 * rand());
      }
      hi = Math.max(hi, o, c); lo = Math.min(lo, o, c);
      if (hasVol) { vol = num(vols[i]) || 0; }
      else { vol = (0.55 + 0.9 * rand()) * (1 + Math.abs(c - o) / (c * 0.01 || 1)); }
      bars.push({ o: o, h: hi, l: lo, c: c, v: vol, up: c >= o });
    }
    return bars;
  }

  function fmtPrice(v) {
    if (v == null || isNaN(v)) return "";
    var a = Math.abs(v);
    if (a >= 1000) return Math.round(v).toLocaleString("en-US");
    if (a >= 1) return v.toFixed(1);
    if (a >= 0.01) return v.toFixed(3);
    return v.toFixed(6);
  }
  function pad2(x) { return x < 10 ? "0" + x : "" + x; }
  function fmtTime(ts, stepSec) {
    var d = new Date(ts);
    if (stepSec >= 86400 * 25) return (d.getFullYear() % 100) + "/" + pad2(d.getMonth() + 1);
    if (stepSec >= 86400) return pad2(d.getMonth() + 1) + "/" + pad2(d.getDate());
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  // svg → canvas 교체 (최초 1회). 같은 id/스타일 유지.
  function ensureCanvas(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    if (el.tagName && el.tagName.toLowerCase() === "canvas") return el;
    var cv = document.createElement("canvas");
    cv.id = id;
    cv.style.width = "100%";
    cv.style.height = HEIGHT + "px";
    cv.style.display = "block";
    cv.style.cursor = "crosshair";
    el.parentNode.replaceChild(cv, el);
    cv.addEventListener("mousemove", function (e) {
      var r = cv.getBoundingClientRect();
      cv.__hts.mouse = { x: e.clientX - r.left, y: e.clientY - r.top };
      draw(cv);
    });
    cv.addEventListener("mouseleave", function () { cv.__hts.mouse = null; draw(cv); });
    return cv;
  }

  function layout(W, H) {
    var padT = 10, padR = 66, padL = 8, timeAx = 20, gap = 10;
    var plotW = W - padL - padR;
    var innerH = H - padT - timeAx;
    var priceH = Math.round(innerH * 0.60);
    var volH = Math.round(innerH * 0.16);
    var rsiH = innerH - priceH - volH - gap * 2;
    var priceTop = padT;
    var volTop = priceTop + priceH + gap;
    var rsiTop = volTop + volH + gap;
    return { padL: padL, padR: padR, plotW: plotW, W: W, H: H, timeAx: timeAx,
      priceTop: priceTop, priceH: priceH, volTop: volTop, volH: volH, rsiTop: rsiTop, rsiH: rsiH };
  }

  function draw(cv) {
    var st = cv.__hts; if (!st) return;
    var data = st.data, bars = st.bars;
    var rect = cv.getBoundingClientRect();
    var W = Math.max(320, Math.round(rect.width)), H = HEIGHT;
    var dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    }
    var ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.textBaseline = "middle";

    var L = layout(W, H);
    var n = bars.length;
    if (!n) return;

    // ---- 가격 범위 ----
    var hi = -Infinity, lo = Infinity;
    for (var i = 0; i < n; i++) { if (bars[i].h > hi) hi = bars[i].h; if (bars[i].l < lo) lo = bars[i].l; }
    var m5 = (data.ma5 || []), m20 = (data.ma20 || []);
    for (i = 0; i < n; i++) {
      var a = num(m5[i]), b = num(m20[i]);
      if (a != null) { hi = Math.max(hi, a); lo = Math.min(lo, a); }
      if (b != null) { hi = Math.max(hi, b); lo = Math.min(lo, b); }
    }
    var pd = (hi - lo) * 0.08 || (hi * 0.01) || 1;
    var pHi = hi + pd, pLo = lo - pd;
    var pX = function (i) { return L.padL + (n <= 1 ? L.plotW / 2 : (i / (n - 1)) * L.plotW); };
    var priceY = function (v) { return L.priceTop + (L.priceH) * (1 - (v - pLo) / (pHi - pLo)); };

    // ---- 시간축 타임스탬프 ----
    var step = INTERVAL_SEC[data.interval || st.interval] || 3600;
    var lastTs = Date.now();
    var tsAt = function (i) { return lastTs - (n - 1 - i) * step * 1000; };

    // ---- 가격 격자 + 우측 라벨 ----
    ctx.strokeStyle = GRID; ctx.lineWidth = 1; ctx.fillStyle = AXIS; ctx.textAlign = "left";
    var rows = 5;
    for (i = 0; i <= rows; i++) {
      var gy = L.priceTop + (L.priceH / rows) * i;
      ctx.beginPath(); ctx.moveTo(L.padL, gy + .5); ctx.lineTo(L.padL + L.plotW, gy + .5); ctx.stroke();
      var pv = pHi - ((pHi - pLo) / rows) * i;
      ctx.fillText(fmtPrice(pv), L.padL + L.plotW + 6, gy);
    }

    // ---- 세로 격자 + 하단 시간 라벨 ----
    var vlines = 6;
    ctx.textAlign = "center";
    for (i = 1; i < vlines; i++) {
      var gx = L.padL + (L.plotW / vlines) * i;
      ctx.strokeStyle = GRID2; ctx.beginPath(); ctx.moveTo(gx + .5, L.priceTop); ctx.lineTo(gx + .5, L.rsiTop + L.rsiH); ctx.stroke();
      var bi = Math.round((i / vlines) * (n - 1));
      ctx.fillStyle = AXIS; ctx.fillText(fmtTime(tsAt(bi), step), gx, L.priceTop + L.priceH + L.volH + L.rsiH + 30);
    }

    // 패널 라벨
    ctx.textAlign = "left"; ctx.fillStyle = "#3a4658";
    ctx.fillText("거래량", L.padL + 2, L.volTop + 8);
    ctx.fillText("RSI 14", L.padL + 2, L.rsiTop + 8);

    // ---- 거래량 패널 ----
    var vMax = 0; for (i = 0; i < n; i++) vMax = Math.max(vMax, bars[i].v);
    var bw = L.plotW / n, body = Math.max(1, Math.min(bw * 0.64, 14));
    for (i = 0; i < n; i++) {
      var vh = vMax ? (bars[i].v / vMax) * (L.volH - 2) : 0;
      ctx.fillStyle = bars[i].up ? "rgba(31,214,168,.45)" : "rgba(255,93,108,.45)";
      ctx.fillRect(pX(i) - body / 2, L.volTop + L.volH - vh, body, vh);
    }

    // ---- 캔들 ----
    for (i = 0; i < n; i++) {
      var bar = bars[i], x = pX(i), col = bar.up ? UP : DOWN;
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
      // 꼬리
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + .5, priceY(bar.h));
      ctx.lineTo(Math.round(x) + .5, priceY(bar.l));
      ctx.stroke();
      // 몸통
      var yo = priceY(bar.o), yc = priceY(bar.c);
      var top = Math.min(yo, yc), hgt = Math.max(1, Math.abs(yc - yo));
      ctx.fillRect(x - body / 2, top, body, hgt);
    }

    // ---- MA 오버레이 ----
    function poly(arr, color) {
      ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.beginPath();
      var started = false;
      for (var k = 0; k < n; k++) {
        var v = num(arr[k]); if (v == null) { started = false; continue; }
        var px = pX(k), py = priceY(v);
        if (started) ctx.lineTo(px, py); else { ctx.moveTo(px, py); started = true; }
      }
      ctx.stroke();
    }
    poly(m20, MA20); poly(m5, MA5);

    // ---- 현재가 태그 ----
    var last = bars[n - 1], lastCol = last.up ? UP : DOWN, ly = priceY(last.c);
    ctx.strokeStyle = lastCol; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(L.padL, ly + .5); ctx.lineTo(L.padL + L.plotW, ly + .5); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = lastCol; ctx.fillRect(L.padL + L.plotW, ly - 8, L.padR - 2, 16);
    ctx.fillStyle = "#04110d"; ctx.textAlign = "left"; ctx.font = "bold 10px 'JetBrains Mono', monospace";
    ctx.fillText(fmtPrice(last.c), L.padL + L.plotW + 5, ly);
    ctx.font = "10px 'JetBrains Mono', monospace";

    // ---- RSI 패널 ----
    var rArr = (data.rsi || []);
    ctx.strokeStyle = "#1c2430"; ctx.lineWidth = 1;
    ctx.strokeRect(L.padL + .5, L.rsiTop + .5, L.plotW, L.rsiH);
    var rsiY = function (v) { return L.rsiTop + L.rsiH * (1 - v / 100); };
    [[70, "#3a2a1d"], [30, "#1d3a2a"]].forEach(function (g) {
      ctx.strokeStyle = g[1]; ctx.setLineDash([3, 3]); ctx.beginPath();
      ctx.moveTo(L.padL, rsiY(g[0]) + .5); ctx.lineTo(L.padL + L.plotW, rsiY(g[0]) + .5); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#3a4658"; ctx.textAlign = "left"; ctx.fillText(g[0], L.padL + L.plotW + 6, rsiY(g[0]));
    });
    ctx.strokeStyle = RSI; ctx.lineWidth = 1.4; ctx.beginPath();
    var rs = false;
    for (i = 0; i < n; i++) {
      var rv = num(rArr[i]); if (rv == null) { rs = false; continue; }
      var rx = pX(i), ry = rsiY(rv);
      if (rs) ctx.lineTo(rx, ry); else { ctx.moveTo(rx, ry); rs = true; }
    }
    ctx.stroke();

    // ---- 크로스헤어 + OHLC 툴팁 ----
    var mo = st.mouse;
    if (mo && mo.x >= L.padL && mo.x <= L.padL + L.plotW && mo.y >= L.priceTop && mo.y <= L.rsiTop + L.rsiH) {
      var idx = Math.round(((mo.x - L.padL) / L.plotW) * (n - 1));
      idx = Math.max(0, Math.min(n - 1, idx));
      var cx = pX(idx), b2 = bars[idx];
      ctx.strokeStyle = "#3a4658"; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(Math.round(cx) + .5, L.priceTop); ctx.lineTo(Math.round(cx) + .5, L.rsiTop + L.rsiH); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(L.padL, Math.round(mo.y) + .5); ctx.lineTo(L.padL + L.plotW, Math.round(mo.y) + .5); ctx.stroke();
      ctx.setLineDash([]);
      // 시간 태그(하단)
      ctx.fillStyle = "#1c2430"; ctx.fillRect(cx - 26, L.rsiTop + L.rsiH + 4, 52, 14);
      ctx.fillStyle = "#cdd5e0"; ctx.textAlign = "center";
      ctx.fillText(fmtTime(tsAt(idx), step), cx, L.rsiTop + L.rsiH + 11);
      // OHLC 박스(좌상단)
      var chg = b2.o ? (b2.c / b2.o - 1) * 100 : 0;
      var lines = ["O " + fmtPrice(b2.o), "H " + fmtPrice(b2.h), "L " + fmtPrice(b2.l),
        "C " + fmtPrice(b2.c), (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%"];
      var boxW = 96, boxH = lines.length * 14 + 8, bxX = L.padL + 6, bxY = L.priceTop + 6;
      if (mo.x < L.padL + 160) bxX = L.padL + L.plotW - boxW - 6;
      ctx.fillStyle = "rgba(13,18,25,.92)"; ctx.strokeStyle = "#2a3442";
      ctx.fillRect(bxX, bxY, boxW, boxH); ctx.strokeRect(bxX + .5, bxY + .5, boxW, boxH);
      ctx.textAlign = "left";
      for (i = 0; i < lines.length; i++) {
        ctx.fillStyle = i === 4 ? (chg >= 0 ? UP : DOWN) : "#cdd5e0";
        ctx.fillText(lines[i], bxX + 8, bxY + 12 + i * 14);
      }
    }
  }

  // ---- 기존 renderChart 오버라이드 ----
  var _resizeBound = false;
  window.renderChart = function (elId, _rsiUnused, data) {
    data = data || {};
    var closes = (data.closes || []).map(num).filter(function (v) { return v != null; });
    var cv = ensureCanvas(elId);
    if (cv && closes.length) {
      var seed = (data.ticker || data.code || elId) + ":" + (data.interval || "");
      cv.__hts = {
        data: data, bars: buildBars(data, seed),
        interval: data.interval || window._coinInterval || window._stockFrame, mouse: (cv.__hts && cv.__hts.mouse) || null
      };
      draw(cv);
      if (!_resizeBound) {
        _resizeBound = true;
        var t;
        window.addEventListener("resize", function () {
          clearTimeout(t);
          t = setTimeout(function () {
            ["coinSvg", "stockSvg"].forEach(function (id) {
              var c = document.getElementById(id);
              if (c && c.tagName.toLowerCase() === "canvas" && c.__hts) draw(c);
            });
          }, 120);
        });
      }
    }
    var ma5 = data.ma5 || [], ma20 = data.ma20 || [], rsi = data.rsi || [];
    return {
      lastClose: closes[closes.length - 1],
      lastRsi: num(rsi[rsi.length - 1]),
      lastMa5: num(ma5[ma5.length - 1]),
      lastMa20: num(ma20[ma20.length - 1]),
      changePct: closes[0] ? (closes[closes.length - 1] / closes[0] - 1) * 100 : 0
    };
  };
})();
