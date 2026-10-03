// Panneau « Garder au premier plan » (épingle de la barre latérale) : n'importe
// quelle fenêtre ouverte peut rester au-dessus des autres, et être rendue plus
// ou moins transparente. Chaque fenêtre est présentée avec l'icône de son
// application, une miniature et son titre complet.
import { h, plural } from "./dom.js";
import { ico, iconButton, openPopover, toast } from "./ui.js";

const MIN_OPACITY = 20;        // comme winutil.MIN_OPACITY
const THUMB_JOBS = 3;          // captures en parallèle (un thread Python par appel)
const thumbs = new Map();      // hwnd → dernière miniature, montrée d'emblée à la réouverture
let panel = null;
let opening = false;

const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function switchEl() {
  return h("span", { class: "switch", role: "switch", "aria-checked": "false" });
}

function setSwitch(sw, on) {
  sw.classList.toggle("on", on);
  sw.setAttribute("aria-checked", String(on));
}

function appIcon(w, size) {
  return w.icon
    ? h("img", { class: "pin-app-icon", src: w.icon, alt: "", width: size, height: size, draggable: "false" })
    : ico("app-window", size);
}

/**
 * Curseur d'opacité, appliqué en direct pendant le glissement : un appel à la
 * fois, la dernière valeur demandée part dès que le précédent a répondu.
 * `apply(percent)` retourne false si c'est refusé (le curseur revient alors à
 * la valeur en place). `initial` à null : transparence gérée par l'application.
 */
function opacitySlider(initial, apply) {
  const input = h("input", { type: "range", min: MIN_OPACITY, max: 100, step: 5, "aria-label": "Opacité",
    onInput: () => request(Number(input.value)) });
  const value = h("span", { class: "pin-opacity-value" });
  const el = h("div", { class: "pin-opacity", dataset: { tip: "Opacité · double-clic : opaque" },
    onDblclick: () => { if (!input.disabled) request(100); } }, ico("blend", 14), input, value);
  let current = initial ?? 100;    // valeur en place
  let wanted = current;
  let busy = false;

  function show(percent) {
    input.value = percent;
    value.textContent = `${percent}\u00a0%`;
    el.style.setProperty("--fill", `${((percent - MIN_OPACITY) / (100 - MIN_OPACITY)) * 100}%`);
    el.classList.toggle("faded", percent < 100);
  }

  async function request(percent) {
    show(percent);
    wanted = percent;
    if (busy) return;
    busy = true;
    try {
      while (wanted !== current) {
        const target = wanted;
        if (!(await apply(target))) { wanted = current; show(current); break; }
        current = target;
      }
    } finally {
      busy = false;
    }
  }

  function disable(tip) {
    input.disabled = true;
    el.classList.add("disabled");
    el.dataset.tip = tip;
  }

  show(current);
  if (initial == null) disable("Transparence gérée par l'application elle-même");
  return { el, disable };
}

export async function togglePinPanel(ctx, anchor) {
  if (panel) { panel.close(); return; }
  if (opening) return;
  opening = true;
  const first = await ctx.api.windows_list().finally(() => { opening = false; });
  buildPanel(ctx, anchor, first || []);
}

