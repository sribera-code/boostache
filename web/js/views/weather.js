// Vue Météo : prévision sur 14 jours en courbes défilantes (d'après weatherline ; données : weather.py).
// Quatre graphiques (température, luminosité, pluie et humidité, vent) partagent la même échelle
// horizontale : ils défilent ensemble, et survoler l'un lit la même heure sur tous.
import { h, debounce, isTyping } from "../dom.js";
import { ico, iconButton, toast, openPopover } from "../ui.js";

const LEFT_AXIS = 42;
const RIGHT_AXIS = 36;
const SPANS = [[1, "24 h"], [3, "3 j"], [7, "7 j"]];
const STALE_MS = 30 * 60 * 1000;         // prévision rechargée au-delà
const PARIS = { name: "Paris", detail: "Île-de-France, France", lat: 48.8566, lon: 2.3522 };
const DAY_NAMES = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
const FONT = '"Inter", "Segoe UI", sans-serif';

const COLORS = {
  sun: "#fbbf24", cloud: "#94a3b8", rain: "#60a5fa", rainProb: "#a78bfa",
  wind: "#2dd4bf", gust: "#f472b6", band: "#fb923c",
};

// ─────────────────────────────────────────────
//  Couleurs et formats
// ─────────────────────────────────────────────
const hex = (s) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
const rgba = (c, a = 1) => {
  const [r, g, b] = typeof c === "string" ? hex(c) : c;
  return `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${a})`;
};
const WHITE = [255, 255, 255];
/** Couleur CSS : [r, g, b] converti, chaîne gardée telle quelle. */
const css = (c) => (typeof c === "string" ? c : rgba(c));

/** Échelle de couleur continue de la température. */
const TEMP_STOPS = [[-10, "#a78bfa"], [0, "#60a5fa"], [8, "#22d3ee"], [15, "#4ade80"],
  [21, "#facc15"], [28, "#fb923c"], [36, "#ef4444"]].map(([t, c]) => [t, hex(c)]);
function tempColor(t) {
  if (t == null || !Number.isFinite(t)) return WHITE;
  if (t <= TEMP_STOPS[0][0]) return TEMP_STOPS[0][1];
  for (let i = 1; i < TEMP_STOPS.length; i++) {
    const [tb, cb] = TEMP_STOPS[i];
    if (t <= tb) {
      const [ta, ca] = TEMP_STOPS[i - 1];
      const k = (t - ta) / (tb - ta);
      return ca.map((v, j) => v + (cb[j] - v) * k);
    }
  }
  return TEMP_STOPS[TEMP_STOPS.length - 1][1];
}

const ok = (v) => v != null && Number.isFinite(v);
const fmt0 = (v) => (ok(v) ? String(Math.round(v) || 0) : "–");
const fmt1 = (v) => (ok(v) ? String(Math.round(v * 10) / 10).replace(".", ",") : "–");
const CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];
const cardinal = (deg) => (ok(deg) ? CARDINALS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16] : "");

/** "2026-11-11T14:00" (heure locale du lieu) → millisecondes, lue comme UTC. */
function stamp(s) {
  const [d, t = "00:00"] = String(s).split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [hh, mm] = t.split(":").map(Number);
  return Date.UTC(y, m - 1, day, hh || 0, mm || 0);
}
const dayLabel = (ms) => { const d = new Date(ms); return `${DAY_NAMES[d.getUTCDay()]} ${d.getUTCDate()}`; };
const clockText = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
};

// ─────────────────────────────────────────────
//  Échelles
// ─────────────────────────────────────────────
/** Plage « ronde » englobant [lo, hi] avec environ count graduations. */
function niceRange(lo, hi, count = 4) {
  if (!ok(lo) || !ok(hi)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-6) hi = lo + 1;
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const min = Math.floor(lo / step) * step;
  const max = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let t = min; t <= max + step * 1e-6; t += step) ticks.push(t);
  const decimals = step >= 1 && step % 1 === 0 ? 0 : step >= 0.1 ? 1 : 2;
  return { min, max, ticks, label: (v) => v.toFixed(decimals).replace(".", ",") };
}
const PERCENT = { min: 0, max: 100, ticks: [0, 25, 50, 75, 100], label: (v) => String(v) };

function extent(...lists) {
  let lo = Infinity, hi = -Infinity;
  for (const list of lists) for (const v of list || []) if (ok(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  return [lo, hi];
}

const niceStep = (minHours) => [1, 2, 3, 4, 6, 12, 24].find((s) => s >= minHours) ?? 24;

// ─────────────────────────────────────────────
//  Courbes monotones (Fritsch–Carlson) : lisses, sans dépassement
//  (pas d'humidité > 100 % ni de pluie négative). Calculées en (indice, valeur) :
//  la courbe ne dépend pas de l'échelle.
// ─────────────────────────────────────────────
/** Suites de valeurs valides : [{ start, end (inclus), t: pentes }]. */
function segments(values, valid = (i) => ok(values[i])) {
  const out = [];
  let start = -1;
  for (let i = 0; i <= values.length; i++) {
    const isValid = i < values.length && valid(i);
    if (isValid && start < 0) start = i;
    if (!isValid && start >= 0) { out.push(tangents(values, start, i - 1)); start = -1; }
  }
  return out;
}

function tangents(v, start, end) {
  const n = end - start + 1;
  const t = new Float64Array(n);
  if (n < 2) return { start, end, t };
  const m = Array.from({ length: n - 1 }, (_, i) => v[start + i + 1] - v[start + i]);
  t[0] = m[0];
  t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i];
    const s = a * a + b * b;
    if (s > 9) {
      const tau = 3 / Math.sqrt(s);
      t[i] = tau * a * m[i];
      t[i + 1] = tau * b * m[i];
    }
  }
  return { start, end, t };
}

