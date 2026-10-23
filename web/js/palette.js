// Recherche dans tout Boostache (Ctrl+K) : sections et actions (ici), puis conversations,
// notes, messages arrivés, transcriptions, presse-papiers, calculs, documents et tâches
// (search.py). Entrée ouvre le résultat : la section s'affiche et sa vue le montre
// (view.reveal(target, words, query)), mots trouvés surlignés (markFound).
import { h, debounce } from "./dom.js";
import { ico } from "./ui.js";

const DELAY = 140;             // ms entre la frappe et la recherche
const FOUND_MS = 5000;         // ms de surlignage des mots trouvés dans la vue ouverte

// Libellés des groupes qui ne sont pas ceux de la section
const GROUP_LABELS = { assistant: "Messages arrivés", recorder: "Transcriptions" };

/** Texte replié (minuscules, sans accents), un caractère pour un : les positions restent celles du texte. */
export function fold(text) {
  return String(text || "").replace(/[^a-z0-9 ]/g, (c) => {
    if (c === "’" || c === "‘") return "'";
    const base = c.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
    return base.length === 1 ? base : c;
  });
}

const foldWords = (query) => [...new Set(fold(query).match(/[\p{L}\p{N}_]+/gu) || [])];

let foundTimer = null;
/** Surligne les mots trouvés dans root (CSS Custom Highlight API, sans toucher au DOM)
 *  quelques secondes ; rend la plage du premier, pour y faire défiler. */
export function markFound(root, words) {
  if (!root || !words?.length || !window.CSS?.highlights) return null;
  const ranges = [];
  let first = null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const folded = fold(node.data);
    for (const w of words) {
      for (let i = folded.indexOf(w); i >= 0; i = folded.indexOf(w, i + w.length)) {
        const r = new Range();
        r.setStart(node, i);
        r.setEnd(node, i + w.length);
        ranges.push(r);
        if (!first) first = r;
      }
    }
  }
  CSS.highlights.set("found", new Highlight(...ranges));
  clearTimeout(foundTimer);
  foundTimer = setTimeout(() => CSS.highlights.delete("found"), FOUND_MS);
  return first;
}

/** Élément montré : centré, bref éclairage. */
export function flashNode(node) {
  if (!node) return;
  node.scrollIntoView({ block: "center", behavior: "smooth" });
  node.classList.remove("found-flash");
  void node.offsetWidth;            // relance l'animation
  node.classList.add("found-flash");
  setTimeout(() => node.classList.remove("found-flash"), 1700);
}

/** Texte avec ses repères [début, fin] en <mark>. */
function marked(text, marks) {
  const out = [];
  let at = 0;
  for (const [a, b] of marks || []) {
    if (a > at) out.push(text.slice(at, a));
    out.push(h("mark", { text: text.slice(a, b) }));
    at = b;
  }
  out.push(text.slice(at));
  return out;
}

/** Repères des mots dans un libellé (sections, actions : filtrés ici). */
function localMarks(label, words) {
  const folded = fold(label);
  const spans = [];
  for (const w of words) {
    const i = folded.indexOf(w);
    if (i >= 0) spans.push([i, i + w.length]);
  }
  return spans.sort((x, y) => x[0] - y[0]).filter((s, i, all) => i === 0 || s[0] >= all[i - 1][1]);
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (days === 0) return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return "Hier";
  if (days < 7) return d.toLocaleDateString("fr-FR", { weekday: "long" });
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", ...(d.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
}

/**
 * sections() : [{ id, label, icon, shortcut }] ; actions() : [{ label, icon, keywords, run }] ;
 * open(section, target, words, query) : montre un résultat de search.py.
 */