function buildPanel(ctx, anchor, windows) {
  const { api, store } = ctx;
  let items = [];            // { w, el, shot, sw, meta, fader } dans l'ordre d'empilement
  let focused = null;        // ligne choisie au clavier
  let generation = 0;        // invalide les miniatures d'un chargement précédent
  let onSlider = false;      // clic commencé sur un curseur d'opacité : la ligne ne bascule pas
  const pressed = (ev) => { onSlider = !!ev.target.closest(".pin-opacity"); };

  // ── En-tête ────────────────────────────────
  const count = h("span", { class: "pin-count" });
  const filter = h("input", { type: "text", placeholder: "Filtrer par titre ou application",
    spellcheck: "false", "aria-label": "Filtrer les fenêtres", onInput: applyFilter, onKeydown: onFilterKey });
  const head = h("div", { class: "pin-head" },
    ico("pin", 15),
    h("span", { class: "pin-heading", text: "Garder au premier plan" }),
    count,
    h("label", { class: "search pin-search" }, ico("search", 14), filter),
    iconButton("refresh-cw", "Actualiser la liste", () => load(), { size: 14, cls: "sm" }));

  // ── Boostache lui-même ─────────────────────
  const selfSwitch = switchEl();
  const selfFader = opacitySlider(store.settings.window_opacity ?? 100, fadeSelf);
  const selfRow = h("div", { class: "pin-row pin-self", onPointerdown: pressed,
    onClick: () => { if (!onSlider) toggleSelf(); }, onMouseenter: () => setFocus(self) },
    h("div", { class: "brand-mark" }, ico("zap", 14, 2)),
    h("div", { class: "pin-text" },
      h("div", { class: "pin-title", text: "Boostache" }),
      h("div", { class: "pin-meta", text: "Cette fenêtre" })),
    selfFader.el,
    selfSwitch);
  const self = { el: selfRow, self: true };
  const renderSelf = () => {
    setSwitch(selfSwitch, !!store.settings.always_on_top);
    selfRow.classList.toggle("pinned", !!store.settings.always_on_top);
  };
  renderSelf();

  // ── Liste et pied ──────────────────────────
  const list = h("div", { class: "pin-list" });
  const empty = h("div", { class: "pin-empty", hidden: true });
  const releaseAll = h("button", { class: "btn ghost sm", type: "button", onClick: releaseAllPins,
    dataset: { tip: "Libérer toutes les autres fenêtres" } }, ico("pin-off", 14), "Tout libérer");
  const foot = h("div", { class: "pin-foot" },
    h("span", { text: "Clic ou Entrée : garder au premier plan ou libérer · ↑ ↓ : choisir · Échap : fermer" }),
    releaseAll);

  panel = openPopover([head, selfRow, list, foot], anchor, {
    cls: "pin-panel", label: "Garder au premier plan",
    onClose: () => { panel = null; generation++; },
  });
  show(windows);
  filter.focus();

  // ── Rendu ──────────────────────────────────
  function row(w) {
    const item = { w, shot: h("div", { class: "pin-shot" }), sw: switchEl(), meta: h("div", { class: "pin-meta" }) };
    item.fader = opacitySlider(w.opacity, (percent) => fade(item, percent));
    item.el = h("div", { class: "pin-row", onPointerdown: pressed,
      onClick: () => { if (!onSlider) toggle(item); }, onMouseenter: () => setFocus(item) },
      item.shot,
      h("div", { class: "pin-text" }, h("div", { class: "pin-title", text: w.title }), item.meta),
      item.fader.el,
      item.sw);
    setShot(item, thumbs.get(w.hwnd), !w.minimized);
    update(item);
    return item;
  }

  function setShot(item, url, loading = false) {
    const { w, shot } = item;
    shot.replaceChildren(...[
      url ? h("img", { class: "pin-thumb", src: url, alt: "", draggable: "false" }) : appIcon(w, 26),
      w.minimized && h("span", { class: "pin-tag", text: "Réduite" }),
      h("span", { class: "pin-badge" }, ico("pin", 11, 2.2)),
    ].filter(Boolean));
    shot.classList.toggle("loading", loading && !url);
  }

  function update(item) {
    const { w } = item;
    item.el.classList.toggle("pinned", w.topmost);
    setSwitch(item.sw, w.topmost);
    item.meta.replaceChildren(...[
      appIcon(w, 16),
      h("span", { class: "pin-app", text: w.app || "Application" }),
      w.topmost && h("span", { class: "pin-state", text: "Au premier plan" }),
    ].filter(Boolean));
  }

  function summarize() {
    const pinned = items.filter((it) => it.w.topmost).length;
    count.textContent = items.length
      ? plural(items.length, "fenêtre", "fenêtres") + (pinned ? ` · ${pinned} au premier plan` : "")
      : "";
    releaseAll.hidden = !pinned;
  }

  function show(windows) {
    items = windows.map(row);
    list.replaceChildren(...items.map((it) => it.el), empty);
    const alive = new Set(windows.map((w) => w.hwnd));
    for (const hwnd of thumbs.keys()) if (!alive.has(hwnd)) thumbs.delete(hwnd);
    focused = null;
    applyFilter();
    summarize();
    loadThumbs(++generation);
  }

  async function load() {
    const gen = ++generation;
    const windows = await api.windows_list();
    if (gen === generation && panel) show(windows || []);
  }

  async function loadThumbs(gen) {
    const queue = items.filter((it) => !it.w.minimized);
    const worker = async () => {
      for (let it = queue.shift(); it && gen === generation; it = queue.shift()) {
        const url = await api.window_thumbnail(it.w.hwnd);
        if (gen !== generation) return;
        if (url) thumbs.set(it.w.hwnd, url);
        else thumbs.delete(it.w.hwnd);
        setShot(it, url);
      }
    };
    await Promise.all(Array.from({ length: THUMB_JOBS }, worker));
  }

  // ── Filtre et clavier ──────────────────────
  function visible() {
    return [self, ...items.filter((it) => !it.el.hidden)];
  }

  function applyFilter() {
    const q = fold(filter.value.trim());
    let shown = 0;
    for (const it of items) {
      it.el.hidden = !!q && !fold(`${it.w.title} ${it.w.app}`).includes(q);
      if (!it.el.hidden) shown += 1;
    }
    empty.hidden = shown > 0;
    empty.textContent = items.length ? `Aucune fenêtre ne correspond à « ${filter.value.trim()} ».`
      : "Aucune autre fenêtre ouverte.";
    if (focused?.el.hidden) setFocus(null);
  }

  function setFocus(item, scroll = false) {
    focused = item;
    for (const it of [self, ...items]) it.el.classList.toggle("focus", it === item);
    if (item && scroll) item.el.scrollIntoView({ block: "nearest" });
  }

  function onFilterKey(ev) {
    const rows = visible();
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      const step = ev.key === "ArrowDown" ? 1 : -1;
      const index = rows.indexOf(focused);
      const next = index < 0 ? (step > 0 ? (rows[1] || rows[0]) : rows[rows.length - 1])
        : rows[(index + step + rows.length) % rows.length];
      setFocus(next, true);
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      const target = focused || (filter.value.trim() ? rows[1] : null);
      if (target) target.self ? toggleSelf() : toggle(target);
    }
  }

  // ── Actions ────────────────────────────────
  async function toggleSelf() {
    await ctx.saveSetting("always_on_top", !store.settings.always_on_top);
    renderSelf();
  }

  async function fadeSelf(percent) {
    return (await ctx.saveSetting("window_opacity", percent)) === percent;
  }

  function forget(item) {
    toast("Cette fenêtre a été fermée.", "info");
    items = items.filter((it) => it !== item);
    item.el.remove();
    thumbs.delete(item.w.hwnd);
    if (focused === item) focused = null;
    applyFilter();
    summarize();
  }

  async function fade(item, percent) {
    const done = await api.window_set_opacity(item.w.hwnd, percent);
    if (done === null) {
      if (items.includes(item)) forget(item);
    } else if (!done) {
      item.fader.disable("Windows refuse de rendre cette fenêtre transparente");
      toast("Windows refuse de rendre cette fenêtre transparente (application lancée en administrateur ?).", "error");
    } else {
      item.w.opacity = percent;
    }
    return !!done;
  }

  async function toggle(item) {
    if (item.busy) return;
    item.busy = true;
    const on = !item.w.topmost;
    const done = await api.window_set_topmost(item.w.hwnd, on).finally(() => { item.busy = false; });
    if (done === null) {
      forget(item);
    } else if (!done) {
      toast("Windows refuse de modifier cette fenêtre (application lancée en administrateur ?).", "error");
    } else {
      item.w.topmost = on;
      update(item);
      summarize();
    }
  }

  async function releaseAllPins() {
    const pinned = items.filter((it) => it.w.topmost);
    const results = await Promise.all(pinned.map((it) => api.window_set_topmost(it.w.hwnd, false)));
    pinned.forEach((it, i) => {
      if (results[i] === false) return;
      it.w.topmost = false;
      update(it);
    });
    if (results.includes(false)) {
      toast("Windows refuse de libérer certaines fenêtres (applications lancées en administrateur ?).", "error");
    }
    summarize();
  }
}