/** Trace un segment (X, Y : indice, valeur → pixels), à l'endroit ou à l'envers. */
function trace(ctx, seg, values, X, Y, { reverse = false, move = true } = {}) {
  const { start, end, t } = seg;
  const first = reverse ? end : start;
  if (move) ctx.moveTo(X(first), Y(values[first]));
  else ctx.lineTo(X(first), Y(values[first]));
  if (start === end) { ctx.lineTo(X(first) + 0.01, Y(values[first])); return; }
  const k = 1 / 3;
  if (!reverse) {
    for (let i = start; i < end; i++) {
      const a = t[i - start], b = t[i + 1 - start];
      ctx.bezierCurveTo(X(i + k), Y(values[i] + a * k), X(i + 1 - k), Y(values[i + 1] - b * k),
        X(i + 1), Y(values[i + 1]));
    }
  } else {
    for (let i = end; i > start; i--) {
      const a = t[i - start], b = t[i - 1 - start];
      ctx.bezierCurveTo(X(i - k), Y(values[i] - a * k), X(i - 1 + k), Y(values[i - 1] + b * k),
        X(i - 1), Y(values[i - 1]));
    }
  }
}

/** Valeur de la courbe à l'indice fractionnaire x ; null hors d'un segment. */
function curveAt(values, segs, x) {
  const k = Math.floor(x);
  const seg = segs.find((s) => k >= s.start && k < s.end);
  if (!seg) return ok(values[k]) && x === k ? values[k] : null;
  const u = x - k, u2 = u * u, u3 = u2 * u;
  const ta = seg.t[k - seg.start], tb = seg.t[k + 1 - seg.start];
  return (2 * u3 - 3 * u2 + 1) * values[k] + (u3 - 2 * u2 + u) * ta
    + (3 * u2 - 2 * u3) * values[k + 1] + (u3 - u2) * tb;
}

// ─────────────────────────────────────────────
//  Prévision et graphiques
// ─────────────────────────────────────────────
function prepare(f) {
  const n = f.times.length;
  const t0 = stamp(f.times[0]);
  return {
    ...f, n, t0,
    hours: f.times.map((s) => Number(s.slice(11, 13))),
    stamps: f.times.map(stamp),
    /** Position fractionnaire de « maintenant » dans les tableaux horaires. */
    nowIndex: () => (Date.now() + f.utc_offset * 1000 - t0) / 3600000,
  };
}

/** Pour chaque jour : plage d'heures et heures du minimum et du maximum. */
function dailyExtremes(f, values) {
  const out = [];
  for (const d of f.days) {
    const end = Math.min(d.start + 24, f.n);
    let lo = -1, hi = -1;
    for (let i = d.start; i < end; i++) {
      if (!ok(values[i])) continue;
      if (lo < 0 || values[i] < values[lo]) lo = i;
      if (hi < 0 || values[i] > values[hi]) hi = i;
    }
    if (lo >= 0) out.push({ from: d.start, to: end, min: lo, max: hi });
  }
  return out;
}

function series(values, color, opts = {}) {
  return { values, color, kind: "line", right: false, width: 2.4, fill: 0.38, shift: 0, colorFn: null,
    ...opts, segs: segments(values) };
}