export function createPalette(ctx, { sections, actions, open }) {
  const { api } = ctx;
  let box = null;
  let state = null;           // { query, items: [{ el, run }], active, seq }

  function close() {
    if (!box) return;
    box.backdrop.remove();
    document.removeEventListener("keydown", onKey, true);
    const previous = box.previous;
    box = null;
    state = null;
    previous?.focus?.();
  }

  function openPalette(initial = "") {
    if (box) { box.input.select(); return; }
    const input = h("input", { class: "palette-input", type: "text", spellcheck: "false", autocomplete: "off",
      placeholder: "Rechercher partout : conversations, notes, messages, transcriptions, calculs, documents…",
      "aria-label": "Rechercher dans Boostache" });
    const spinner = h("span", { class: "palette-spin", hidden: true }, ico("loader-circle", 15));
    const list = h("div", { class: "palette-list", role: "listbox" });
    const foot = h("div", { class: "palette-foot" },
      h("span", {}, h("kbd", { text: "↑" }), h("kbd", { text: "↓" }), " choisir"),
      h("span", {}, h("kbd", { text: "Entrée" }), " ouvrir"),
      h("span", {}, h("kbd", { text: "Échap" }), " fermer"),
      h("span", { class: "palette-foot-hint", text: "Sans accents ni majuscules, tous les mots" }));
    const panel = h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Rechercher" },
      h("div", { class: "palette-field" }, ico("search", 16), input, spinner), list, foot);
    // modal-backdrop : les sites intégrés (WhatsApp, Gmail) s'effacent le temps de la recherche
    const backdrop = h("div", { class: "modal-backdrop palette-backdrop" }, panel);
    backdrop.addEventListener("mousedown", (ev) => { if (ev.target === backdrop) close(); });
    document.body.append(backdrop);
    box = { backdrop, input, list, spinner, previous: document.activeElement };
    state = { query: "", items: [], active: 0, seq: 0, groups: [] };
    input.addEventListener("input", () => update(input.value));
    document.addEventListener("keydown", onKey, true);
    input.value = initial;
    update(initial);
    setTimeout(() => input.focus(), 20);
  }

  function onKey(ev) {
    if (!box) return;
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); close(); return; }
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      ev.stopPropagation();
      move(ev.key === "ArrowDown" ? 1 : -1);
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      ev.stopPropagation();
      state.items[state.active]?.run();
    }
  }

  function move(step) {
    if (!state.items.length) return;
    setActive((state.active + step + state.items.length) % state.items.length);
  }

  function setActive(i) {
    state.items[state.active]?.el.classList.remove("active");
    state.active = i;
    const it = state.items[i];
    if (!it) return;
    it.el.classList.add("active");
    it.el.scrollIntoView({ block: "nearest" });
  }

  function update(value) {
    state.query = value.trim();
    state.groups = [];
    render();
    if (state.query) remote();
    else box.spinner.hidden = true;
  }

  const remote = debounce(async () => {
    if (!box || !state.query) return;
    const seq = ++state.seq;
    const query = state.query;
    box.spinner.hidden = false;
    const res = await api.search_all(query);
    if (!box || seq !== state.seq || query !== state.query) return;
    box.spinner.hidden = true;
    state.groups = res?.groups || [];
    state.words = res?.words || [];
    render();
  }, DELAY);

  function item({ icon, title, titleMarks, snippet, marks, side, kbd, run }) {
    const el = h("div", { class: "palette-item", role: "option" },
      h("span", { class: "palette-icon" }, ico(icon, 15)),
      h("div", { class: "palette-text" },
        h("div", { class: "palette-title" }, ...marked(title, titleMarks)),
        snippet ? h("div", { class: "palette-snippet" }, ...marked(snippet, marks)) : null),
      side ? h("span", { class: "palette-side", text: side }) : null,
      kbd ? h("kbd", { text: kbd }) : null);
    const entry = { el, run: () => { close(); run(); } };
    el.addEventListener("mousemove", () => {
      const i = state.items.indexOf(entry);
      if (i !== state.active) setActive(i);
    });
    el.addEventListener("click", entry.run);
    state.items.push(entry);
    return el;
  }

  function group(label, icon, more, children) {
    return h("div", { class: "palette-group" },
      h("div", { class: "palette-group-head" }, ico(icon, 13), h("span", { text: label }),
        more ? h("span", { class: "muted", text: `· ${more} de plus` }) : null),
      children);
  }

  function render() {
    if (!box) return;
    state.items = [];
    const words = foldWords(state.query);
    const nodes = [];
    const all = sections();
    const byId = new Map(all.map((s) => [s.id, s]));
    // Actions et sections dont le libellé contient les mots (toutes, sans recherche)
    const matches = (label, extra = "") => words.every((w) => fold(`${label} ${extra}`).includes(w));
    const acts = actions().filter((a) => !words.length || matches(a.label, a.keywords));
    const secs = all.filter((s) => !words.length || matches(s.label, s.keywords));
    if (acts.length) {
      nodes.push(group("Actions", "zap", 0, acts.slice(0, words.length ? 4 : 8).map((a) => item({
        icon: a.icon, title: a.label, titleMarks: localMarks(a.label, words), run: a.run }))));
    }
    if (secs.length) {
      nodes.push(group("Sections", "layout-grid", 0, secs.slice(0, words.length ? 4 : 30).map((s) => item({
        icon: s.icon, title: s.label, titleMarks: localMarks(s.label, words),
        kbd: s.shortcut ? `Ctrl+${s.shortcut}` : "", run: () => open(s.id, null, [], "") }))));
    }
    // Lus maintenant : la palette est fermée (state remis à zéro) quand un résultat s'ouvre
    const found = state.words || words;
    const query = state.query;
    for (const g of state.groups) {
      const sec = byId.get(g.section);
      if (!sec) continue;
      nodes.push(group(GROUP_LABELS[g.section] || sec.label, sec.icon, g.more, g.items.map((it) => item({
        icon: sec.icon, title: it.title, titleMarks: it.title_marks, snippet: it.snippet, marks: it.marks,
        side: when(it.when), run: () => open(g.section, it.target, found, query) }))));
    }
    if (state.query && !state.groups.length && box.spinner.hidden && !acts.length && !secs.length) {
      nodes.push(h("div", { class: "palette-empty" }, ico("search-x", 18),
        h("span", { text: `Rien trouvé pour « ${state.query} ».` })));
    }
    box.list.replaceChildren(...nodes);
    state.active = 0;
    setActive(0);
  }

  return { open: openPalette, close, toggle: () => (box ? close() : openPalette()), isOpen: () => !!box };
}
