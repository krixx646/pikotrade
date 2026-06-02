const instrumentSelect = document.querySelector("#instrument");
const granularitySelect = document.querySelector("#granularity");
const refreshSelect = document.querySelector("#refreshSeconds");
const themeSelect = document.querySelector("#theme");
const zoneModeSelect = document.querySelector("#zoneMode");
const refreshButton = document.querySelector("#refreshButton");
const overlayLayer = document.querySelector("#overlayLayer");
const chartElement = document.querySelector("#chart");

const fields = {
  bias: document.querySelector("#bias"),
  status: document.querySelector("#status"),
  latestPrice: document.querySelector("#latestPrice"),
  nextCheck: document.querySelector("#nextCheck"),
  freshness: document.querySelector("#freshness"),
  action: document.querySelector("#action"),
  signals: document.querySelector("#signals"),
};

const themes = {
  light: {
    background: "#ffffff",
    text: "#131722",
    grid: "rgba(42, 46, 57, 0.08)",
    border: "#d1d4dc",
    up: "#089981",
    down: "#24272d",
    wickDown: "#24272d",
  },
  dark: {
    background: "#0b1119",
    text: "#c8d3df",
    grid: "rgba(64, 83, 103, 0.35)",
    border: "#263849",
    up: "#00a88f",
    down: "#1f252d",
    wickDown: "#818b98",
  },
};

let currentTheme = localStorage.getItem("liveChartTheme") || "light";

const chart = LightweightCharts.createChart(chartElement, {
  layout: {
    background: { color: themes[currentTheme].background },
    textColor: themes[currentTheme].text,
    attributionLogo: false,
  },
  grid: {
    vertLines: { color: themes[currentTheme].grid },
    horzLines: { color: themes[currentTheme].grid },
  },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  rightPriceScale: { borderColor: themes[currentTheme].border },
  timeScale: { borderColor: themes[currentTheme].border, timeVisible: true, secondsVisible: false },
});

const candleSeries = chart.addCandlestickSeries({
  upColor: themes[currentTheme].up,
  downColor: themes[currentTheme].down,
  borderUpColor: themes[currentTheme].up,
  borderDownColor: themes[currentTheme].down,
  wickUpColor: themes[currentTheme].up,
  wickDownColor: themes[currentTheme].wickDown,
});

let overlays = { zones: [], price_lines: [], state: {} };
let refreshTimer = null;

async function init() {
  themeSelect.value = currentTheme;
  applyTheme(currentTheme);
  const response = await fetch("/api/instruments");
  const payload = await response.json();
  instrumentSelect.innerHTML = payload.instruments
    .map((instrument) => `<option value="${instrument}">${instrument}</option>`)
    .join("");
  const savedInstrument = localStorage.getItem("liveChartInstrument");
  instrumentSelect.value = payload.instruments.includes(savedInstrument)
    ? savedInstrument
    : payload.instruments.includes("XAU_USD")
      ? "XAU_USD"
      : payload.instruments[0];
  granularitySelect.value = localStorage.getItem("liveChartGranularity") || granularitySelect.value;
  zoneModeSelect.value = localStorage.getItem("liveChartZoneMode") || zoneModeSelect.value;
  await loadChart();
  scheduleRefresh();
}

async function loadChart() {
  const instrument = instrumentSelect.value;
  const granularity = granularitySelect.value;
  const zoneMode = zoneModeSelect.value;
  localStorage.setItem("liveChartInstrument", instrument);
  localStorage.setItem("liveChartGranularity", granularity);
  localStorage.setItem("liveChartZoneMode", zoneMode);
  fields.action.textContent = "Loading candles and overlays...";

  const [candlesResponse, overlaysResponse] = await Promise.all([
    fetch(`/api/candles?instrument=${encodeURIComponent(instrument)}&granularity=${granularity}&count=420`),
    fetch(`/api/overlays?instrument=${encodeURIComponent(instrument)}&mode=${encodeURIComponent(zoneMode)}`),
  ]);

  const candlePayload = await candlesResponse.json();
  const overlayPayload = await overlaysResponse.json();
  if (candlePayload.error) {
    throw new Error(candlePayload.error);
  }
  if (overlayPayload.error) {
    throw new Error(overlayPayload.error);
  }

  const candles = candlePayload.candles.map((candle) => ({
    time: candle.time,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  }));
  candleSeries.setData(candles);
  overlays = overlayPayload;
  updateStatus(candlePayload, overlayPayload);
  drawOverlays();
  focusActiveStory(candles, overlayPayload);
}