function buildCharts(f) {
  const hasBand = !!(f.temp_low && f.temp_high);
  const [tLo, tHi] = extent(f.temperature, f.apparent, hasBand && f.temp_low, hasBand && f.temp_high);
  const margin = Math.max(0.5, (tHi - tLo) * 0.1);   // place du max au-dessus du pic, du min sous le creux
  const tempDays = dailyExtremes(f, f.temperature);
  const both = (i) => ok(f.temp_low[i]) && ok(f.temp_high[i]);
  const band = hasBand ? { lower: f.temp_low, upper: f.temp_high, alpha: 0.3, colorFn: tempColor,
    low: segments(f.temp_low, both), up: segments(f.temp_high, both) } : null;
  const [, rainMax] = extent(f.precipitation);
  const [, windMax] = extent(f.wind, f.gusts);

  return [
    {
      key: "temp", title: "Température", icon: "thermometer", color: COLORS.band, height: 180,
      left: niceRange(tLo - margin, tHi + margin), leftUnit: "°C",
      levels: tempDays.flatMap((d) => [[d.max, true], [d.min, false]].map(([at, above]) => ({
        from: d.from, to: d.to, at, value: f.temperature[at], label: `${fmt0(f.temperature[at])}°`,
        color: tempColor(f.temperature[at]), above }))),
      zones: tempDays.map((d) => ({ from: d.from, to: d.to, low: f.temperature[d.min], high: f.temperature[d.max] })),
      band,
      series: [
        series(f.apparent, rgba(WHITE, 0.54), { kind: "dashed", width: 2 }),
        series(f.temperature, "#ffffff", { width: 3, colorFn: tempColor }),
      ],
      readouts: [
        { kind: "line", color: (i) => tempColor(f.temperature[i]), text: (i) => `${fmt1(f.temperature[i])} °C` },
        { kind: "dashed", color: () => rgba(WHITE, 0.54), text: (i) => `ressenti ${fmt0(f.apparent[i])}°` },
        ...(hasBand ? [{ kind: "band", color: (i) => tempColor(f.temperature[i]),
          text: (i) => (ok(f.temp_low[i]) ? `fourchette ${fmt0(f.temp_low[i])} à ${fmt0(f.temp_high[i])}°` : "fourchette –") }] : []),
      ],
    },
    {
      key: "light", title: "Luminosité", icon: "sun", color: COLORS.sun, height: 150,
      left: PERCENT, leftUnit: "%",
      series: [
        series(f.cloud_cover, COLORS.cloud, { kind: "area", width: 1.2, fill: 0.16 }),
        series(f.brightness, COLORS.sun, { kind: "area" }),
      ],
      readouts: [
        { kind: "area", color: () => COLORS.sun, text: (i) => `${fmt0(f.brightness[i])} % du plein soleil` },
        { kind: "area", color: () => COLORS.cloud, text: (i) => `nuages ${fmt0(f.cloud_cover[i])} %` },
      ],
    },
    {
      // Pluie et risque datés de l'heure écoulée : dessinés au milieu de cette heure
      key: "rain", title: "Pluie et humidité", icon: "umbrella", color: COLORS.rain, height: 160,
      left: niceRange(0, Math.max(1, (ok(rainMax) ? rainMax : 0) * 1.1), 4), leftUnit: "mm",
      right: PERCENT, rightUnit: "%",
      series: [
        series(f.precipitation, COLORS.rain, { kind: "area", shift: -0.5 }),
        series(f.humidity, rgba(WHITE, 0.6), { right: true, width: 1.4 }),
        series(f.precip_prob, COLORS.rainProb, { right: true, width: 1.8, shift: -0.5 }),
      ],
      readouts: [
        { kind: "area", color: () => COLORS.rain, text: (i) => `${fmt1(f.precipitation[i])} mm` },
        { kind: "line", color: () => COLORS.rainProb, text: (i) => `risque ${fmt0(f.precip_prob[i])} %` },
        { kind: "line", color: () => rgba(WHITE, 0.6), text: (i) => `humidité ${fmt0(f.humidity[i])} %` },
      ],
    },
    {
      // Échelle fixe sur les 14 jours, rafales comprises (au moins 20 km/h : un vent faible ne paraît pas fort)
      key: "wind", title: "Vent", icon: "wind", color: COLORS.wind, height: 170,
      left: niceRange(0, Math.max(20, ok(windMax) ? windMax : 0)), leftUnit: "km/h",
      arrows: f.wind_dir,
      series: [
        series(f.gusts, COLORS.gust, { kind: "dashed" }),
        series(f.wind, COLORS.wind, { kind: "area" }),
      ],
      readouts: [
        { kind: "area", color: () => COLORS.wind, text: (i) => `${fmt0(f.wind[i])} km/h ${cardinal(f.wind_dir[i])}`.trim() },
        { kind: "dashed", color: () => COLORS.gust, text: (i) => `rafales ${fmt0(f.gusts[i])} km/h` },
      ],
    },
  ];
}

