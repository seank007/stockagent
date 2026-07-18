import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../docs/coin-live.js", import.meta.url), "utf8");

function fixtureMarkets(count = 64) {
  const head = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA"];
  const symbols = head.concat(Array.from({ length: count - head.length }, (_, i) => `C${i + 1}`));
  return symbols.map((symbol) => ({
    market: `KRW-${symbol}`,
    symbol,
    korean_name: symbol,
    english_name: symbol,
  }));
}

async function loadHarness({ autoOpen = true } = {}) {
  let now = Date.parse("2026-07-18T05:00:00Z");
  class FakeDate extends Date {
    constructor(value) { super(value === undefined ? now : value); }
    static now() { return now; }
  }

  const markets = fixtureMarkets();
  const visibleCodes = ["KRW-BTC", "KRW-C1", "KRW-C2"];
  const priceNodes = new Map();
  const changeNodes = new Map();
  const cards = visibleCodes.map((ticker) => ({
    getAttribute(name) { return name === "data-ticker" ? ticker : null; },
    querySelector(selector) {
      const target = selector === ".coin-mini-price" ? priceNodes : changeNodes;
      if (!target.has(ticker)) target.set(ticker, { textContent: "—", className: "" });
      return target.get(ticker);
    },
  }));
  const nodes = {
    "coin-ticker": {},
    "coin-market-board-grid": {},
    upd: { textContent: "—" },
  };
  const document = {
    hidden: false,
    addEventListener() {},
    getElementById(id) { return nodes[id] || null; },
    querySelectorAll(selector) {
      return selector.includes("coin-mini-card") ? cards : [];
    },
  };

  const sent = [];
  const sockets = [];
  class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.closeCalled = false;
      sockets.push(this);
      if (autoOpen) {
        queueMicrotask(() => {
          this.readyState = FakeWebSocket.OPEN;
          this.onopen?.();
        });
      }
    }
    send(payload) { sent.push(JSON.parse(payload)); }
    close() {
      this.closeCalled = true;
      this.readyState = 3;
    }
  }

  const prices = Object.fromEntries(markets.map((m, i) => [m.market, 1000 + i]));
  const fetch = async (input) => {
    const url = String(input);
    if (url.includes("coin_markets.json")) {
      return new Response(JSON.stringify({
        generated_at: "2026-07-18 04:29",
        markets,
        prices,
        changes: {},
      }), { status: 200 });
    }
    if (url.includes("coin_candles_")) {
      return new Response(JSON.stringify({
        closes: Object.fromEntries(markets.map((m) => [m.market, [1, 2, 3]])),
      }), { status: 200 });
    }
    throw new Error(`unexpected fetch: ${url}`);
  };

  const window = {
    _coinTicker: "KRW-BTC",
    _coinSection: "market",
    location: { pathname: "/stockagent/coin/" },
    fetch,
    MutationObserver: class { observe() {} },
  };
  window.window = window;

  const context = vm.createContext({
    AbortController,
    Date: FakeDate,
    document,
    encodeURIComponent,
    MutationObserver: window.MutationObserver,
    Promise,
    Response,
    setInterval() { return 1; },
    setTimeout(callback, delay) {
      const timer = setTimeout(callback, delay);
      if (delay >= 10_000) timer.unref();
      return timer;
    },
    clearTimeout,
    TextDecoder,
    URL,
    WebSocket: FakeWebSocket,
    window,
  });
  vm.runInContext(source, context, { filename: "coin-live.js" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  return {
    advance(ms) { now += ms; },
    live: window.__coinLive,
    nodes,
    sent,
    sockets,
    window,
  };
}

test("subscribes only selected and visible markets while keeping the full catalog", async () => {
  const h = await loadHarness();
  assert.ok(h.sent.length >= 1);
  const tickerRequest = h.sent.at(-1).find((row) => row.type === "ticker");
  assert.ok(tickerRequest.codes.includes("KRW-BTC"));
  assert.ok(tickerRequest.codes.includes("KRW-C1"));
  assert.ok(tickerRequest.codes.includes("KRW-C2"));
  assert.ok(!tickerRequest.codes.includes("KRW-C58"));
  assert.ok(tickerRequest.codes.length < 64);

  h.window._coinTicker = "KRW-C58";
  h.live.syncSubscriptions();
  await new Promise((resolve) => setTimeout(resolve, 280));
  const changed = h.sent.at(-1).find((row) => row.type === "ticker");
  assert.ok(changed.codes.includes("KRW-C58"));

  h.live.setOrderbookCode("KRW-C58");
  await new Promise((resolve) => setTimeout(resolve, 280));
  const orderbook = h.sent.at(-1).find((row) => row.type === "orderbook");
  assert.deepEqual(orderbook.codes, ["KRW-C58"]);
});

test("marks genuine websocket data live and detects a dead connection", async () => {
  const h = await loadHarness();
  const socket = h.sockets[0];
  socket.onmessage({ data: JSON.stringify({
    type: "ticker",
    code: "KRW-BTC",
    trade_price: 94_500_000,
    signed_change_rate: 0.01,
    timestamp: Date.now(),
  }) });
  h.live.refreshStatus();
  assert.equal(h.live.prices["KRW-BTC"].price, 94_500_000);
  assert.equal(h.live.getStatus().fresh, true);
  assert.match(h.nodes.upd.textContent, /^MARKET LIVE /);

  h.advance(31_000);
  h.live.refreshStatus();
  assert.equal(h.live.getStatus().fresh, false);
  assert.match(h.nodes.upd.textContent, /^MARKET STALE /);
  h.live.checkHealth();
  assert.equal(socket.closeCalled, true);
});

test("closes the active socket when Upbit returns an error packet", async () => {
  const h = await loadHarness();
  const socket = h.sockets[0];
  socket.onmessage({ data: JSON.stringify({ error: { name: "WRONG_FORMAT" } }) });
  assert.equal(socket.closeCalled, true);
});

test("does not bypass the minimum reconnect interval through ensureWs", async () => {
  const h = await loadHarness();
  const socket = h.sockets[0];
  socket.onclose();

  h.advance(2_500);
  h.live.ensureWs();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(h.sockets.length, 1);

  h.advance(8_501);
  h.live.ensureWs();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(h.sockets.length, 2);
});

test("recovers when the websocket handshake stays connecting", async () => {
  const h = await loadHarness({ autoOpen: false });
  const socket = h.sockets[0];
  assert.equal(socket.readyState, 0);

  h.advance(31_000);
  h.live.checkHealth();
  assert.equal(socket.closeCalled, true);

  h.live.ensureWs();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(h.sockets.length, 1);
  assert.equal(h.live.getStatus().state, "reconnecting");

  h.advance(11_001);
  h.live.ensureWs();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(h.sockets.length, 2);
});