function updateStatus(candlePayload, overlayPayload) {
  const latest = candlePayload.candles[candlePayload.candles.length - 1];
  const state = overlayPayload.state || {};
  fields.bias.textContent = state.bias || "-";
  fields.status.textContent = state.status || "-";
  fields.latestPrice.textContent = latest ? latest.close.toFixed(pricePrecision(latest.close)) : "-";
  fields.nextCheck.textContent = state.next_check_time || "-";
  fields.freshness.textContent = state.freshness || "-";
  fields.freshness.className = state.is_stale ? "stale" : "fresh";
  fields.action.textContent = state.action || "No current agent note.";
  renderSignals(overlayPayload);
}

function drawOverlays() {
  overlayLayer.replaceChildren();

  for (const zone of overlays.zones || []) {
    drawZone(zone);
  }
  for (const line of overlays.price_lines || []) {
    drawPriceLine(line);
  }
  for (const marker of overlays.markers || []) {
    drawSequenceMarker(marker);
  }
}

function drawZone(zone) {
  const top = candleSeries.priceToCoordinate(zone.high);
  const bottom = candleSeries.priceToCoordinate(zone.low);
  if (top === null || bottom === null) {
    return;
  }

  const element = document.createElement("div");
  const sideClass = zone.side === "SELL" || zone.side === "SUPPLY" ? "supply" : "demand";
  element.className = `zone ${sideClass} ${zone.kind || ""}`.trim();
  const x = zone.start_time ? chart.timeScale().timeToCoordinate(zone.start_time) : null;
  if (x === null || x > chartElement.clientWidth - 120) {
    return;
  }
  const left = Math.max(0, x);
  const rightPadding = 96;
  const maxWidth = chartElement.clientWidth - left - rightPadding;
  const width = Math.min(Math.max(280, maxWidth * 0.55), Math.max(80, maxWidth));
  element.style.top = `${Math.min(top, bottom)}px`;
  element.style.height = `${Math.max(4, Math.abs(bottom - top))}px`;
  element.style.left = `${left}px`;
  element.style.width = `${width}px`;
  element.title = zone.note || zone.label || "";

  const label = document.createElement("div");
  label.className = "zone-label";
  label.textContent = `${x < 0 ? "Earlier " : ""}${cleanZoneLabel(zone)}`;
  element.appendChild(label);
  if (zone.kind === "entry" || zone.kind === "ai") {
    element.appendChild(entryCallout(zone));
  }
  overlayLayer.appendChild(element);
}

function cleanZoneLabel(zone) {
  if (zone.kind === "entry" || zone.kind === "ai") {
    return `${zone.label || `${zone.side} entry`} ${formatPrice(zone.low)}-${formatPrice(zone.high)}`;
  }
  return `${zone.route || "Agent"} ${zone.label || "zone"} ${formatPrice(zone.low)}-${formatPrice(zone.high)}`;
}

function drawSequenceMarker(marker) {
  const x = chart.timeScale().timeToCoordinate(marker.time);
  const y = candleSeries.priceToCoordinate(marker.price);
  if (x === null || y === null) {
    return;
  }

  const element = document.createElement("div");
  element.className = `sequence-marker ${marker.kind} ${marker.side === "SELL" ? "sell" : "buy"}`;
  element.style.left = `${x}px`;
  element.style.top = `${y}px`;
  element.title = marker.note || marker.label || "";
  element.innerHTML = `
    <div class="marker-dot"></div>
    <div class="marker-label">
      <strong>${marker.label}</strong>
      <span>${formatPrice(marker.price)}</span>
    </div>
  `;
  overlayLayer.appendChild(element);
}

function entryCallout(zone) {
  const callout = document.createElement("div");
  callout.className = `entry-callout ${zone.side === "SELL" ? "sell" : "buy"}`;
  callout.innerHTML = `
    <div class="entry-arrow">${zone.side === "SELL" ? "↓" : "↑"}</div>
    <div>
      <strong>${zone.label || `${zone.side} entry`}</strong>
      <small>${formatPrice(zone.low)}-${formatPrice(zone.high)}</small>
      <span>${zone.note || "Agent marked this as the active entry area."}</span>
    </div>
  `;
  return callout;
}