// ─────────────────────────────────────────────
//  Vue
// ─────────────────────────────────────────────
export function createWeatherView(ctx, state) {
  const { api, store } = ctx;
  const boot = state.weather || {};
  let place = boot.place ?? null;          // lieu fixe, ou null : position actuelle
  let data = null;                         // { forecast, place, here, lat, lon, updated }
  let f = null;                            // prévision préparée
  let charts = [];
  let span = [1, 3, 7].includes(store.settings.weather_span) ? store.settings.weather_span : 3;
  let hw = 0;                              // largeur d'une heure (px)
  let vw = 0;                              // largeur visible des graphiques (px)
  let offset = 0;                          // défilement horizontal commun (px)
  let needsInitialScroll = true;
  let selection = null;                    // heure survolée
  let loadGen = 0;
  let loading = false;
  let error = "";
  let status = "";
  let anim = 0;

  // ── En-tête ──
  const placeName = h("span", { class: "wx-place-name" });
  const placeIcon = h("span", { class: "wx-place-icon" });
  const placeBtn = h("button", { class: "btn ghost sm wx-place-btn", type: "button",
    dataset: { tip: "Choisir le lieu" }, onClick: () => openPlaces() },
    placeIcon, placeName, ico("chevron-down", 13));
  const updated = h("span", { class: "view-sub wx-updated" });
  const spanSeg = h("div", { class: "segmented wx-span" }, ...SPANS.map(([value, label]) => h("button", {
    type: "button", text: label, dataset: { span: String(value), tip: value === 1 ? "Une journée à l'écran" : `${value} jours à l'écran` },
    onClick: () => setSpan(value),
  })));
  const nowBtn = iconButton("calendar-days", "Revenir à maintenant", () => scrollToNow(true), { size: 15, cls: "sm", kbd: "Origine" });
  const refreshBtn = iconButton("refresh-cw", "Actualiser", () => load(), { size: 15, cls: "sm" });
  const head = h("header", { class: "view-head" },
    h("span", { class: "view-title", text: "Météo" }), placeBtn, updated,
    h("span", { class: "head-spacer" }), spanSeg, nowBtn, refreshBtn);

  // ── Corps ──
  const progress = h("div", { class: "wx-progress", hidden: true });
  const days = h("div", { class: "wx-days", role: "toolbar", "aria-label": "Jours" });
  const cards = h("div", { class: "wx-cards" });
  const foot = h("div", { class: "wx-foot" });
  const content = h("div", { class: "wx-inner" }, days, cards, foot);
  const placeholder = h("div", { class: "wx-placeholder" });
  const body = h("div", { class: "wx-body" }, content, placeholder);
  const el = h("section", { class: "wx", tabindex: "-1", onKeydown: onKey }, head, progress, body);

  // ─────────────────────────────────────────
  //  Chargement
  // ─────────────────────────────────────────
  async function load() {
    const gen = ++loadGen;
    error = "";
    setLoading(true, place ? "Chargement des prévisions…" : "Localisation en cours…");
    let target = place ? { lat: place.lat, lon: place.lon, name: place.name, here: false } : null;
    if (!target) {
      const loc = await api.weather_locate();
      if (gen !== loadGen) return;
      if (!loc || loc.error) { fail(loc?.error || "Impossible d'obtenir la position."); return; }
      target = { ...loc, here: true };
      setLoading(true, "Chargement des prévisions…");
    }
    const res = await api.weather_forecast(target.lat, target.lon, target.name, target.here);
    if (gen !== loadGen) return;
    if (!res || res.error) {
      if (res?.stale && !data) setData(res.stale);
      fail(res?.error || "Impossible de charger la météo.");
      return;
    }
    setLoading(false);
    setData(res);
  }

  function fail(message) {
    setLoading(false);
    if (data) { toast(message.split("\n")[0], "error", 5000); return; }
    error = message;
    render();
  }

  function setLoading(on, text = "") {
    loading = on;
    status = text;
    progress.hidden = !on || !data;
    refreshBtn.disabled = on;
    if (!data) render();
  }

  function setData(res) {
    data = res;
    f = prepare(res.forecast);
    charts = buildCharts(f);
    needsInitialScroll = true;
    selection = null;
    render();
  }

  const stale = () => !data || Date.now() - new Date(data.updated).getTime() > STALE_MS;

  /** Dernière prévision gardée, si elle est celle du lieu choisi. */
  function fromCache(cached) {
    if (!cached?.forecast) return false;
    if (place ? Math.abs(cached.lat - place.lat) > 0.05 || Math.abs(cached.lon - place.lon) > 0.05 : !cached.here) return false;
    try { setData(cached); return true; } catch (err) { console.error(err); return false; }
  }

  // ─────────────────────────────────────────
  //  Rendu
  // ─────────────────────────────────────────
  function renderHead() {
    const here = !place;
    placeIcon.replaceChildren(ico(here ? "locate-fixed" : "map-pin", 14));
    placeName.textContent = here ? (data?.here ? data.place : "Position actuelle") : place.name;
    placeBtn.dataset.tip = here ? "Position actuelle — choisir un autre lieu" : `${place.name}${place.detail ? `, ${place.detail}` : ""} — choisir un autre lieu`;
    updated.textContent = data ? `mis à jour à ${clockText(data.updated)}` : "";
    for (const b of spanSeg.children) b.classList.toggle("active", Number(b.dataset.span) === span);
    nowBtn.disabled = !f;
    for (const b of spanSeg.children) b.disabled = !f;
  }

  function render() {
    renderHead();
    content.hidden = !f;
    placeholder.hidden = !!f;
    if (!f) {
      cards.replaceChildren();
      placeholder.replaceChildren(loading ? h("div", { class: "wx-loading" }, h("div", { class: "wx-bar" }), h("p", { text: status }))
        : h("div", { class: "empty" },
          h("div", { class: "glyph" }, ico("cloud-sun", 22)),
          h("h3", { text: error ? "Météo indisponible" : "Météo" }),
          h("p", { class: "wx-error", text: error || "Prévision sur 14 jours pour votre position ou la ville de votre choix." }),
          h("div", { class: "empty-actions" },
            h("button", { class: "btn primary", type: "button", onClick: () => load() }, ico("refresh-cw", 15), "Réessayer"),
            h("button", { class: "btn", type: "button", onClick: () => openPlaces() }, ico("search", 15), "Choisir une ville"),
            place?.name === PARIS.name ? null : h("button", { class: "btn ghost", type: "button", onClick: () => choose(PARIS) },
              "Voir la météo de Paris"))));
      return;
    }
    renderDays();
    cards.replaceChildren(...charts.map(buildCard));
    foot.replaceChildren(
      h("div", { text: `Données Open-Meteo${f.members ? ` · fourchette de température sur ${f.members} scénarios ECMWF` : ""}` }),
      h("div", { text: "Survolez une courbe pour lire les valeurs de cette heure ; glissez, Maj + molette ou cliquez un jour pour défiler." }));
    layout();
  }

  function renderDays() {
    const today = Math.floor(f.nowIndex() / 24);
    days.replaceChildren(...f.days.map((d, k) => {
      const ms = f.stamps[d.start];
      const rain = ok(d.precip) && d.precip >= 0.2 ? `${fmt1(d.precip)} mm` : "";
      return h("button", {
        class: "wx-day", type: "button",
        dataset: { tip: [dayLabel(ms), ok(d.precip_prob) ? `risque de pluie ${fmt0(d.precip_prob)} %` : "",
          d.sunrise ? `soleil ${d.sunrise.slice(11)} – ${(d.sunset || "").slice(11)}` : ""].filter(Boolean).join(" · ") },
        onClick: () => scrollTo(d.start * hw, true),
      },
        h("span", { class: "wx-day-name", text: k === today ? "Auj." : dayLabel(ms) }),
        h("span", { class: "wx-day-temps" },
          h("b", { text: `${fmt0(d.t_max)}°`, style: { color: rgba(tempColor(d.t_max)) } }),
          h("span", { text: `${fmt0(d.t_min)}°` })),
        h("span", { class: "wx-day-rain", text: rain }));
    }), h("div", { class: "wx-thumb" }));
  }

  function buildCard(ch) {
    const reads = ch.readouts.map((r, k) => {
      const sw = h("span", { class: `wx-sw ${r.kind}` });
      const text = h("span");
      return { r, sw, text, el: h("span", { class: `wx-read${k === 0 ? " wx-main" : ""}` }, sw, text) };
    });
    ch.reads = reads;
    ch.when = h("span", { class: "wx-when" });
    ch.canvas = h("canvas");
    ch.axisL = h("canvas", { class: "wx-axis" });
    ch.axisR = h("canvas", { class: "wx-axis" });
    ch.plot = h("div", { class: "wx-plot", style: { height: `${ch.height}px` } }, ch.canvas);
    bindPlot(ch.plot);
    const card = h("div", { class: "wx-card", style: { "--c": ch.color } },
      h("div", { class: "wx-card-head" },
        h("div", { class: "wx-reads" },
          h("span", { class: "wx-title" }, h("span", { class: "wx-tile" }, ico(ch.icon, 15)), ch.title),
          ...reads.map((x) => x.el)),
        ch.when),
      h("div", { class: "wx-chart" }, ch.axisL, ch.plot, ch.axisR));
    return card;
  }

  // ─────────────────────────────────────────
  //  Échelle, défilement
  // ─────────────────────────────────────────
  const pad = () => hw / 2;
  const total = () => (f ? f.n * hw : 0);
  const maxOffset = () => Math.max(0, total() - vw);
  const nowOffset = () => (f.nowIndex() - 2) * hw;

  /** Largeur des graphiques connue : échelle recalculée, l'instant au centre gardé. */
  function layout() {
    if (!f || !charts[0]?.plot) return;
    const width = charts[0].plot.clientWidth;
    if (!width) return;                     // section masquée
    const oldHw = hw, oldVw = vw;
    vw = width;
    hw = vw / (span * 24);
    fitHeights();
    if (needsInitialScroll) {
      needsInitialScroll = false;
      offset = nowOffset();
    } else if (oldHw && (oldHw !== hw || oldVw !== vw)) {
      const center = (offset + oldVw / 2 - oldHw / 2) / oldHw;
      offset = pad() + center * hw - vw / 2;
    }
    offset = clamp(offset);
    const dpr = window.devicePixelRatio || 1;
    for (const ch of charts) {
      size(ch.canvas, vw, ch.height, dpr);
      size(ch.axisL, LEFT_AXIS, ch.height, dpr);
      size(ch.axisR, RIGHT_AXIS, ch.height, dpr);
      drawAxis(ch.axisL, ch, ch.left, ch.leftUnit, true, dpr);
      if (ch.right) drawAxis(ch.axisR, ch, ch.right, ch.rightUnit, false, dpr);
    }
    drawAll();
  }

  /** Graphiques à la hauteur de la fenêtre : la place libre est partagée selon leur hauteur
   *  de base (jamais en dessous ; fenêtre trop basse : la page défile). */
  function fitHeights() {
    for (const ch of charts) ch.base ??= ch.height;
    const plots = charts.reduce((sum, ch) => sum + ch.plot.offsetHeight, 0);
    const other = content.offsetHeight - plots;
    const base = charts.reduce((sum, ch) => sum + ch.base, 0);
    const scale = Math.max(1, Math.min(3, (body.clientHeight - other - 1) / base));
    for (const ch of charts) {
      ch.height = Math.floor(ch.base * scale);
      ch.plot.style.height = `${ch.height}px`;
    }
  }

  function size(canvas, w, hgt, dpr) {
    const W = Math.round(w * dpr), H = Math.round(hgt * dpr);
    if (canvas.width !== W) canvas.width = W;
    if (canvas.height !== H) canvas.height = H;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${hgt}px`;
  }

  const clamp = (x) => Math.max(0, Math.min(maxOffset(), x));

  function scrollTo(target, animate = false) {
    cancelAnimationFrame(anim);
    target = clamp(target);
    if (!animate || document.hidden) { offset = target; drawAll(); return; }
    const from = offset, t0 = performance.now(), dur = 320;
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur);
      offset = from + (target - from) * (1 - (1 - k) ** 3);
      drawAll();
      if (k < 1) anim = requestAnimationFrame(step);
    };
    anim = requestAnimationFrame(step);
  }
  const scrollBy = (dx) => scrollTo(offset + dx);
  const scrollToNow = (animate) => f && scrollTo(nowOffset(), animate);

  function setSpan(value) {
    if (value === span) return;
    span = value;
    ctx.saveSetting("weather_span", value);
    renderHead();
    layout();
  }

  let frame = 0;
  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; drawAll(); });
  }

  // ── Souris : survol (lecture), glisser (défilement), molette horizontale ──
  function bindPlot(plot) {
    let drag = null;
    const indexAt = (ev) => {
      const x = ev.clientX - plot.getBoundingClientRect().left + offset;
      return Math.max(0, Math.min(f.n - 1, Math.round((x - pad()) / hw)));
    };
    plot.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      drag = { x: ev.clientX, offset, moved: false };
      plot.setPointerCapture(ev.pointerId);
    });
    plot.addEventListener("pointermove", (ev) => {
      if (drag) {
        const dx = ev.clientX - drag.x;
        if (!drag.moved && Math.abs(dx) > 3) { drag.moved = true; plot.classList.add("dragging"); }
        if (drag.moved) { cancelAnimationFrame(anim); offset = clamp(drag.offset - dx); }
      }
      select(indexAt(ev));
    });
    const end = () => { drag = null; plot.classList.remove("dragging"); };
    plot.addEventListener("pointerup", end);
    plot.addEventListener("pointercancel", end);
    plot.addEventListener("pointerleave", () => { if (!drag) select(null); });
    plot.addEventListener("wheel", (ev) => {
      const horizontal = ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY);
      if (!horizontal) return;               // molette seule : la page défile
      ev.preventDefault();
      cancelAnimationFrame(anim);
      offset = clamp(offset + (Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : ev.deltaY));
      schedule();
    }, { passive: false });
  }

  function select(i) {
    if (i === selection) { schedule(); return; }
    selection = i;
    schedule();
  }

  function onKey(ev) {
    if (!f || isTyping(ev.target) || ev.ctrlKey || ev.altKey) return;
    const page = span * 24 * hw;
    if (ev.key === "ArrowLeft") scrollTo(offset - page / 4, true);
    else if (ev.key === "ArrowRight") scrollTo(offset + page / 4, true);
    else if (ev.key === "PageUp") scrollTo(offset - page, true);
    else if (ev.key === "PageDown") scrollTo(offset + page, true);
    else if (ev.key === "Home") scrollToNow(true);
    else return;
    ev.preventDefault();
  }

  // ─────────────────────────────────────────
  //  Dessin
  // ─────────────────────────────────────────
  function drawAll() {
    if (!f || !vw) return;
    const now = f.nowIndex();
    const i = selection ?? Math.max(0, Math.min(f.n - 1, Math.round(now)));
    const dpr = window.devicePixelRatio || 1;
    for (const ch of charts) {
      draw(ch, now, dpr);
      for (const { r, sw, text } of ch.reads) {
        text.textContent = r.text(i);
        sw.style.setProperty("--c", css(r.color(i)));
      }
      ch.when.textContent = selection == null ? "Maintenant" : `${dayLabel(f.stamps[i])} · ${f.hours[i]}h`;
    }
    // Bandeau des jours : partie affichée
    const thumb = days.querySelector(".wx-thumb");
    if (thumb && days.children.length > 1) {
      const first = days.children[0], last = days.children[f.days.length - 1];
      const left = first.offsetLeft, width = last.offsetLeft + last.offsetWidth - left;
      const hours = f.days.length * 24;
      const a = offset / hw / hours, b = (offset + vw) / hw / hours;
      thumb.style.left = `${left + width * Math.max(0, a)}px`;
      thumb.style.width = `${width * Math.max(0, Math.min(1, b) - Math.max(0, a))}px`;
    }
  }

  function geometry(ch) {
    const top = ch.arrows ? 30 : 10;
    const bottom = ch.height - 22;
    return { top, bottom, y: (v, r) => bottom - ((v - r.min) / (r.max - r.min)) * (bottom - top) };
  }

  function text(c, s, x, y, { size = 10.5, weight = 500, color = rgba(WHITE, 0.5), align = "left", base = "top" } = {}) {
    c.font = `${weight} ${size}px ${FONT}`;
    c.fillStyle = color;
    c.textAlign = align;
    c.textBaseline = base;
    c.fillText(s, x, y);
  }

  function valueGradient(c, g, r, fn, alpha) {
    const grad = c.createLinearGradient(0, g.bottom, 0, g.top);
    for (let j = 0; j <= 12; j++) grad.addColorStop(j / 12, rgba(fn(r.min + ((r.max - r.min) * j) / 12), alpha));
    return grad;
  }

  function draw(ch, now, dpr) {
    const c = ch.canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, vw, ch.height);
    const g = geometry(ch);
    const X = (i) => pad() + i * hw - offset;
    const L = ch.left;
    const n = f.n;
    const i0 = Math.max(0, Math.floor(offset / hw) - 1);
    const i1 = Math.min(n - 1, Math.ceil((offset + vw) / hw) + 1);

    // Jour / nuit : heures de jour éclaircies
    c.fillStyle = "rgba(255, 255, 255, .045)";
    let start = -1;
    for (let i = 0; i <= n; i++) {
      const day = i < n && f.is_day[i];
      if (day && start < 0) start = i;
      if (!day && start >= 0) { c.fillRect(X(start - 0.5), 0, (i - start) * hw, g.bottom); start = -1; }
    }

    // Grille horizontale
    c.strokeStyle = "rgba(255, 255, 255, .07)";
    c.lineWidth = 1;
    c.beginPath();
    for (const t of L.ticks) { const y = Math.round(g.y(t, L)) + 0.5; c.moveTo(0, y); c.lineTo(vw, y); }
    c.stroke();

    // Séparateurs de jours
    c.strokeStyle = "rgba(255, 255, 255, .3)";
    c.lineWidth = 1.2;
    c.beginPath();
    for (let i = Math.max(1, i0); i <= i1; i++) if (f.hours[i] === 0) { c.moveTo(X(i), 0); c.lineTo(X(i), g.bottom); }
    c.stroke();

    // Zones entre le min et le max du jour, en dégradé du bas vers le haut
    for (const z of ch.zones || []) {
      const top = g.y(z.high, L), bottom = g.y(z.low, L);
      if (bottom - top < 0.5) continue;
      const grad = c.createLinearGradient(0, bottom, 0, top);
      grad.addColorStop(0, rgba(tempColor(z.low), 0.16));
      grad.addColorStop(1, rgba(tempColor(z.high), 0.16));
      c.fillStyle = grad;
      c.fillRect(X(z.from), top, (z.to - z.from) * hw, bottom - top);
    }

    // Bande d'incertitude (fourchette des scénarios)
    const band = ch.band;
    if (band) {
      c.fillStyle = valueGradient(c, g, L, band.colorFn, band.alpha);
      const Yu = (v) => g.y(v, L);
      band.low.forEach((seg, k) => {
        c.beginPath();
        trace(c, band.up[k], band.upper, X, Yu);
        trace(c, seg, band.lower, X, Yu, { reverse: true, move: false });
        c.closePath();
        c.fill();
      });
    }

    // Niveaux : traits d'un séparateur de jour à l'autre sous les courbes, valeurs par-dessus
    for (const l of ch.levels || []) {
      const y = g.y(l.value, L);
      c.strokeStyle = rgba(l.color, 0.55);
      c.lineWidth = 1;
      c.beginPath(); c.moveTo(X(l.from), y); c.lineTo(X(l.to), y); c.stroke();
    }

    // Séries
    for (const s of ch.series) drawSeries(c, s, s.right ? ch.right : L, g, X);

    for (const l of ch.levels || []) {
      if (l.to < i0 - 24 || l.from > i1 + 24) continue;
      c.font = `700 10.5px ${FONT}`;
      const w = c.measureText(l.label).width, th = 13;
      const y = g.y(l.value, L);
      const left = X(l.from) + 3;
      const x = Math.max(left, Math.min(X(l.at) - w / 2, Math.max(left, X(l.to) - 3 - w)));
      // De l'autre côté du trait s'il n'y a pas la place
      const above = l.above ? y - th - 1 >= g.top - 10 : y + 1 + th > g.bottom;
      text(c, l.label, x, above ? y - th - 1 : y + 1, { weight: 700, color: rgba(l.color) });
    }

    // Passé assombri + repère « maintenant »
    const nowX = X(now);
    if (nowX > 0) {
      c.fillStyle = "rgba(0, 0, 0, .22)";
      c.fillRect(0, 0, Math.min(nowX, vw), g.bottom);
    }
    if (nowX > 0 && nowX < vw) {
      c.strokeStyle = "rgba(255, 255, 255, .85)";
      c.lineWidth = 1.4;
      c.setLineDash([3, 3]);
      c.beginPath(); c.moveTo(nowX, 2); c.lineTo(nowX, g.bottom); c.stroke();
      c.setLineDash([]);
      c.fillStyle = "#fff";
      c.beginPath(); c.arc(nowX, g.bottom, 3.5, 0, Math.PI * 2); c.fill();
    }

    // Flèches de vent (dans le sens où il souffle)
    if (ch.arrows) {
      const step = niceStep(26 / hw);
      c.fillStyle = rgba(ch.color, 0.9);
      for (let i = i0; i <= i1; i++) {
        if (f.hours[i] % step !== 0 || !ok(ch.arrows[i])) continue;
        c.save();
        c.translate(X(i), 14);
        c.rotate(((ch.arrows[i] + 180) * Math.PI) / 180);
        c.beginPath(); c.moveTo(0, -7); c.lineTo(4.5, 5); c.lineTo(0, 2.4); c.lineTo(-4.5, 5); c.closePath();
        c.fill();
        c.restore();
      }
    }

    // Heures
    const step = niceStep(36 / hw);
    for (let i = i0; i <= i1; i++) {
      if (f.hours[i] % step !== 0) continue;
      const midnight = f.hours[i] === 0;
      text(c, midnight ? dayLabel(f.stamps[i]) : `${f.hours[i]}h`, X(i), g.bottom + 5,
        { align: "center", weight: midnight ? 700 : 500, color: midnight ? "#fff" : rgba(WHITE, 0.5) });
    }

    // Heure lue : curseur et points de chaque courbe
    if (selection != null) {
      const x = X(selection);
      c.strokeStyle = "rgba(255, 255, 255, .6)";
      c.lineWidth = 1.2;
      c.beginPath(); c.moveTo(x, 0); c.lineTo(x, g.bottom); c.stroke();
      for (const s of ch.series) {
        // Série décalée : le point suit la courbe, entre deux valeurs
        const v = s.shift === 0 ? s.values[selection] : curveAt(s.values, s.segs, selection - s.shift);
        if (!ok(v)) continue;
        const r = s.right ? ch.right : L;
        const col = s.colorFn ? rgba(s.colorFn(v)) : css(s.color);
        const y = g.y(v, r);
        c.globalAlpha = 0.25; c.fillStyle = col;
        c.beginPath(); c.arc(x, y, 8, 0, Math.PI * 2); c.fill();
        c.globalAlpha = 1;
        c.beginPath(); c.arc(x, y, 4.2, 0, Math.PI * 2); c.fill();
        c.strokeStyle = "#000"; c.lineWidth = 1.6; c.stroke();
      }
    }
  }

  function drawSeries(c, s, r, g, X) {
    const Xs = (i) => X(i + s.shift);
    const Y = (v) => g.y(v, r);
    c.lineCap = "round";
    c.lineJoin = "round";
    c.lineWidth = s.kind === "dashed" ? s.width * 0.75 : s.width;
    c.strokeStyle = s.colorFn ? valueGradient(c, g, r, s.colorFn, 1) : s.color;
    for (const seg of s.segs) {
      if (s.kind === "area") {
        c.beginPath();
        trace(c, seg, s.values, Xs, Y);
        c.lineTo(Xs(seg.end), g.bottom);
        c.lineTo(Xs(seg.start), g.bottom);
        c.closePath();
        const grad = c.createLinearGradient(0, g.top, 0, g.bottom);
        grad.addColorStop(0, rgba(s.color, s.fill));    // aires : couleurs #rrggbb
        grad.addColorStop(1, rgba(s.color, 0.02));
        c.fillStyle = grad;
        c.fill();
      }
      c.beginPath();
      trace(c, seg, s.values, Xs, Y);
      c.setLineDash(s.kind === "dashed" ? [5, 4] : []);
      c.stroke();
    }
    c.setLineDash([]);
  }

  /** Graduations d'un axe vertical (colonne fixe, hors zone défilante). */
  function drawAxis(canvas, ch, r, unit, alignEnd, dpr) {
    const c = canvas.getContext("2d");
    const w = alignEnd ? LEFT_AXIS : RIGHT_AXIS;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, ch.height);
    const g = geometry(ch);
    const x = alignEnd ? w - 6 : 6;
    const align = alignEnd ? "right" : "left";
    for (const t of r.ticks) text(c, r.label(t), x, g.y(t, r), { align, base: "middle" });
    text(c, unit, x, g.bottom + 5, { size: 10, weight: 600, color: rgba(WHITE, 0.38), align });
  }

  // ─────────────────────────────────────────
  //  Lieu : position actuelle ou ville cherchée
  // ─────────────────────────────────────────
  function choose(next) {
    const changed = JSON.stringify(next) !== JSON.stringify(place);
    place = next;
    api.weather_set_place(next);
    if (!changed && data) return;
    data = null; f = null; charts = []; error = "";
    render();
    load();
  }

  function openPlaces() {
    const input = h("input", { class: "field", type: "search", placeholder: "Rechercher une ville…",
      spellcheck: "false", autocomplete: "off" });
    const list = h("div", { class: "wx-opts", role: "listbox" });
    let options = [];
    let focusIdx = 0;
    let searchGen = 0;
    const panel = openPopover(h("div", { class: "wx-pop-inner" }, input, list), placeBtn,
      { cls: "wx-pop", label: "Lieu de la météo" });

    function option(icon, title, sub, active, onPick) {
      const opt = h("div", { class: `wx-opt${active ? " on" : ""}`, role: "option",
        onMousedown: (ev) => ev.preventDefault(), onClick: () => { panel.close(); onPick(); } },
      ico(icon, 15), h("span", { class: "wx-opt-text" }, h("span", { text: title }), sub ? h("span", { class: "sub", text: sub }) : null),
      active ? ico("check", 14) : null);
      return opt;
    }

    function show(results, note = "") {
      const here = option("locate-fixed", "Position actuelle", data?.here ? data.place : "localisation de Windows", !place,
        () => choose(null));
      const current = place && !results.length && !input.value.trim()
        ? [option("map-pin", place.name, place.detail, true, () => choose(place))] : [];
      options = [here, ...current, ...results.map((p) => option("map-pin", p.name, p.detail,
        !!place && Math.abs(p.lat - place.lat) < 0.01 && Math.abs(p.lon - place.lon) < 0.01, () => choose(p)))];
      list.replaceChildren(...options, ...(note ? [h("div", { class: "wx-opt-note", text: note })] : []));
      focusIdx = results.length ? 1 + current.length : 0;
      mark();
    }
    const mark = () => options.forEach((o, k) => o.classList.toggle("focus", k === focusIdx));

    const search = debounce(async () => {
      const q = input.value.trim();
      const gen = ++searchGen;
      if (q.length < 2) { show([]); return; }
      const res = await api.weather_search(q);
      if (gen !== searchGen) return;
      if (res?.error) show([], res.error);
      else show(res?.results || [], res?.results?.length ? "" : "Aucune ville trouvée.");
    }, 250);
    input.addEventListener("input", search);
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        focusIdx = (focusIdx + (ev.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
        mark();
        options[focusIdx]?.scrollIntoView({ block: "nearest" });
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        options[focusIdx]?.click();
      }
    });
    show([]);
    setTimeout(() => input.focus(), 0);
  }

  // ─────────────────────────────────────────
  //  Cycle de vie
  // ─────────────────────────────────────────
  new ResizeObserver(() => { if (ctx.isActive("weather")) layout(); }).observe(body);
  // Repère « maintenant » qui avance, prévision rechargée quand elle a vieilli
  setInterval(() => {
    if (!ctx.isActive("weather") || document.hidden) return;
    if (f) drawAll();
    if (!loading && stale()) load();
  }, 60 * 1000);

  if (!fromCache(boot.cached)) render();

  return {
    el,
    onShow() {
      layout();
      if (!loading && stale()) load();
    },
    onWindowShown() {
      if (!loading && stale()) load();
    },
    focus() { el.focus({ preventScroll: true }); },
  };
}
