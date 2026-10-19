// Vue Journal : ce que fait Boostache et surtout ce qui échoue — erreurs et
// avertissements avec la trace de l'exception — pour suivre un problème et le
// résoudre. Entrées de engine.Logger (Python : modules, exceptions non rattrapées,
// logging, stdout/stderr, voir diagnostics.py) et de l'interface (errors.js).
import { h, plural, formatDay, formatFullDate, formatHour } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow } from "../ui.js";

const MAX_ENTRIES = 5000;
const LEVELS = {
  error:   { label: "Erreurs",        one: "Erreur",        icon: "circle-x",       name: "ERREUR" },
  warning: { label: "Avertissements", one: "Avertissement", icon: "triangle-alert", name: "AVERT" },
  info:    { label: "Infos",          one: "Info",          icon: "info",           name: "INFO" },
  debug:   { label: "Détails",        one: "Détail technique", icon: "braces",      name: "DEBUG" },
};
const ORDER = ["error", "warning", "info", "debug"];
const DEFAULT_LEVELS = ["error", "warning", "info"];
// Modules Python (et autres sources) → nom affiché ; le nom technique reste en infobulle
const SOURCES = {
  main: "Application", app: "Application", api: "Application", bridge: "Application", engine: "Moteur",
  chat: "Conversations", terminals: "Consoles", notes: "Notes", clipboard_listener: "Presse-papiers",
  captures: "Captures", whatsapp: "WhatsApp", gmail: "Gmail", webpane: "Sites intégrés",
  recorder: "Enregistreur", wasapi: "Enregistreur", transcriber: "Transcription", assistant: "Assistant",
  rag: "Documents", docreader: "Documents", ollama_admin: "Ollama", speech: "Lecture", ocr: "OCR",
  tasks: "Tâches", custom_tasks: "Tâches", bindings: "Raccourcis", storage: "Stockage", winutil: "Windows",
  diagnostics: "Diagnostic", interface: "Interface", python: "Python", threading: "Python",
};
const SESSION_STATES = { running: "en cours", crash: "plantage", abnormal: "arrêt anormal", shutdown: "arrêt de Windows" };