function renderSignals(overlayPayload) {
  const zones = overlayPayload.zones || [];
  const entryZones = zones.filter((zone) => zone.kind === "entry" || zone.kind === "ai");
  const contextZones = zones.filter((zone) => zone.kind === "zone");
  const state = overlayPayload.state || {};
  const narrative = state.htf_narrative || {};
  const storyHtml = narrative.summary
    ? `<div class="signal context"><strong>HTF story</strong><p>${narrative.summary}</p></div>`
    : "";
  const contextHtml = contextZones.length
    ? `<div class="signal context"><strong>Rule context zones</strong>${contextZones
        .map((zone) => `<p><b>${cleanZoneLabel(zone)}</b>: ${zone.note || "Context zone inside the active HTF story."}</p>`)
        .join("")}</div>`
    : "";
  const aiRoutes = state.ai_routes || [];
  const aiHtml = aiRoutes.length
    ? `<div class="signal ai"><strong>AI route decisions</strong>${aiRoutes
        .map((route) => `<p><b>${route.route}</b>: ${route.status || "NO_DATA"} ${route.side || ""}${route.entry_zone ? ` at ${route.entry_zone}` : ""}${route.is_stale ? " (stale)" : ""}. ${route.note || "No chart zone produced."}</p>`)
        .join("")}</div>`
    : "";
  const warning = overlayPayload.state?.reversal_warning;
  const warningHtml = warning
    ? `<div class="signal warning"><strong>Opposite market shift warning</strong><p>${warning.message || "Possible trend-control change detected. This is a warning, not an entry."}</p></div>`
    : "";
  if (!entryZones.length) {
    fields.signals.innerHTML = `${storyHtml}${warningHtml}${contextHtml}${aiHtml}<div class="signal empty">No active fresh entry zone for this pair right now. If the status is stale/expired, wait for the agent to confirm fresh sweep + market shift.</div>`;
    return;
  }
  fields.signals.innerHTML = storyHtml + warningHtml + entryZones
    .map((zone) => `
      <div class="signal ${zone.side === "SELL" ? "sell" : "buy"}">
        <strong>${zone.label}</strong>
        <span>${formatPrice(zone.low)}-${formatPrice(zone.high)}</span>
        <p>${zone.note || "Fresh agent entry area."}</p>
      </div>
    `)
    .join("") + contextHtml + aiHtml;
}

function focusActiveStory(candles, overlayPayload) {
  const activeFromIso = overlayPayload.state?.htf_narrative?.active_from_time;
  const latest = candles[candles.length - 1];
  if (!activeFromIso || !latest) {
    chart.timeScale().fitContent();
    return;
  }
  const from = Math.floor(Date.parse(activeFromIso) / 1000);
  if (!Number.isFinite(from)) {
    chart.timeScale().fitContent();
    return;
  }
  chart.timeScale().setVisibleRange({ from, to: latest.time });
}

function drawPriceLine(line) {
  const y = candleSeries.priceToCoordinate(line.price);
  if (y === null) {
    return;
  }
  const element = document.createElement("div");
  element.className = `price-line ${line.kind || ""}`.trim();
  element.style.top = `${y}px`;
  const label = document.createElement("span");
  label.textContent = line.label || formatPrice(line.price);
  element.appendChild(label);
  overlayLayer.appendChild(element);
}

function scheduleRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
  }
  refreshTimer = setInterval(() => {
    loadChart().catch(showError);
  }, Number(refreshSelect.value) * 1000);
}

function applyTheme(themeName) {
  currentTheme = themes[themeName] ? themeName : "light";
  const theme = themes[currentTheme];
  document.body.dataset.theme = currentTheme;
  localStorage.setItem("liveChartTheme", currentTheme);
  chart.applyOptions({
    layout: {
      background: { color: theme.background },
      textColor: theme.text,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: theme.grid },
      horzLines: { color: theme.grid },
    },
    rightPriceScale: { borderColor: theme.border },
    timeScale: { borderColor: theme.border },
  });
  candleSeries.applyOptions({
    upColor: theme.up,
    downColor: theme.down,
    borderUpColor: theme.up,
    borderDownColor: theme.down,
    wickUpColor: theme.up,
    wickDownColor: theme.wickDown,
  });
  drawOverlays();
}

function showError(error) {
  fields.action.textContent = `Chart load failed: ${error.message}`;
}

function formatPrice(value) {
  return Number(value).toFixed(pricePrecision(Number(value)));
}

function pricePrecision(value) {
  return value > 20 ? 3 : 5;
}

instrumentSelect.addEventListener("change", () => loadChart().catch(showError));
granularitySelect.addEventListener("change", () => loadChart().catch(showError));
refreshSelect.addEventListener("change", scheduleRefresh);
themeSelect.addEventListener("change", () => applyTheme(themeSelect.value));
zoneModeSelect.addEventListener("change", () => loadChart().catch(showError));
refreshButton.addEventListener("click", () => loadChart().catch(showError));
chart.timeScale().subscribeVisibleTimeRangeChange(drawOverlays);
new ResizeObserver(() => {
  chart.applyOptions({
    width: chartElement.clientWidth,
    height: chartElement.clientHeight,
  });
  drawOverlays();
}).observe(chartElement);

init().catch(showError);