const sourceName = (src) => SOURCES[src] || src;
const pad = (n) => String(n).padStart(2, "0");
const clock = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const sizeText = (n) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} Ko`
  : `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} Mo`);

/** Entrée en texte, comme dans le fichier du journal (diagnostics.py). */
function entryText(e, details = true) {
  let text = `${clock(new Date(e.t * 1000))} ${(LEVELS[e.level]?.name || e.level).padEnd(6)} [${e.src}] ${e.msg}`;
  if ((e.repeat || 1) > 1) text += `  (×${e.repeat}, dernière fois à ${clock(new Date(e.last * 1000))})`;
  if (details && e.details) text += `\n${e.details.split("\n").map((l) => `    ${l}`.trimEnd()).join("\n")}`;
  return text;
}

export function createLogsView(ctx, state) {
  const { api, on } = ctx;
  const rows = new Map();          // seq → { entry, el, hay } (ordre d'arrivée)
  const counts = { error: 0, warning: 0, info: 0, debug: 0 };
  const shown = new Set(DEFAULT_LEVELS);
  let source = null;               // seule source affichée (null : toutes)
  let query = "";
  let follow = true;               // suivre la fin tant que l'on n'est pas remonté
  let missed = 0;                  // entrées arrivées pendant que l'on lit plus haut
  let unseen = 0;                  // erreurs arrivées hors de la vue (pastille)
  let windowVisible = !!state.visible;
  let pending = [];
  let flushTimer = null;
  let lastSeq = 0;

  // ── Éléments ────────────────────────────────
  const sub = h("span", { class: "view-sub" });
  const search = h("input", { type: "search", placeholder: "Filtrer…", spellcheck: "false",
    "aria-label": "Filtrer le journal" });
  const levelBtns = Object.fromEntries(ORDER.map((lv) => [lv, h("button", {
    class: `btn sm log-level lv-${lv}`, type: "button", role: "switch", onClick: () => toggleLevel(lv) })]));
  const sourceBtn = h("button", { class: "btn ghost sm log-source", type: "button",
    onClick: (ev) => sourceMenu(ev.currentTarget) });
  const box = h("div", { class: "logs", tabindex: "0", role: "log", "aria-label": "Entrées du journal" });
  const empty = h("div", { class: "empty logs-empty", hidden: true });
  const jumpBtn = h("button", { class: "logs-jump", type: "button", hidden: true, onClick: () => toBottom() });
  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Journal" }), sub,
      h("div", { class: "head-spacer" }),
      h("button", { class: "btn ghost sm", type: "button", onClick: copyReport,
        dataset: { tip: "Copie un rapport de diagnostic (versions, état, erreurs avec leurs traces, dernières entrées) "
          + "à coller dans une conversation ou un message pour résoudre un problème" } },
      ico("bug", 14), h("span", { class: "logs-report-label", text: "Copier le rapport" })),
      iconButton("folder-open", "Fichiers du journal", (ev) => filesMenu(ev.currentTarget)),
      iconButton("copy", "Copier les entrées affichées", copyShown),
      iconButton("eraser", "Effacer le journal", clear)),
    h("div", { class: "logs-bar" }, ...ORDER.map((lv) => levelBtns[lv]), h("div", { class: "head-spacer" }),
      sourceBtn, h("label", { class: "search" }, ico("search", 14), search)),
    h("div", { class: "logs-wrap" }, box, empty, jumpBtn));

  // ── Lignes ──────────────────────────────────
  function makeRow(entry) {
    const lv = LEVELS[entry.level] || LEVELS.info;
    const d = new Date(entry.t * 1000);
    const row = h("div", { class: "log-row" },
      h("span", { class: "log-ts", text: clock(d), dataset: { tip: formatFullDate(d) } }),
      h("span", { class: "log-lv", dataset: { tip: lv.one } }, ico(lv.icon, 13)),
      h("span", { class: "log-src", text: sourceName(entry.src), dataset: { tip: entry.src } }),
      h("span", { class: "log-msg", text: entry.msg }),
      h("span", { class: "log-rep" }),
      entry.details ? h("span", { class: "log-more", dataset: { tip: "Détails" } }, ico("chevron-right", 13)) : null);
    const wrap = h("div", { class: `log-entry lv-${entry.level}${entry.details ? " has-details" : ""}`,
      dataset: { seq: entry.seq } }, row);
    const r = { entry, el: wrap, pre: null,
      hay: `${sourceName(entry.src)} ${entry.src} ${entry.msg} ${entry.details || ""}`.toLowerCase() };
    setRepeat(r);
    wrap.hidden = !visible(r);
    return r;
  }

  function setRepeat(r) {
    const { entry } = r;
    const rep = r.el.querySelector(".log-rep");
    if ((entry.repeat || 1) < 2) return;
    rep.textContent = `×${entry.repeat}`;
    rep.dataset.tip = `Répété ${entry.repeat} fois, dernière fois à ${clock(new Date(entry.last * 1000))}`;
  }

  function toggleDetails(r, open = !r.el.classList.contains("open")) {
    if (!r?.entry.details) return;
    r.el.classList.toggle("open", open);
    if (open && !r.pre) {
      r.pre = h("pre", { class: "log-details", text: r.entry.details });
      r.el.append(r.pre);
    }
  }

  const visible = (r) => shown.has(r.entry.level) && (!source || r.entry.src === source)
    && (!query || r.hay.includes(query));

  // ── Arrivée des entrées ─────────────────────
  const watching = () => ctx.isActive("logs") && windowVisible;
  const atBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight < 40;

  function receive(entry) {
    pending.push(entry);
    // (pas de requestAnimationFrame : il ne s'exécute pas tant que la fenêtre est masquée)
    if (!flushTimer) flushTimer = setTimeout(flush, 40);
  }

  function flush() {
    flushTimer = null;
    const batch = pending;
    pending = [];
    const frag = document.createDocumentFragment();
    let added = 0;
    for (const entry of batch) {
      const known = rows.get(entry.seq);
      if (known) {               // message répété : son compteur
        known.entry = entry;
        setRepeat(known);
        continue;
      }
      if (entry.seq <= lastSeq) continue;     // déjà retirée (trop ancienne) ou effacée
      lastSeq = entry.seq;
      const r = makeRow(entry);
      rows.set(entry.seq, r);
      counts[entry.level] = (counts[entry.level] || 0) + 1;
      frag.append(r.el);
      if (!r.el.hidden) added += 1;
      if (entry.level === "error" && !watching()) unseen += 1;
    }
    box.append(frag);
    trim();
    if (follow) toBottom();
    else if (added) { missed += added; renderJump(); }
    render();
  }

  function trim() {
    for (const [seq, r] of rows) {
      if (rows.size <= MAX_ENTRIES) break;
      r.el.remove();
      rows.delete(seq);
      counts[r.entry.level] -= 1;
    }
  }

  function toBottom() {
    box.scrollTop = box.scrollHeight;
    follow = true;
    missed = 0;
    renderJump();
  }

  box.addEventListener("scroll", () => {
    if (!box.clientHeight) return;     // vue masquée
    follow = atBottom();
    if (follow && missed) { missed = 0; renderJump(); }
  });

  function renderJump() {
    jumpBtn.hidden = follow || !missed;
    jumpBtn.replaceChildren(ico("arrow-down-to-line", 14),
      plural(missed, "nouvelle entrée", "nouvelles entrées"));
  }

  // ── Filtres ─────────────────────────────────
  function applyFilter() {
    for (const r of rows.values()) r.el.hidden = !visible(r);
    render();
    if (follow) toBottom();
  }

  function toggleLevel(lv) {
    if (shown.has(lv)) shown.delete(lv); else shown.add(lv);
    applyFilter();
  }

  function setSource(src) {
    source = src;
    applyFilter();
  }

  function resetFilters() {
    ORDER.forEach((lv) => shown.add(lv));
    source = null;
    query = search.value = "";
    applyFilter();
  }

  search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); applyFilter(); });
  search.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && search.value) { ev.stopPropagation(); search.value = ""; query = ""; applyFilter(); }
  });

  function sourceMenu(anchor) {
    const tally = new Map();
    for (const r of rows.values()) tally.set(r.entry.src, (tally.get(r.entry.src) || 0) + 1);
    const list = [...tally].sort((a, b) => sourceName(a[0]).localeCompare(sourceName(b[0]), "fr")
      || a[0].localeCompare(b[0]));
    openMenuBelow([
      { section: "Source" },
      { label: "Toutes les sources", checked: !source, hint: String(rows.size), onSelect: () => setSource(null) },
      ...(list.length ? ["-"] : []),
      ...list.map(([src, n]) => ({ label: sourceName(src) === src ? src : `${sourceName(src)} · ${src}`,
        hint: String(n), checked: source === src, onSelect: () => setSource(src) })),
    ], anchor, "right");
  }

  // ── Rendu ───────────────────────────────────
  function render() {
    const total = rows.size;
    let count = 0;
    for (const r of rows.values()) if (!r.el.hidden) count += 1;
    const filtered = count < total;
    sub.textContent = !total ? "" : plural(total, "entrée", "entrées")
      + (filtered ? ` · ${plural(count, "affichée", "affichées")}` : "");

    for (const lv of ORDER) {
      const btn = levelBtns[lv];
      const on = shown.has(lv);
      btn.classList.toggle("on", on);
      btn.setAttribute("aria-checked", String(on));
      btn.dataset.tip = `${on ? "Masquer" : "Afficher"} : ${LEVELS[lv].label.toLowerCase()}`;
      btn.replaceChildren(ico(LEVELS[lv].icon, 13), h("span", { class: "log-level-label", text: LEVELS[lv].label }),
        h("span", { class: "log-count", text: (counts[lv] || 0).toLocaleString("fr-FR") }));
    }
    sourceBtn.classList.toggle("on", !!source);
    sourceBtn.replaceChildren(ico("list-filter", 14),
      h("span", { class: "log-source-label", text: source ? sourceName(source) : "Toutes les sources" }),
      ico("chevron-down", 13));
    sourceBtn.dataset.tip = source ? `Seulement « ${source} » : cliquez pour changer` : "Filtrer par source";

    empty.hidden = count > 0;
    if (!empty.hidden) {
      empty.replaceChildren(
        h("div", { class: "glyph" }, ico(total ? "list-filter" : "scroll-text", 22)),
        h("h3", { text: total ? "Aucune entrée ne correspond aux filtres" : "Journal vide" }),
        h("p", { text: total ? "Changez les niveaux, la source ou le texte recherché."
          : "Les actions de Boostache, ses avertissements et ses erreurs s'afficheront ici." }),
        total ? h("button", { class: "btn sm", type: "button", onClick: resetFilters }, "Tout afficher") : null);
    }
    renderBadge();
  }

  function renderBadge() {
    if (watching()) unseen = 0;
    ctx.setBadge("logs", unseen, "error");
  }

  // ── Actions ─────────────────────────────────
  const shownEntries = () => [...rows.values()].filter((r) => !r.el.hidden).map((r) => r.entry);

  function copyShown() {
    const list = shownEntries();
    if (list.length) ctx.copy(list.map((e) => entryText(e)).join("\n"), plural(list.length, "entrée copiée", "entrées copiées"));
  }

  async function copyReport() {
    const text = await api.logs_report({
      userAgent: navigator.userAgent,
      viewport: `${innerWidth} × ${innerHeight} px, échelle ${devicePixelRatio}`,
    });
    if (text) ctx.copy(text, "Rapport copié : collez-le dans une conversation pour analyser le problème");
  }

  async function filesMenu(anchor) {
    const list = (await api.logs_sessions()) || [];
    const current = list.some((s) => s.state === "current");
    const others = list.filter((s) => s.state !== "current").slice(0, 15);
    openMenuBelow([
      { label: "Ouvrir le journal de cette session", icon: "file-text", disabled: !current, onSelect: () => api.logs_open() },
      { label: "Ouvrir le dossier des journaux", icon: "folder-open", onSelect: () => api.logs_open_folder() },
      ...(others.length ? ["-", { section: "Sessions précédentes" }] : []),
      ...others.map((s) => {
        const d = new Date(s.start);
        const bad = s.state === "crash" || s.state === "abnormal";
        return { label: `${formatDay(d)}, ${formatHour(d)}`, icon: bad ? "triangle-alert" : "file-text",
          hint: [SESSION_STATES[s.state], sizeText(s.size)].filter(Boolean).join(" · "),
          onSelect: () => api.logs_open(s.name) };
      }),
    ], anchor, "right");
  }

  function clear() {
    api.logs_clear();
    rows.clear();
    box.replaceChildren();
    ORDER.forEach((lv) => { counts[lv] = 0; });
    unseen = missed = 0;
    follow = true;
    renderJump();
    render();
  }

  // Clic sur une ligne : ses détails (pas pendant une sélection de texte)
  box.addEventListener("click", (ev) => {
    const entryEl = ev.target.closest(".log-entry");
    if (!entryEl || ev.target.closest(".log-details") || !window.getSelection().isCollapsed) return;
    toggleDetails(rows.get(Number(entryEl.dataset.seq)));
  });

  box.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    const entryEl = ev.target.closest(".log-entry");
    const r = entryEl ? rows.get(Number(entryEl.dataset.seq)) : null;
    const sel = window.getSelection().toString();
    const first = [
      sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
      r ? { label: "Copier l'entrée", icon: "copy", onSelect: () => ctx.copy(entryText(r.entry), "Entrée copiée") } : null,
      r?.entry.details ? { label: r.el.classList.contains("open") ? "Masquer les détails" : "Afficher les détails",
        icon: "chevron-right", onSelect: () => toggleDetails(r) } : null,
      r ? (source === r.entry.src
        ? { label: "Toutes les sources", icon: "list-filter", onSelect: () => setSource(null) }
        : { label: `Seulement « ${sourceName(r.entry.src)} »`, icon: "list-filter", onSelect: () => setSource(r.entry.src) })
        : null,
    ].filter(Boolean);
    openMenu([
      ...first, ...(first.length ? ["-"] : []),
      { label: "Copier les entrées affichées", icon: "copy", disabled: !shownEntries().length, onSelect: copyShown },
      { label: "Copier le rapport de diagnostic", icon: "bug", onSelect: copyReport },
      "-",
      { label: "Effacer le journal", icon: "eraser", danger: true, disabled: !rows.size, onSelect: clear },
    ], ev.clientX, ev.clientY);
  });

  box.addEventListener("keydown", (ev) => {
    if (ev.key === "End") { ev.preventDefault(); toBottom(); }
  });

  // ── Événements ──────────────────────────────
  on("log", receive);
  on("window:shown", () => { windowVisible = true; renderBadge(); });
  on("window:hidden", () => { windowVisible = false; });

  for (const entry of state.logs || []) pending.push(entry);
  flush();
  // Erreurs du démarrage (et arrêt anormal de la session précédente) : pastille,
  // une fois la barre latérale construite
  unseen = counts.error;
  setTimeout(renderBadge, 0);

  return {
    el,
    onShow() {
      renderBadge();
      if (follow) toBottom();
    },
    focus() { box.focus(); },
  };
}
