// Vue Documents : index local des dossiers choisis (rag.py). À gauche, l'arborescence
// (comme l'explorateur) avec l'état de l'indexation de chaque dossier et de chaque fichier,
// et la recherche ; à droite, le fichier ou le dossier choisi (résumé, points clés…) et les
// questions, auxquelles Ollama répond au fil de l'eau en citant ses sources [1], [2]…
import { h, plural, formatShort, parseDate } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, openModal, toast } from "../ui.js";
import { renderMarkdown } from "../markdown.js";
import { createComposer, createSuggestions, conversationSpeech } from "../composer.js";

const EXPANDED_KEY = "boostache.rag.expanded";     // dossiers dépliés (localStorage)
const SELECTED_KEY = "boostache.rag.selected";     // élément choisi
const REFRESH_MS = 700;        // dossiers dépliés relus au plus toutes les… pendant l'indexation
const TREE_MIN = 200;
const MAIN_MIN = 340;
const TREE_DEFAULT = 330;
const CHUNKS_SHOWN = 200;      // « Texte indexé » : passages affichés au plus

const KIND_ICONS = { pdf: "file-text", word: "file-text", sheet: "file-spreadsheet", slides: "presentation",
  mail: "mail", book: "file-text", code: "file-code", web: "file-code", text: "file-text" };
const TASKS = {
  summary:   { label: "Résumer",              title: "Résumé",       icon: "scroll-text" },
  keypoints: { label: "Points clés",          title: "Points clés",  icon: "list-checks" },
  explain:   { label: "Expliquer",            title: "Explication",  icon: "lightbulb" },
  overview:  { label: "Présenter le dossier", title: "Présentation", icon: "library" },
};
const FILE_TASKS = ["summary", "keypoints", "explain"];
// Suggestions au-dessus du champ, gardées quand des questions ont été posées
const FOLLOW_UPS = [
  { icon: "arrow-down", label: "Développer", ask: "Développe ta dernière réponse." },
  { icon: "lightbulb", label: "Plus simplement", ask: "Explique plus simplement ta dernière réponse." },
  { icon: "text-select", label: "Citer les passages", ask: "Cite mot pour mot les passages qui appuient ta dernière réponse." },
];
const STARTERS = [
  { icon: "search", label: "Quels documents parlent de…", fill: "Quels documents parlent de … ?" },
  { icon: "message-square", label: "Que disent mes documents sur…", fill: "Que disent mes documents sur … ?" },
];
const STOP = new Set(("les des une pour dans avec sans par sur que qui quoi quel quelle quels quelles est sont "
  + "aux ces cet cette mais donc comme plus moins tout tous toute toutes leur leurs nos vos notre votre "
  + "the and for with from this that what which").split(" "));
const BUSY = new Set(["searching", "reading", "thinking"]);
const READ_ALL = "rag:all";              // lecture de toutes les questions (bouton Lire)
const sourceOf = (id) => `rag:${id}`;    // lecture d'une réponse

// ─────────────────────────────────────────────
//  Mise en forme
// ─────────────────────────────────────────────
function bytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} o`;
  if (n < 1e6) return `${Math.round(n / 1024)} Ko`;
  const [v, unit] = n < 1e9 ? [n / 1e6, "Mo"] : [n / 1e9, "Go"];
  return `${v.toLocaleString("fr-FR", { maximumFractionDigits: v < 10 ? 1 : 0 })} ${unit}`;
}

function duration(s) {
  if (s < 60) return `${Math.max(5, Math.round(s / 5) * 5)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;
}

const fold = (s) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
/** Clé d'un chemin comme rag._key (minuscules, barres obliques inverses). */
const keyOf = (path) => String(path || "").replace(/\//g, "\\").toLowerCase();
const fullName = (name) => (name && !name.split("/").pop().includes(":") ? `${name}:latest` : name);
const noCitations = (text) => String(text || "").replace(/\s?\[\d+(?:[,;\s]+\d+)*\]/g, "");

/** Mots de la recherche, comme rag._fts_query : sans accents, les longs mots par leur début. */
function terms(query) {
  const words = (String(query || "").toLowerCase().match(/[\p{L}\p{N}]+/gu) || [])
    .filter((w) => w.length > 2 && !STOP.has(w)).map(fold)
    .map((w) => (w.length >= 6 && !/^\d+$/.test(w) ? w.slice(0, Math.max(5, w.length - 2)) : w));
  return [...new Set(words)];
}

/** Texte avec les mots cherchés surlignés ; max : extrait de max caractères autour du premier. */
function highlight(text, query, max = 0) {
  text = String(text || "");
  const ts = terms(query);
  const ranges = [];
  if (ts.length) {
    let folded = "";
    const map = [];
    for (let i = 0; i < text.length; i++) {
      const f = fold(text[i]);
      for (let j = 0; j < f.length; j++) map.push(i);
      folded += f;
    }
    const letter = (c) => !!c && /[\p{L}\p{N}]/u.test(c);
    for (const t of ts) {
      for (let idx = folded.indexOf(t); idx >= 0; idx = folded.indexOf(t, idx + t.length)) {
        // Comme l'index : un mot qui commence par le terme, surligné en entier
        const a = map[idx];
        if (letter(text[a - 1])) continue;
        let b = map[idx + t.length - 1] + 1;
        while (letter(text[b])) b++;
        ranges.push([a, b]);
      }
    }
    ranges.sort((a, b) => a[0] - b[0]);
  }
  let start = 0;
  let end = text.length;
  if (max && text.length > max) {
    start = Math.max(0, (ranges[0]?.[0] ?? 0) - Math.floor(max / 4));
    end = Math.min(text.length, start + max);
  }
  const out = start > 0 ? ["…"] : [];
  let pos = start;
  for (const [a, b] of ranges) {
    if (b <= pos || a >= end) continue;
    out.push(text.slice(pos, Math.max(a, pos)), h("mark", { text: text.slice(Math.max(a, pos), Math.min(b, end)) }));
    pos = Math.min(b, end);
  }
  out.push(text.slice(pos, end));
  if (end < text.length) out.push("…");
  return out;
}

/** replaceChildren sans les enfants absents (null y deviendrait le texte « null »). */
const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));

function loadSet(key) {
  try { return new Set(JSON.parse(localStorage.getItem(key) || "[]")); } catch { return new Set(); }
}
function saveSet(key, set) {
  try { localStorage.setItem(key, JSON.stringify([...set])); } catch { /* stockage indisponible */ }
}
function loadText(key) {
  try { return localStorage.getItem(key) || ""; } catch { return ""; }
}
function saveText(key, value) {
  try { localStorage.setItem(key, value || ""); } catch { /* stockage indisponible */ }
}

/** Avancement d'un dossier : fichiers traités, en attente (lecture ou vecteurs), illisibles. */
function progressOf(stats, vectors) {
  const total = stats?.total || 0;
  const waiting = (stats?.pending || 0) + (vectors ? stats?.text || 0 : 0);
  return { total, waiting, done: total - waiting, errors: stats?.error || 0, chunks: stats?.chunks || 0,
    pct: total ? (total - waiting) / total : 1 };
}

function statsText(stats, vectors) {
  const p = progressOf(stats, vectors);
  if (!p.total) return "Aucun document pris en charge";
  const parts = [`${p.done.toLocaleString("fr-FR")} fichier${p.done > 1 ? "s" : ""} traité${p.done > 1 ? "s" : ""} sur ${p.total.toLocaleString("fr-FR")}`];
  if (p.waiting) parts.push(`${p.waiting.toLocaleString("fr-FR")} en attente`);
  if (p.errors) parts.push(plural(p.errors, "illisible", "illisibles"));
  if (p.chunks) parts.push(plural(p.chunks, "passage", "passages"));
  return parts.join(" · ");
}

export function createRagView(ctx, state) {
  const { api, on, store } = ctx;
  const init = state.rag || {};
  const S = {
    st: { folders: [], totals: {}, embed: {}, ...init },
    history: init.history || [],
    models: state.chat?.models || [],
    expanded: loadSet(EXPANDED_KEY),
    selected: null,          // nœud choisi dans l'arborescence
    scope: null,             // { path, name, dir } : portée des questions (null : tous les dossiers)
    search: null,            // { query, scope, loading, res }
    info: null,              // détails du fichier choisi (rag_file_info)
    pulls: new Map(),        // téléchargements de modèles (section Ollama)
    windowVisible: !!state.visible,
    currentKey: "",
  };
  const nodes = new Map();   // clé → nœud
  let roots = [];
  const answers = new Map(); // id → réponse affichée
  const dirty = new Set();   // dossiers dépliés à relire
  let refreshTimer = null;

  const vectors = () => !!(S.st.embed?.ready && S.st.embed?.online);

  // ── Barre du haut ───────────────────────────
  const sub = h("span", { class: "view-sub" });
  const statusPill = h("span", { class: "rag-status" });
  const pauseBtn = iconButton("pause", "Mettre l'indexation en pause", () => togglePause(), { size: 15 });
  const menuBtn = iconButton("sliders-horizontal", "Indexation et modèles", (ev) => settingsMenu(ev.currentTarget), { size: 15 });
  const addBtn = h("button", { class: "btn sm", type: "button", onClick: () => addFolder() },
    ico("folder-plus", 14), h("span", { class: "rag-add-label", text: "Ajouter un dossier" }));
  const head = h("header", { class: "view-head" },
    h("span", { class: "view-title", text: "Documents" }), sub, h("div", { class: "head-spacer" }),
    statusPill, pauseBtn, menuBtn, addBtn);

  // ── Colonne de gauche : recherche, arborescence, avancement ──
  const searchInput = h("input", { type: "text", placeholder: "Rechercher dans les documents", spellcheck: "false",
    "aria-label": "Rechercher dans les documents",
    onKeydown: (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); runSearch(); }
      if (ev.key === "Escape" && (searchInput.value || S.search)) { ev.preventDefault(); ev.stopPropagation(); closeSearch(true); }
      if (ev.key === "ArrowDown" && S.search) { ev.preventDefault(); results.querySelector(".rag-hit")?.focus(); }
    },
    onInput: () => { if (!searchInput.value.trim() && S.search) closeSearch(false); } });
  const searchBox = h("div", { class: "search rag-search", dataset: { tip: "Entrée : chercher les passages et les noms de fichiers" } },
    ico("search", 14), searchInput);
  const embedCard = h("div", { class: "rag-embed", hidden: true });
  const tree = h("div", { class: "rag-tree", role: "tree", "aria-label": "Dossiers indexés" });
  const results = h("div", { class: "rag-results", hidden: true });
  const empty = h("div", { class: "empty rag-empty", hidden: true, dataset: { drop: "rag" } },
    h("div", { class: "glyph" }, ico("folder-plus", 22)),
    h("h3", { text: "Aucun dossier" }),
    h("p", { text: "Choisissez les dossiers à indexer : leurs documents (texte, PDF, Word, Excel, PowerPoint, "
      + "e-mails…) sont lus et rangés dans un index local, pour y chercher et leur poser des questions." }),
    h("div", { class: "empty-actions" },
      h("button", { class: "btn primary sm", type: "button", onClick: () => addFolder() }, ico("folder-plus", 14), "Ajouter un dossier")),
    h("div", { class: "hints" }, h("span", {}, ico("arrow-down", 13), "ou glissez un dossier ici")));
  const unavailable = h("div", { class: "empty rag-empty", hidden: true },
    h("div", { class: "glyph" }, ico("triangle-alert", 22)), h("h3", { text: "Index indisponible" }), h("p"));
  const footBar = h("i");
  const footText = h("div", { class: "rag-foot-text" });
  const footNote = h("div", { class: "rag-foot-note" });
  const foot = h("footer", { class: "rag-foot" }, h("div", { class: "rag-bar" }, footBar), footText, footNote);
  const side = h("aside", { class: "rag-side", dataset: { drop: "rag" } },
    h("div", { class: "rag-side-head" }, searchBox), embedCard, tree, results, empty, unavailable, foot);

  // ── Séparateur ─────────────────────────────
  const splitter = h("div", { class: "rag-splitter", role: "separator", tabindex: "0", "aria-orientation": "vertical",
    "aria-label": "Largeur de l'arborescence", dataset: { tip: "Glisser pour élargir · double-clic : largeur par défaut" } });

  // ── Colonne de droite : élément choisi, questions ──
  const detail = h("div", { class: "rag-detail", hidden: true });
  const intro = h("div", { class: "rag-intro" });
  const thread = h("div", { class: "asst-thread rag-thread" });
  const clearBtn = h("button", { class: "btn ghost sm", type: "button", onClick: () => api.rag_clear() },
    ico("trash-2", 14), "Effacer");
  const threadHead = h("div", { class: "asst-block-head rag-thread-head" },
    h("span", { class: "asst-block-title", text: "Questions" }), h("div", { class: "head-spacer" }), clearBtn);
  const scroller = h("div", { class: "rag-scroll" }, h("div", { class: "rag-scroll-inner" }, intro, threadHead, thread));

  const scopeBtn = h("button", { class: "rag-scope", type: "button", onClick: (ev) => scopeMenu(ev) });
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle des réponses",
    dataset: { tip: "Modèle qui répond aux questions et résume les documents" },
    onClick: (ev) => openMenuBelow(answerModelItems(), ev.currentTarget) },
    ico("sparkles", 14), modelName, ico("chevron-down", 14));
  let readingMode = "answers";   // ce que suit la lecture de toutes les questions
  const composer = createComposer(ctx, {
    label: "Question sur les documents", maxlength: 4000, maxHeight: 200,
    tools: [iconButton("mic", "Dictée vocale", () => { input.focus(); api.dictate(); }, { kbd: "Win+H" }),
      scopeBtn, modelBtn],
    speakKey: "rag_speak",
    // Activée pendant une réponse : elle est lue tout de suite
    onAutoRead: (on) => { const entry = busyEntry(); if (on && entry) autoRead(entry); },
    read: {
      source: READ_ALL,
      match: (source) => source === READ_ALL || (source.startsWith("rag:") && answers.has(source.slice(4))),
      speak: (mode) => {
        readingMode = mode;
        ctx.speak(threadSpeech(mode), READ_ALL, true, !!busyEntry());
      },
    },
    onSend: () => ask(),
    onStop: () => api.rag_stop(),
    sendTip: "Demander", stopTip: "Arrêter la réponse",
  });
  const { input } = composer;
  const suggest = createSuggestions(composer, (text) => ask(text));
  const askBar = h("div", { class: "composer-wrap" }, suggest.el, composer.el);
  const main = h("div", { class: "rag-main" }, detail, scroller, askBar);

  const body = h("div", { class: "rag-body" }, side, splitter, main);
  const el = h("section", {}, head, body);
  setTreeWidth(Number(store.settings.rag_tree_width) || TREE_DEFAULT);

  // ─────────────────────────────────────────────
  //  Arborescence
  // ─────────────────────────────────────────────
  function makeNode(entry, depth, parent) {
    const node = { key: entry.key, path: entry.path, name: entry.name, dir: !!entry.dir, depth, parent, entry,
      expanded: false, loaded: false, loading: null, error: "", children: [], more: 0, el: null };
    nodes.set(node.key, node);
    if (node.dir && S.expanded.has(node.key)) {
      node.expanded = true;
      load(node);
    }
    return node;
  }

  function dropNode(node) {
    for (const k of node.children) { const c = nodes.get(k); if (c) dropNode(c); }
    nodes.delete(node.key);
    if (S.selected === node) select(null, { scope: false });
    if (S.scope && keyOf(S.scope.path) === node.key) setScope(null);
  }

  /** Dossiers de la liste (état reçu de Python) → nœuds racines. */
  function syncRoots() {
    const before = roots.map((r) => r.key).join("|");
    const keys = new Set(S.st.folders.map((f) => f.key));
    for (const r of roots) if (!keys.has(r.key)) dropNode(r);
    roots = S.st.folders.map((f) => {
      const entry = { ...f, dir: true, root: true };
      const node = nodes.get(f.key);
      if (node) { node.entry = entry; return node; }
      return makeNode(entry, 0, null);
    });
    return before !== roots.map((r) => r.key).join("|");
  }

  async function load(node) {
    if (node.loading) return node.loading;
    node.loading = (async () => {
      const res = await api.rag_children(node.path);
      if (!res || !nodes.has(node.key)) return;
      const before = node.children.join("|") + node.error;
      if (!res.ok) {
        for (const k of node.children) { const c = nodes.get(k); if (c) dropNode(c); }
        node.children = [];
        node.error = res.error || "Dossier illisible";
      } else {
        node.error = "";
        const keep = new Set(res.entries.map((e) => e.key));
        for (const k of node.children) if (!keep.has(k)) { const c = nodes.get(k); if (c) dropNode(c); }
        node.children = res.entries.map((e) => {
          const child = nodes.get(e.key);
          if (child && child.parent === node.key) {
            child.entry = e;
            child.name = e.name;
            return child.key;
          }
          return makeNode(e, node.depth + 1, node.key).key;
        });
        node.more = res.more || 0;
      }
      node.loaded = true;
      if (before !== node.children.join("|") + node.error) renderTree();
      else for (const k of node.children) updateRow(nodes.get(k));
      if (S.selected && S.selected.parent === node.key) renderDetail();
    })();
    try { await node.loading; } finally { node.loading = null; }
  }

  function toggle(node, open = !node.expanded) {
    if (!node?.dir || node.expanded === open) return;
    node.expanded = open;
    if (open) S.expanded.add(node.key); else S.expanded.delete(node.key);
    saveSet(EXPANDED_KEY, S.expanded);
    if (open && !node.loaded) load(node);
    renderTree();
  }

  function visibleRows() {
    const out = [];
    const walk = (node) => {
      out.push(node);
      if (!node.dir || !node.expanded) return;
      const depth = node.depth + 1;
      if (!node.loaded) out.push({ placeholder: "Lecture du dossier…", depth });
      else if (node.error) out.push({ placeholder: node.error, depth, error: true });
      else if (!node.children.length) out.push({ placeholder: "Dossier vide", depth });
      for (const k of node.children) { const c = nodes.get(k); if (c) walk(c); }
      if (node.more) out.push({ placeholder: `… et ${plural(node.more, "autre élément", "autres éléments")}`, depth });
    };
    roots.forEach(walk);
    return out;
  }

  function renderTree() {
    tree.replaceChildren(...visibleRows().map((n) => (n.placeholder
      ? h("div", { class: `rag-row placeholder${n.error ? " error" : ""}`, style: { "--depth": n.depth } },
        h("span", { class: "rag-twisty" }), h("span", { class: "rag-name", text: n.placeholder }))
      : rowOf(n))));
    // Une seule rangée dans l'ordre de tabulation : la rangée choisie, sinon la première
    if (!S.selected?.el?.isConnected) {
      const first = tree.querySelector(".rag-row:not(.placeholder)");
      if (first) first.tabIndex = 0;
    }
    empty.hidden = roots.length > 0 || !S.st.available;
    unavailable.hidden = !!S.st.available;
    unavailable.querySelector("p").textContent = S.st.error || "";
    tree.hidden = !roots.length || !!S.search;
  }

  function focusTree() {
    const row = S.selected?.el?.isConnected ? S.selected.el : tree.querySelector(".rag-row:not(.placeholder)");
    if (row) row.focus();
    else searchInput.focus();
  }

  function rowOf(node) {
    if (!node.el) node.el = h("div", { class: "rag-row", role: "treeitem", dataset: { key: node.key } });
    updateRow(node);
    return node.el;
  }

  function fileState(e) {
    if (e.excluded) return { icon: "eye-off", cls: "muted", tip: "Exclu de l'index" };
    if (e.ignored) return { icon: "minus", cls: "muted", tip: "Dans un dossier caché ou technique : non indexé" };
    if (!e.supported) return { icon: "", cls: "", tip: e.reason || "Format non pris en charge" };
    if (e.key === S.currentKey && S.st.phase === "indexing") return { icon: "loader-circle", cls: "spin", tip: "Indexation en cours…" };
    const n = plural(e.chunks || 0, "passage", "passages");
    const part = e.truncated ? " (début du fichier seulement : il est très long)" : "";
    switch (e.status) {
      case "indexed": return { icon: "check", cls: "ok", tip: `Indexé · ${n}${part}` };
      case "text": return vectors()
        ? { icon: "check", cls: "half", tip: `Texte indexé (${n}) · vecteurs en attente` }
        : { icon: "check", cls: "ok", tip: `Indexé, recherche par mots · ${n}${part}` };
      case "pending": return { icon: "clock", cls: "muted", tip: "En attente d'indexation" };
      case "error": return { icon: "triangle-alert", cls: "error", tip: e.error || "Illisible" };
      case "empty": return { icon: "minus", cls: "muted", tip: e.error || "Aucun texte" };
      case "skipped": return { icon: "minus", cls: "muted", tip: e.error || "Ignoré" };
      default: return { icon: "clock", cls: "muted", tip: "Pas encore vu par l'indexation" };
    }
  }

  function badge(node) {
    const e = node.entry;
    if (!node.dir) {
      const st = fileState(e);
      return h("span", { class: `rag-state ${st.cls}`, dataset: { tip: st.tip } }, st.icon ? ico(st.icon, 13) : null);
    }
    if (e.missing) return h("span", { class: "rag-state error", dataset: { tip: "Dossier introuvable (disque débranché ?) : son index est gardé" } }, ico("triangle-alert", 13));
    if (e.excluded) return h("span", { class: "rag-state muted", dataset: { tip: "Exclu de l'index" } }, ico("eye-off", 13));
    if (e.ignored) return h("span", { class: "rag-state muted", dataset: { tip: "Dossier caché ou technique : non indexé" } }, ico("minus", 13));
    const p = progressOf(e.stats, vectors());
    if (!p.total) return h("span", { class: "rag-count muted", dataset: { tip: "Aucun document pris en charge" } }, "—");
    const tip = statsText(e.stats, vectors());
    if (!p.waiting) {
      return h("span", { class: `rag-count${p.errors ? "" : " ok"}`, dataset: { tip } },
        ico(p.errors ? "triangle-alert" : "check", 12), p.total.toLocaleString("fr-FR"));
    }
    return h("span", { class: "rag-count busy", dataset: { tip } },
      h("span", { class: "rag-mini" }, h("i", { style: { width: `${Math.round(p.pct * 100)}%` } })),
      `${p.done.toLocaleString("fr-FR")}/${p.total.toLocaleString("fr-FR")}`);
  }

  function updateRow(node) {
    const row = node?.el;
    if (!row) return;
    const e = node.entry;
    const dim = e.excluded || e.ignored || (!node.dir && !e.supported) || e.missing;
    const selected = node === S.selected;
    row.className = `rag-row ${node.dir ? "dir" : "file"}${selected ? " selected" : ""}${dim ? " dim" : ""}${node.depth ? "" : " root"}`;
    row.style.setProperty("--depth", node.depth);
    row.setAttribute("aria-level", String(node.depth + 1));
    row.setAttribute("aria-selected", String(selected));
    if (node.dir) row.setAttribute("aria-expanded", String(node.expanded));
    row.tabIndex = selected ? 0 : -1;
    const glyph = node.dir ? (node.expanded ? "folder-open" : "folder") : KIND_ICONS[e.kind] || "file";
    row.replaceChildren(
      h("span", { class: "rag-twisty" }, node.dir ? ico(node.expanded ? "chevron-down" : "chevron-right", 14) : null),
      h("span", { class: `rag-ico${node.dir ? " dir" : ""}` }, ico(glyph, 15)),
      h("span", { class: "rag-name", text: node.name }),
      badge(node));
  }

  function visibleNodes() {
    return visibleRows().filter((n) => !n.placeholder);
  }

  // ── Choix d'un élément ──────────────────────
  function select(node, { focus = false, scope = true } = {}) {
    const prev = S.selected;
    S.selected = node || null;
    if (prev && prev !== node) updateRow(prev);
    if (node) {
      updateRow(node);
      if (focus && node.el?.isConnected) {
        node.el.focus({ preventScroll: true });
        node.el.scrollIntoView({ block: "nearest" });
      }
    }
    saveText(SELECTED_KEY, node ? node.path : "");
    // Portée des questions : le dossier ou le fichier choisi (s'il se lit)
    if (scope && node && (node.dir ? !node.entry.missing : node.entry.supported && !node.entry.excluded && !node.entry.ignored)) {
      setScope({ path: node.path, name: node.name, dir: node.dir });
    }
    S.info = null;
    renderDetail();
    if (node && !node.dir && node.entry.supported) fetchInfo(node);
  }

  async function fetchInfo(node) {
    const info = await api.rag_file_info(node.path);
    if (S.selected !== node || !info) return;
    S.info = info;
    renderDetail();
  }

  /** Montre un fichier ou un dossier dans l'arborescence (dossiers parents dépliés). */
  async function reveal(path, { focus = true } = {}) {
    const key = keyOf(path);
    const root = roots.find((r) => key === r.key || key.startsWith(r.key.replace(/\\$/, "") + "\\"));
    if (!root) { if (focus) toast("Hors des dossiers indexés.", "info"); return null; }
    if (S.search) closeSearch(false);
    let node = root;
    const parts = key.slice(root.key.replace(/\\$/, "").length).split("\\").filter(Boolean);
    for (const part of parts) {
      if (!node.expanded) { node.expanded = true; S.expanded.add(node.key); }
      if (!node.loaded) await load(node);
      const child = nodes.get(`${node.key.replace(/\\$/, "")}\\${part}`);
      if (!child) break;
      node = child;
    }
    saveSet(EXPANDED_KEY, S.expanded);
    renderTree();
    select(node, { focus });
    if (!focus) node.el?.scrollIntoView({ block: "nearest" });
    return node;
  }

  // ── Événements de l'arborescence ───────────
  const nodeOf = (target) => nodes.get(target.closest?.(".rag-row")?.dataset.key || "");
  tree.addEventListener("click", (ev) => {
    const node = nodeOf(ev.target);
    if (!node) return;
    if (ev.target.closest(".rag-twisty") && node.dir) { toggle(node); select(node, { focus: true }); return; }
    select(node, { focus: true });
  });
  tree.addEventListener("dblclick", (ev) => {
    const node = nodeOf(ev.target);
    if (!node || ev.target.closest(".rag-twisty")) return;
    if (node.dir) toggle(node);
    else openFile(node.path);
  });
  tree.addEventListener("contextmenu", (ev) => {
    const node = nodeOf(ev.target);
    if (!node) return;
    ev.preventDefault();
    select(node, { focus: true });
    openMenu(nodeMenu(node), ev.clientX, ev.clientY);
  });
  tree.addEventListener("keydown", (ev) => {
    const list = visibleNodes();
    if (!list.length) return;
    const node = S.selected && list.includes(S.selected) ? S.selected : null;
    const i = node ? list.indexOf(node) : -1;
    const move = (n) => { if (n) select(n, { focus: true }); };
    switch (ev.key) {
      case "ArrowDown": move(list[Math.min(list.length - 1, i + 1)]); break;
      case "ArrowUp": move(list[Math.max(0, i - 1)]); break;
      case "Home": move(list[0]); break;
      case "End": move(list[list.length - 1]); break;
      case "ArrowRight":
        if (node?.dir && !node.expanded) toggle(node, true);
        else if (node?.dir && node.children.length) move(nodes.get(node.children[0]));
        break;
      case "ArrowLeft":
        if (node?.dir && node.expanded) toggle(node, false);
        else if (node?.parent) move(nodes.get(node.parent));
        break;
      case "Enter":
        if (node?.dir) toggle(node); else if (node) openFile(node.path);
        break;
      case "F10":
        if (!ev.shiftKey) return;
      // fallthrough : Maj+F10 ouvre le menu contextuel
      case "ContextMenu": {
        if (!node?.el) return;
        const r = node.el.getBoundingClientRect();
        openMenu(nodeMenu(node), r.left + 24, r.bottom);
        break;
      }
      default: return;
    }
    ev.preventDefault();
    ev.stopPropagation();
  });

  // ─────────────────────────────────────────────
  //  Menus et actions
  // ─────────────────────────────────────────────
  function nodeMenu(node) {
    const e = node.entry;
    const ollama = !!store.ollama;
    const noOllama = ollama ? "" : "Ollama ne répond pas";
    const common = [
      { label: node.dir ? "Ouvrir dans l'explorateur" : "Ouvrir", icon: "external-link", hint: node.dir ? "" : "Double-clic",
        onSelect: () => (node.dir ? api.rag_reveal(node.path) : openFile(node.path)) },
      node.dir ? null : { label: "Afficher dans l'explorateur", icon: "folder-open", onSelect: () => api.rag_reveal(node.path) },
      { label: "Copier le chemin", icon: "copy", onSelect: () => ctx.copy(node.path, "Chemin copié") },
    ];
    if (!node.dir && (!e.supported || e.ignored)) {
      return [{ section: e.ignored ? "Dans un dossier non indexé" : e.reason || "Format non pris en charge" }, ...common];
    }
    const readable = node.dir ? !e.missing : !e.excluded;
    const tasks = node.dir
      ? [{ label: TASKS.overview.label, icon: TASKS.overview.icon, hint: noOllama, disabled: !ollama || !readable,
        onSelect: () => runTask("overview", node) }]
      : FILE_TASKS.map((k) => ({ label: TASKS[k].label, icon: TASKS[k].icon, hint: noOllama, disabled: !ollama || !readable,
        onSelect: () => runTask(k, node) }));
    return [
      ...tasks,
      { label: node.dir ? "Poser une question sur ce dossier…" : "Poser une question sur ce fichier…", icon: "message-square",
        disabled: !ollama || !readable, onSelect: () => { setScope({ path: node.path, name: node.name, dir: node.dir }); input.focus(); } },
      node.dir ? null : { label: "Voir le texte indexé", icon: "file-search", disabled: !["text", "indexed"].includes(e.status),
        onSelect: () => showChunks(node.path) },
      "-",
      ...common,
      "-",
      { label: node.dir ? "Réindexer le dossier" : "Réindexer", icon: "refresh-cw", disabled: e.excluded || e.ignored,
        onSelect: () => reindex(node) },
      node.depth ? { label: e.excluded ? "Inclure dans l'index" : "Exclure de l'index", icon: e.excluded ? "eye" : "eye-off",
        disabled: !!e.ignored, onSelect: () => exclude(node, !e.excluded) } : null,
      node.depth ? null : { label: "Retirer de la liste…", icon: "folder-x", danger: true, onSelect: () => confirmRemove(node) },
    ];
  }

  async function addFolder() {
    const res = await api.rag_add_folder();
    if (!res) return;
    if (!res.ok) { toast(res.error || "Dossier non ajouté.", "error"); return; }
    S.expanded.add(keyOf(res.path));
    saveSet(EXPANDED_KEY, S.expanded);
    await refreshState();
    reveal(res.path);
    toast(res.merged?.length ? `${res.path} ajouté (il contient ${plural(res.merged.length, "dossier déjà indexé", "dossiers déjà indexés")})`
      : "Dossier ajouté : indexation en cours");
  }

  function confirmRemove(node) {
    const n = node.entry.stats?.total || 0;
    openModal({
      title: `Retirer « ${node.name} » ?`,
      width: 460,
      body: h("p", { class: "rag-confirm", text: `Son index (${plural(n, "fichier", "fichiers")}) est effacé ; `
        + "les fichiers eux-mêmes ne sont pas touchés. Vous pourrez l'ajouter de nouveau." }),
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: "Retirer", icon: "folder-x", kind: "danger", onClick: async () => {
          await api.rag_remove_folder(node.path);
          await refreshState();
          toast(`« ${node.name} » retiré de l'index`);
        } },
      ],
    });
  }

  async function exclude(node, excluded) {
    const res = await api.rag_exclude(node.path, excluded);
    if (!res?.ok) { toast(res?.error || "Impossible.", "error"); return; }
    toast(excluded ? `« ${node.name} » exclu de l'index` : `« ${node.name} » de nouveau indexé`);
    const parent = nodes.get(node.parent);
    if (parent) load(parent);
  }

  async function reindex(node) {
    const res = await api.rag_reindex(node.path);
    if (res?.ok) toast(node.dir ? `${plural(res.count, "fichier", "fichiers")} à relire` : `${node.name} va être relu`);
  }

  async function openFile(path) {
    const ok = await api.rag_open(path);
    if (ok === false) toast("Ouverture impossible.", "error");
  }

  async function togglePause() {
    const st = await api.rag_pause(!S.st.paused);
    if (st) { S.st = st; renderStatus(); }
  }

  async function refreshState() {
    const st = await api.rag_state();
    if (!st) return;
    S.st = { ...S.st, ...st };
    onState();
  }

  function settingsMenu(anchor) {
    const e = S.st.embed || {};
    const installed = e.installed || [];
    const suggested = (e.suggested || []).filter((s) => !installed.includes(fullName(s.name)));
    const totals = S.st.totals || {};
    openMenuBelow([
      { section: "Indexation" },
      { label: S.st.paused ? "Reprendre l'indexation" : "Mettre en pause", icon: S.st.paused ? "play" : "pause", onSelect: togglePause },
      { label: "Rechercher les changements", icon: "refresh-cw", hint: S.st.watching ? "aussi en continu" : "",
        onSelect: () => { api.rag_rescan(); toast("Recherche des fichiers nouveaux ou modifiés…", "info"); } },
      { label: "Tout réindexer…", icon: "rotate-ccw", disabled: !totals.total, onSelect: confirmReindexAll },
      "-",
      { section: "Modèles" },
      { label: `Embeddings : ${e.model || "aucun"}`, icon: "database", submenu: [
        ...installed.map((m) => ({ label: m, checked: m === e.model, onSelect: () => setEmbedModel(m) })),
        ...(installed.length ? ["-"] : []),
        ...suggested.map((s) => ({ label: `Télécharger ${s.name}`, icon: "download", hint: s.size, disabled: !store.ollama,
          onSelect: () => pull(s.name) })),
        { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
      ] },
      { label: `Réponses : ${S.st.model || "aucun"}`, icon: "sparkles", submenu: answerModelItems(false) },
    ], anchor, "right");
  }

  function confirmReindexAll() {
    const n = S.st.totals?.total || 0;
    openModal({
      title: "Tout réindexer ?",
      width: 460,
      body: h("p", { class: "rag-confirm", text: `Les ${plural(n, "fichier", "fichiers")} sont relus et leurs vecteurs recalculés `
        + "(utile après une mise à jour de Boostache ou si des résultats semblent anciens). L'index actuel sert jusqu'à leur relecture." }),
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: "Tout réindexer", icon: "rotate-ccw", kind: "primary", onClick: async () => { await api.rag_reindex(null); } },
      ],
    });
  }

  async function setEmbedModel(name) {
    const st = await api.rag_set_embed_model(name);
    if (st) { S.st = { ...S.st, ...st }; onState(); }
    toast(`Modèle d'embeddings : ${name} — les vecteurs sont recalculés si besoin`, "info");
  }

  function answerModelItems(withHead = true) {
    const current = S.st.model;
    return [
      withHead ? { section: "Modèle des réponses" } : null,
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === current, onSelect: async () => {
          await ctx.saveSetting("rag_model", m);
          refreshState();
        } }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  async function pull(name) {
    const res = await api.rag_pull(name);
    if (!res?.ok) { toast(res?.error || "Téléchargement impossible.", "error"); return; }
    S.pulls.set(res.name, res.pull);
    renderEmbed();
    toast(`Téléchargement de ${name}…`, "info");
  }

  // ─────────────────────────────────────────────
  //  Recherche
  // ─────────────────────────────────────────────
  /** Recherche dans le dossier de la portée (un fichier seul : partout), ou partout (everywhere). */
  async function runSearch(everywhere = false) {
    const query = searchInput.value.trim();
    if (!query) { closeSearch(false); return; }
    const scope = !everywhere && S.scope?.dir ? S.scope : null;
    S.search = { query, scope, loading: true, res: null };
    renderSearch();
    const res = await api.rag_search(query, scope?.path || null);
    if (S.search?.query !== query) return;
    if (!res?.ok) { toast(res?.error || "Recherche impossible.", "error"); S.search = null; renderSearch(); return; }
    S.search = { query, scope, loading: false, res };
    renderSearch();
  }

  function closeSearch(clear) {
    if (clear) searchInput.value = "";
    S.search = null;
    renderSearch();
  }

  function hitEl(p, query) {
    const glyph = KIND_ICONS[p.kind] || "file";
    const item = h("div", { class: "rag-hit", tabindex: "0", role: "button",
      onClick: () => openPassage({ question: query, kind: "ask" }, p),
      onKeydown: (ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); openPassage({ question: query, kind: "ask" }, p); }
        if (ev.key === "ArrowDown") { ev.preventDefault(); item.nextElementSibling?.focus(); }
        if (ev.key === "ArrowUp") { ev.preventDefault(); (item.previousElementSibling?.classList.contains("rag-hit") ? item.previousElementSibling : searchInput).focus(); }
      },
      onContextmenu: (ev) => {
        ev.preventDefault();
        openMenu([
          { label: "Voir le passage", icon: "eye", onSelect: () => openPassage({ question: query, kind: "ask" }, p) },
          { label: "Voir dans l'arborescence", icon: "folder", onSelect: () => reveal(p.path) },
          { label: "Ouvrir", icon: "external-link", onSelect: () => openFile(p.path) },
          { label: "Copier le passage", icon: "copy", onSelect: () => ctx.copy(p.text, "Passage copié") },
        ], ev.clientX, ev.clientY);
      } },
      h("div", { class: "rag-hit-head" }, ico(glyph, 13), h("span", { class: "rag-hit-name", text: p.name }),
        p.loc ? h("span", { class: "rag-hit-loc", text: p.loc }) : null,
        p.folder && p.folder !== "." ? h("span", { class: "rag-hit-folder", text: p.folder }) : null),
      h("div", { class: "rag-hit-text" }, highlight(p.text.replace(/\s+/g, " "), query, 260)));
    return item;
  }

  function renderSearch() {
    const s = S.search;
    results.hidden = !s;
    tree.hidden = !!s || !roots.length;
    if (!s) return;
    const close = iconButton("x", "Fermer la recherche", () => { closeSearch(true); focusTree(); }, { size: 14, cls: "sm" });
    const where = s.scope ? ` dans « ${s.scope.name} »` : "";
    if (s.loading) {
      results.replaceChildren(h("div", { class: "rag-results-head loading" },
        ico("loader-circle", 13), h("span", { text: `Recherche${where}…` }), close));
      return;
    }
    const { files, passages } = s.res;
    const count = [passages.length ? plural(passages.length, "passage", "passages") : "",
      files.length ? plural(files.length, "fichier", "fichiers") : ""].filter(Boolean).join(" · ");
    const out = [h("div", { class: "rag-results-head" }, h("span", { text: count ? `${count}${where}` : `Aucun résultat${where}` }), close)];
    if (!files.length && !passages.length) {
      out.push(h("p", { class: "rag-results-empty", text: vectors()
        ? "Essayez d'autres mots, ou posez la question en bas : le modèle sait reformuler."
        : "La recherche porte sur les mots exacts (pas de modèle d'embeddings) : essayez d'autres mots." }));
      if (s.scope) {
        out.push(h("button", { class: "btn ghost sm rag-everywhere", type: "button", onClick: () => runSearch(true) },
          ico("search", 14), "Chercher dans tous les dossiers"));
      }
    }
    if (files.length) {
      out.push(h("div", { class: "rag-results-label", text: "Fichiers" }),
        ...files.map((f) => h("div", { class: "rag-hit file", tabindex: "0", role: "button",
          onClick: () => reveal(f.path), onKeydown: (ev) => { if (ev.key === "Enter") reveal(f.path); },
          onDblclick: () => openFile(f.path) },
          h("div", { class: "rag-hit-head" }, ico(KIND_ICONS[f.kind] || "file", 13),
            h("span", { class: "rag-hit-name" }, highlight(f.name, s.query))))));
    }
    if (passages.length) {
      out.push(h("div", { class: "rag-results-label", text: "Passages" }), ...passages.map((p) => hitEl(p, s.query)));
    }
    results.replaceChildren(...out);
    results.scrollTop = 0;
  }

  // ─────────────────────────────────────────────
  //  Passages et texte indexé
  // ─────────────────────────────────────────────
  function openPassage(item, src) {
    const query = item.kind === "ask" ? item.question : "";
    const modal = openModal({
      title: src.name + (src.loc ? ` — ${src.loc}` : ""),
      subtitle: src.path,
      width: 720,
      body: h("div", { class: "rag-passage" }, highlight(src.text || "", query)),
      actions: [
        { label: "Copier", icon: "copy", kind: "ghost", left: true, iconOnly: true,
          onClick: () => { ctx.copy(src.text, "Passage copié"); return false; } },
        { label: "Voir dans l'arborescence", icon: "folder", kind: "ghost", onClick: () => { reveal(src.path); } },
        { label: "Afficher dans l'explorateur", icon: "folder-open", kind: "ghost", iconOnly: true,
          onClick: () => { api.rag_reveal(src.path); } },
        { label: "Ouvrir le fichier", icon: "external-link", kind: "primary", onClick: () => { openFile(src.path); } },
      ],
    });
    return modal;
  }

  async function showChunks(path) {
    const [res, info] = await Promise.all([api.rag_file_chunks(path), api.rag_file_info(path)]);
    if (!res) return;
    const name = path.split("\\").pop();
    const meta = [plural(res.total, "passage", "passages"), info?.chars ? plural(info.chars, "caractère", "caractères") : "",
      info?.vectors ? `vecteurs : ${info.embed_model}` : "sans vecteurs (recherche par mots)",
      info?.indexed_at ? `indexé le ${formatShort(parseDate(info.indexed_at))}` : ""].filter(Boolean).join(" · ");
    const list = h("div", { class: "rag-chunks" }, ...res.chunks.slice(0, CHUNKS_SHOWN).map((c) => h("div", { class: "rag-chunk" },
      h("div", { class: "rag-chunk-head" }, h("span", { text: `Passage ${c.idx + 1}` }), c.loc ? h("span", { text: c.loc }) : null,
        c.vector ? null : h("span", { class: "muted", text: "sans vecteur" })),
      h("div", { class: "rag-chunk-text", text: c.text }))),
    res.total > CHUNKS_SHOWN ? h("p", { class: "muted", text: `… et ${plural(res.total - CHUNKS_SHOWN, "autre passage", "autres passages")}` }) : null,
    info?.truncated ? h("p", { class: "muted", text: "Fichier très long : seul son début est indexé." }) : null);
    openModal({ title: `Texte indexé — ${name}`, subtitle: meta, width: 760, body: list,
      actions: [{ label: "Ouvrir le fichier", icon: "external-link", kind: "ghost", onClick: () => { openFile(path); } },
        { label: "Fermer", kind: "primary" }] });
  }

  // ─────────────────────────────────────────────
  //  Élément choisi
  // ─────────────────────────────────────────────
  function renderDetail() {
    const node = S.selected;
    detail.hidden = !node;
    if (!node) return;
    const e = node.entry;
    const ollama = !!store.ollama;
    const root = roots.find((r) => node.key === r.key || node.key.startsWith(r.key.replace(/\\$/, "") + "\\"));
    const rel = root && root !== node ? `${root.name}\\${node.path.slice(root.path.replace(/\\$/, "").length + 1)}` : node.path;
    const glyph = node.dir ? "folder" : KIND_ICONS[e.kind] || "file";
    const lines = [];
    let progress = null;
    if (node.dir) {
      if (e.missing) lines.push(h("span", { class: "error", text: "Dossier introuvable (disque débranché ?) : son index est gardé." }));
      else if (e.excluded || e.ignored) lines.push(e.excluded ? "Exclu de l'index." : "Dossier caché ou technique : non indexé.");
      else {
        lines.push(statsText(e.stats, vectors()));
        const p = progressOf(e.stats, vectors());
        if (p.waiting) progress = h("div", { class: "rag-bar" }, h("i", { style: { width: `${Math.round(p.pct * 100)}%` } }));
      }
    } else {
      const info = S.info || {};
      const st = fileState(e);
      const facts = [bytes(e.size ?? info.size)];
      if (e.mtime) facts.push(`modifié le ${formatShort(new Date(e.mtime * 1000))}`);
      lines.push(h("span", { class: `rag-detail-state ${st.cls}` }, st.icon ? ico(st.icon, 13) : null, h("span", { text: st.tip })));
      if (info.indexed_at && ["text", "indexed"].includes(info.status)) {
        facts.push(`${plural(info.chars || 0, "caractère", "caractères")} lus le ${formatShort(parseDate(info.indexed_at))}`);
      }
      lines.push(facts.filter(Boolean).join(" · "));
    }
    const readable = node.dir ? !e.missing && !e.excluded && !e.ignored : e.supported && !e.excluded && !e.ignored;
    const actions = [];
    if (readable) {
      const kinds = node.dir ? ["overview"] : FILE_TASKS;
      for (const k of kinds) {
        actions.push(h("button", { class: "btn sm", type: "button", disabled: !ollama || !!S.st.asking,
          dataset: { tip: ollama ? "" : "Ollama ne répond pas" }, onClick: () => runTask(k, node) },
        ico(TASKS[k].icon, 14), TASKS[k].label));
      }
      if (!node.dir) {
        actions.push(h("button", { class: "btn ghost sm", type: "button", disabled: !["text", "indexed"].includes(e.status),
          onClick: () => showChunks(node.path) }, ico("file-search", 14), "Texte indexé"));
      }
    }
    fill(detail,
      h("div", { class: "rag-detail-head" },
        h("span", { class: `rag-detail-icon${node.dir ? " dir" : ""}` }, ico(glyph, 17)),
        h("div", { class: "rag-detail-title" },
          h("div", { class: "rag-detail-name", text: node.name }),
          h("div", { class: "rag-detail-path", text: rel, dataset: { tip: node.path } })),
        iconButton("external-link", node.dir ? "Ouvrir dans l'explorateur" : "Ouvrir", () => (node.dir ? api.rag_reveal(node.path) : openFile(node.path)), { size: 15, cls: "sm" }),
        iconButton("ellipsis", "Plus d'actions", (ev) => openMenuBelow(nodeMenu(node), ev.currentTarget, "right"), { size: 15, cls: "sm" })),
      h("div", { class: "rag-detail-lines" }, ...lines.map((l) => h("div", { class: "rag-detail-line" }, l))),
      progress,
      actions.length ? h("div", { class: "rag-detail-actions" }, ...actions) : null);
  }

  // ─────────────────────────────────────────────
  //  Portée des questions
  // ─────────────────────────────────────────────
  function setScope(scope) {
    S.scope = scope;
    renderScope();
  }

  function renderScope() {
    const s = S.scope;
    scopeBtn.replaceChildren(ico(s ? (s.dir ? "folder" : "file-text") : "library", 14),
      h("span", { class: "name", text: s ? s.name : "Tous les dossiers" }),
      s ? h("span", { class: "rag-scope-clear", role: "button", "aria-label": "Tous les dossiers", dataset: { tip: "Questions sur tous les dossiers" } }, ico("x", 12))
        : ico("chevron-down", 13));
    scopeBtn.classList.toggle("on", !!s);
    scopeBtn.dataset.tip = s ? `Les questions portent sur ${s.dir ? "le dossier" : "le fichier"} « ${s.name} »` : "Les questions portent sur tous les dossiers";
    const ollama = !!store.ollama;
    composer.setPlaceholder(!ollama ? "Les réponses ont besoin d'Ollama (en local), qui ne répond pas."
      : s ? `Une question sur « ${s.name} » ?` : "Une question sur vos documents ?");
    renderSuggest();
  }

  function scopeMenu(ev) {
    if (ev.target.closest(".rag-scope-clear")) { setScope(null); return; }
    const sel = S.selected;
    const items = [
      { section: "Les questions portent sur" },
      { label: "Tous les dossiers", icon: "library", checked: !S.scope, onSelect: () => setScope(null) },
    ];
    if (sel && (sel.dir || (sel.entry.supported && !sel.entry.excluded))) {
      items.push({ label: sel.name, icon: sel.dir ? "folder" : "file-text", checked: S.scope?.path === sel.path,
        hint: "sélection", onSelect: () => setScope({ path: sel.path, name: sel.name, dir: sel.dir }) });
    }
    const others = roots.filter((r) => r !== sel);
    if (others.length) items.push("-", ...others.map((r) => ({ label: r.name, icon: "folder", checked: S.scope?.path === r.path,
      onSelect: () => setScope({ path: r.path, name: r.name, dir: true }) })));
    openMenuBelow(items, ev.currentTarget);
  }

  // ─────────────────────────────────────────────
  //  Questions et résumés
  // ─────────────────────────────────────────────
  async function ask(text) {
    const question = (text ?? input.value).trim();
    if (!question || S.st.asking || !store.ollama) return;
    const res = await api.rag_ask(question, S.scope?.path || null);
    if (!res?.ok) { toast(res?.error || "Question impossible.", "error"); return; }
    if (text === undefined) composer.setValue("");
  }

  /** Suggestions : les actions de la portée choisie (fichier, dossier), la suite de la dernière
   *  réponse, des questions à compléter. */
  function renderSuggest() {
    if (!store.ollama || !S.st.available) { suggest.set([]); return; }
    const scope = S.scope;
    const items = (scope ? (scope.dir ? ["overview"] : FILE_TASKS) : []).map((k) => ({ icon: TASKS[k].icon,
      label: TASKS[k].label, tip: `${TASKS[k].title} de « ${scope.name} »`, run: () => runTask(k, scope) }));
    const last = S.history[S.history.length - 1];
    if (last?.state === "done" && last.answer) items.push(...FOLLOW_UPS);
    items.push(...STARTERS);
    suggest.set(items, !!S.st.asking);
  }

  async function runTask(kind, node) {
    if (S.st.asking) { toast("Une réponse est déjà en cours.", "info"); return; }
    const res = await api.rag_task(kind, node.path);
    if (!res?.ok) toast(res?.error || "Action impossible.", "error");
  }

  function retry(item) {
    if (item.kind === "ask") ask(item.question);
    else api.rag_task(item.kind, item.scope?.path).then((res) => { if (!res?.ok) toast(res?.error || "Action impossible.", "error"); });
  }

  function titleOf(item) {
    if (item.kind === "ask") return item.question;
    return `${TASKS[item.kind]?.title || item.label} · ${item.scope?.name || ""}`;
  }

  function noteText(item) {
    const lines = [`## ${titleOf(item)}`, "", item.answer.trim()];
    const srcs = (item.sources || []).filter((s) => !s.whole);
    if (srcs.length) lines.push("", "Sources :", ...srcs.map((s) => `[${s.n}] ${s.name}${s.loc ? `, ${s.loc}` : ""} — ${s.path}`));
    return lines.join("\n");
  }

  function citeButton(item, src, n) {
    return h("button", { class: "rag-cite", type: "button", dataset: { tip: src.name + (src.loc ? ` · ${src.loc}` : "") },
      onClick: () => openPassage(item, src) }, String(n));
  }

  /** [1], [2, 3] de la réponse → boutons qui montrent le passage cité. */
  function linkCitations(root, item) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement.closest("code, pre") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const texts = [];
    while (walker.nextNode()) if (/\[\d/.test(walker.currentNode.data)) texts.push(walker.currentNode);
    const re = /\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]/g;
    for (const t of texts) {
      const parts = [];
      let last = 0;
      for (const m of t.data.matchAll(re)) {
        parts.push(t.data.slice(last, m.index));
        for (const n of m[1].split(/[,;]/).map((x) => Number(x.trim()))) {
          const src = item.sources.find((s) => s.n === n);
          parts.push(src ? citeButton(item, src, n) : `[${n}]`);
        }
        last = m.index + m[0].length;
      }
      if (!last) continue;
      parts.push(t.data.slice(last));
      t.replaceWith(...parts);
    }
  }

  function sourcesEl(item) {
    const sources = item.sources || [];
    if (!sources.length) return null;
    if (sources[0].whole) {
      const s = sources[0];
      const what = s.files !== undefined ? `début de ${plural(s.files, "document", "documents")}`
        : s.parts > 1 ? `lu en ${s.parts} parties` : "lu en entier";
      return h("div", { class: "rag-sources" },
        h("button", { class: "rag-src", type: "button", dataset: { tip: "Voir dans l'arborescence" }, onClick: () => reveal(s.path) },
          ico(s.files !== undefined ? "folder" : "file-text", 12), h("span", { text: `${s.name} · ${what}` })));
    }
    return h("div", { class: "rag-sources" }, h("span", { class: "rag-src-label", text: "Sources" }),
      ...sources.map((s) => h("button", { class: "rag-src", type: "button",
        dataset: { tip: `${s.path}${s.loc ? ` · ${s.loc}` : ""}` }, onClick: () => openPassage(item, s) },
      h("b", { text: String(s.n) }), h("span", { text: s.name + (s.loc ? ` · ${s.loc}` : "") }))));
  }

  function qaEl(item) {
    const source = `rag:${item.id}`;
    const answer = h("div", { class: "md asst-answer" });
    const phase = h("div", { class: "asst-phase" });
    const meta = h("div", { class: "asst-meta" });
    const speak = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(noCitations(entry.item.answer), source, true), { size: 15, cls: "sm" });
    speak.dataset.speakSource = source;
    speak.dataset.tipIdle = "Lire";
    const actions = h("div", { class: "asst-actions" },
      iconButton("copy", "Copier la réponse", () => ctx.copy(entry.item.answer, "Copié"), { size: 15, cls: "sm" }),
      speak,
      iconButton("notebook-pen", "Ajouter à une note", (ev) => ctx.pickDestination("notes", ev.currentTarget,
        (slot) => ctx.emit("note:insert", { text: noteText(entry.item), slot })), { size: 15, cls: "sm" }),
      iconButton("rotate-ccw", item.kind === "ask" ? "Reposer la question" : "Recommencer", () => retry(entry.item), { size: 15, cls: "sm" }),
      iconButton("x", "Retirer", () => api.rag_delete(item.id), { size: 15, cls: "sm" }));
    const reply = h("div", { class: "asst-reply" }, phase, answer, meta, actions);
    const question = h("div", { class: `asst-question rag-question${item.kind === "ask" ? "" : " task"}` });
    const root = h("div", { class: "asst-qa", dataset: { id: item.id } }, question, reply);
    reply.addEventListener("contextmenu", (ev) => {
      if (ev.target.closest(".rag-cite, .rag-src")) return;
      const sel = window.getSelection().toString();
      ev.preventDefault();
      openMenu([
        sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
        { label: "Copier la réponse", icon: "copy", onSelect: () => ctx.copy(entry.item.answer, "Copié") },
        { label: "Lire", icon: "volume-2", onSelect: () => ctx.speak(noCitations(entry.item.answer), source, true) },
        { label: "Ajouter à une note", icon: "notebook-pen", onSelect: () => ctx.emit("note:insert", { text: noteText(entry.item), slot: "new" }) },
        { label: item.kind === "ask" ? "Reposer la question" : "Recommencer", icon: "rotate-ccw", onSelect: () => retry(entry.item) },
        "-",
        { label: "Retirer", icon: "x", onSelect: () => api.rag_delete(item.id) },
      ], ev.clientX, ev.clientY);
    });
    const entry = { item, root, question, answer, phase, meta, actions, reply, shown: null };
    answers.set(item.id, entry);
    updateQa(entry);
    return root;
  }

  function updateQa(entry) {
    const { item } = entry;
    const busy = BUSY.has(item.state);
    const s = item.scope;
    fill(entry.question,
      item.kind === "ask" ? null : ico(TASKS[item.kind]?.icon || "sparkles", 13),
      h("span", { text: titleOf(item) }),
      item.kind === "ask" && s ? h("button", { class: "rag-question-scope", type: "button", dataset: { tip: s.path },
        onClick: () => reveal(s.path) }, ico(s.dir ? "folder" : "file-text", 11), s.name) : null);
    entry.reply.classList.toggle("busy", busy);
    entry.phase.hidden = !busy || (item.state === "thinking" && !!item.answer);
    entry.phase.replaceChildren(h("span", { class: "pane-busy" }), h("span", { text: item.phase || "…" }));
    if (entry.shown !== item.answer) {
      entry.shown = item.answer;
      entry.answer.innerHTML = item.answer ? renderMarkdown(item.answer) : "";
      if (item.sources?.length && !item.sources[0].whole) linkCitations(entry.answer, item);
    }
    entry.answer.hidden = !item.answer;
    const notes = [];
    if (item.note) notes.push(h("span", { class: "asst-note", text: item.note }));
    if (item.state === "stopped") notes.push(h("span", { class: "asst-note", text: "Arrêtée." }));
    if (item.state === "error") notes.push(h("span", { class: "asst-note error", text: item.error || "Échec." }));
    const at = parseDate(item.at);
    entry.meta.replaceChildren(busy && !item.sources?.length ? "" : sourcesEl(item) || "", ...notes,
      busy ? "" : h("span", { class: "asst-model", text: `${item.model}${at ? ` · ${formatShort(at)}` : ""}` }));
    entry.actions.hidden = busy || !item.answer;
  }

  function renderThread() {
    answers.clear();
    thread.replaceChildren(...S.history.map(qaEl));
    renderIntro();
    ctx.emit("speak-sources-changed");
  }

  function renderIntro() {
    const has = S.history.length > 0;
    threadHead.hidden = !has;
    intro.hidden = has;
    if (has) return;
    const tips = [
      ["message-square", "Posez une question en bas : les passages les plus proches sont retrouvés dans vos documents, puis "
        + "Ollama répond en citant ses sources — cliquez sur [1] pour voir le passage."],
      ["file-text", "Choisissez un fichier à gauche pour le résumer, en tirer les points clés, l'expliquer ou lui poser une "
        + "question (lu en entier s'il n'est pas trop long)."],
      ["folder", "Choisissez un dossier pour limiter les questions à son contenu ou le faire présenter."],
      ["search", "Le champ « Rechercher » montre les passages et les noms de fichiers correspondants, sans attendre de réponse."],
    ];
    intro.replaceChildren(
      h("div", { class: "rag-tips" }, ...tips.map(([icon, text]) => h("div", { class: "rag-tip" }, ico(icon, 15), h("span", { text })))),
      h("p", { class: "asst-privacy" }, ico("lock", 13),
        h("span", { text: "Tout reste sur ce PC : l'index est rangé dans les données de Boostache et Ollama répond en local. "
          + "Les dossiers sont surveillés : un fichier ajouté, modifié ou supprimé est pris en compte en quelques secondes." })));
  }

  function nearBottom() {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  }

  function scrollDown() {
    requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; });
  }

  // ── Lecture à voix haute des réponses ───────
  /** Réponse en cours d'écriture, s'il y en a une. */
  const busyEntry = () => [...answers.values()].find((e) => BUSY.has(e.item.state)) || null;

  /** Lecture auto : la réponse, dès ses premières phrases (la suite par feedAnswer). */
  function autoRead(entry) {
    entry.autoRead = true;
    ctx.speak(noCitations(entry.item.answer), sourceOf(entry.item.id), true, true);
  }

  /** Texte de toutes les questions affichées (réponse en cours comprise), sans les renvois [1]. */
  function threadSpeech(mode) {
    return conversationSpeech(S.history.map((i) => answers.get(i.id)?.item || i)
      .map((i) => ({ ask: titleOf(i), answer: noCitations(i.answer) })), mode);
  }

  /** Suite d'une réponse pour les lectures au fil de l'eau (la sienne, celle de toutes). */
  function feedAnswer(entry) {
    const final = !BUSY.has(entry.item.state);
    ctx.feed(sourceOf(entry.item.id), noCitations(entry.item.answer), final);
    if (ctx.feeding(READ_ALL)) ctx.feed(READ_ALL, threadSpeech(readingMode), final);
  }

  // ─────────────────────────────────────────────
  //  État de l'indexation
  // ─────────────────────────────────────────────
  function renderStatus() {
    const st = S.st;
    const t = st.totals || {};
    const p = progressOf(t, vectors());
    const busy = st.phase === "indexing" || (st.phase === "scanning" && roots.length > 0);
    sub.textContent = t.total ? `${plural(t.total, "fichier", "fichiers")} · ${plural(t.chunks || 0, "passage", "passages")}` : "";
    const name = (st.current || "").split("\\").pop();
    let text;
    let kind = "";
    if (!st.available) { text = "Index indisponible"; kind = "error"; }
    else if (!roots.length) text = "";
    else if (st.paused) { text = p.waiting ? `En pause · ${p.waiting.toLocaleString("fr-FR")} en attente` : "En pause"; kind = "paused"; }
    else if (st.phase === "indexing" && p.waiting) { text = `Indexation ${Math.floor(p.pct * 100)} %`; kind = "busy"; }
    else if (st.phase === "scanning") { text = "Vérification…"; kind = "busy"; }
    else if (p.waiting) { text = `${p.waiting.toLocaleString("fr-FR")} en attente`; kind = "busy"; }
    else text = "À jour";
    statusPill.hidden = !text;
    statusPill.className = `rag-status ${kind}`.trim();
    statusPill.replaceChildren(kind === "busy" ? ico("loader-circle", 13) : h("span", { class: "rag-dot" }), h("span", { text }));
    statusPill.dataset.tip = st.phase === "indexing" && name ? `Indexation de ${name}` : st.watching ? "Dossiers surveillés en continu" : "";
    pauseBtn.hidden = !roots.length;
    pauseBtn.replaceChildren(ico(st.paused ? "play" : "pause", 15));
    pauseBtn.dataset.tip = st.paused ? "Reprendre l'indexation" : "Mettre l'indexation en pause";
    pauseBtn.setAttribute("aria-label", pauseBtn.dataset.tip);

    // Pied de l'arborescence
    foot.hidden = !roots.length;
    footBar.style.width = `${Math.round(p.pct * 100)}%`;
    foot.classList.toggle("done", !p.waiting);
    foot.classList.toggle("paused", !!st.paused);
    const eta = st.eta && p.waiting && !st.paused ? ` · ≈ ${duration(st.eta)}` : "";
    if (st.paused) footText.textContent = p.waiting ? `En pause : ${plural(p.waiting, "fichier", "fichiers")} en attente` : "En pause : index à jour";
    else if (st.phase === "indexing" && name) footText.replaceChildren(h("span", { class: "rag-foot-file", text: name }),
      h("span", { text: `${p.done.toLocaleString("fr-FR")}/${p.total.toLocaleString("fr-FR")}${eta}` }));
    else if (st.phase === "scanning") footText.textContent = `Recherche des fichiers nouveaux ou modifiés${name ? ` dans ${name}` : ""}…`;
    else if (p.waiting) footText.textContent = `${plural(p.waiting, "fichier", "fichiers")} en attente${eta}`;
    else footText.textContent = `À jour : ${plural(t.total || 0, "fichier", "fichiers")}, ${plural(t.chunks || 0, "passage", "passages")}`;
    const e = st.embed || {};
    let note = "";
    if (e.error) note = `Vecteurs en attente : ${e.error}`;
    else if (!e.model && e.numpy === false) note = "Vecteurs indisponibles (numpy absent) : recherche par mots seulement.";
    else if (!e.online && (t.text || 0) > 0) note = "Vecteurs en attente : Ollama ne répond pas.";
    else if (t.error) note = `${plural(t.error, "fichier illisible", "fichiers illisibles")} (icône rouge).`;
    footNote.textContent = note;
    footNote.hidden = !note;
    footNote.classList.toggle("error", !!e.error);

    // Champ de question
    const ollama = !!store.ollama;
    composer.update({ busy: !!st.asking, ready: ollama });
    input.disabled = !ollama;
    modelName.textContent = st.model || "Aucun modèle";
    modelBtn.hidden = !ollama;
    addBtn.disabled = !st.available;
    ctx.setBadge("rag", busy && p.waiting && !st.paused ? "dot" : null, "live");
    renderScope();
    renderEmbed();
  }

  function renderEmbed() {
    const e = S.st.embed || {};
    const show = S.st.available && roots.length > 0 && !(e.installed || []).length && e.numpy !== false;
    embedCard.hidden = !show;
    if (!show) return;
    const best = (e.suggested || [])[0] || { name: "embeddinggemma", size: "" };
    const job = S.pulls.get(fullName(best.name));
    const running = job && !job.done;
    let action;
    if (!store.ollama) action = h("span", { class: "rag-embed-hint", text: "Ollama ne répond pas : lancez-le pour installer le modèle." });
    else if (running) {
      const pct = job.total ? Math.round((job.completed / job.total) * 100) : 0;
      action = h("div", { class: "rag-embed-pull" }, h("div", { class: "rag-bar" }, h("i", { style: { width: `${pct}%` } })),
        h("span", { text: `${job.status || "Téléchargement"} ${job.total ? `${pct} %` : ""}` }),
        iconButton("x", "Annuler", () => api.ollama_cancel(job.name), { size: 13, cls: "sm" }));
    } else {
      action = h("div", { class: "rag-embed-actions" },
        h("button", { class: "btn primary sm", type: "button", onClick: () => pull(best.name) },
          ico("download", 14), `Installer ${best.name}${best.size ? ` · ${best.size}` : ""}`),
        h("button", { class: "btn ghost sm", type: "button", onClick: (ev) => openMenuBelow([
          { section: "Modèles d'embeddings" },
          ...(e.suggested || []).map((s) => ({ label: s.name, hint: s.size, icon: "download", onSelect: () => pull(s.name) })),
          "-",
          { label: "Section Ollama…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
        ], ev.currentTarget) }, "Autres…"));
    }
    fill(embedCard,
      h("div", { class: "rag-embed-head" }, ico("database", 15), h("span", { text: "Recherche par mots seulement" })),
      h("p", { text: "Un modèle d'embeddings retrouve les passages par leur sens, pas seulement par les mots exacts : "
        + "des réponses bien plus justes. Il tourne en local, dans Ollama." }),
      job?.error ? h("p", { class: "error", text: job.error }) : null,
      action);
  }

  /** Nouvel état reçu : racines, rangées et détails mis à jour. */
  let lastAsking = null;
  function onState() {
    const prevKey = S.currentKey;
    S.currentKey = S.st.phase === "indexing" ? S.st.current_key || "" : "";
    const changed = syncRoots();
    if (changed) renderTree();
    else for (const r of roots) updateRow(r);
    if (prevKey !== S.currentKey) {
      updateRow(nodes.get(prevKey));
      updateRow(nodes.get(S.currentKey));
    }
    const asking = S.st.asking || null;
    if (S.selected?.dir || asking !== lastAsking || (S.selected && [prevKey, S.currentKey].includes(S.selected.key))) {
      renderDetail();
    }
    lastAsking = asking;
    renderStatus();
  }

  // ── Relecture des dossiers dépliés ─────────
  const shownNow = () => ctx.isActive("rag") && S.windowVisible;

  function queueRefresh(keys) {
    for (const k of keys) {
      const n = nodes.get(k);
      if (n?.dir && n.expanded && n.loaded) dirty.add(k);
    }
    if (dirty.size && !refreshTimer && shownNow()) refreshTimer = setTimeout(flushRefresh, REFRESH_MS);
  }

  async function flushRefresh() {
    refreshTimer = null;
    if (!shownNow()) return;
    const keys = [...dirty];
    dirty.clear();
    for (const k of keys) {
      const n = nodes.get(k);
      if (n) await load(n);
    }
    if (S.selected && !S.selected.dir && keys.includes(S.selected.parent)) fetchInfo(S.selected);
  }

  // ── Largeur de l'arborescence ──────────────
  function setTreeWidth(w) {
    body.style.setProperty("--rag-tree-w", `${Math.round(w)}px`);
  }

  splitter.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    splitter.setPointerCapture(ev.pointerId);
    splitter.classList.add("dragging");
    const r = body.getBoundingClientRect();
    let width = side.getBoundingClientRect().width;
    const onMove = (e) => {
      width = Math.max(TREE_MIN, Math.min(e.clientX - r.left, r.width - MAIN_MIN));
      setTreeWidth(width);
    };
    const onUp = () => {
      splitter.classList.remove("dragging");
      splitter.removeEventListener("pointermove", onMove);
      splitter.removeEventListener("pointerup", onUp);
      splitter.removeEventListener("pointercancel", onUp);
      ctx.saveSetting("rag_tree_width", Math.round(width));
    };
    splitter.addEventListener("pointermove", onMove);
    splitter.addEventListener("pointerup", onUp);
    splitter.addEventListener("pointercancel", onUp);
  });
  splitter.addEventListener("dblclick", () => { setTreeWidth(TREE_DEFAULT); ctx.saveSetting("rag_tree_width", TREE_DEFAULT); });
  splitter.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    const r = body.getBoundingClientRect();
    const w = Math.max(TREE_MIN, Math.min(side.getBoundingClientRect().width + (ev.key === "ArrowRight" ? 24 : -24), r.width - MAIN_MIN));
    setTreeWidth(w);
    ctx.saveSetting("rag_tree_width", Math.round(w));
  });

  // ─────────────────────────────────────────────
  //  Événements Python
  // ─────────────────────────────────────────────
  on("rag:state", (st) => { S.st = { ...S.st, ...st }; onState(); });
  on("rag:changed", ({ dirs }) => queueRefresh(dirs || []));

  on("rag:qa", ({ item }) => {
    const entry = answers.get(item.id);
    if (entry) {
      entry.item = item;
      updateQa(entry);
      const i = S.history.findIndex((x) => x.id === item.id);
      if (i >= 0) S.history[i] = item;
      // Lecture auto : dès que la réponse commence (passages trouvés et lus)
      if (item.state === "thinking" && !entry.autoRead && composer.autoRead()) autoRead(entry);
      feedAnswer(entry);
      if (!BUSY.has(item.state)) { renderDetail(); renderSuggest(); }
      return;
    }
    S.history.push(item);
    thread.append(qaEl(item));
    renderIntro();
    scrollDown();
    renderDetail();
    ctx.emit("speak-sources-changed");
  });

  on("rag:delta", ({ id, text }) => {
    const entry = answers.get(id);
    if (!entry) return;
    const follow = nearBottom();
    entry.item = { ...entry.item, answer: (entry.item.answer || "") + text, state: "thinking" };
    updateQa(entry);
    feedAnswer(entry);
    if (follow) scrollDown();
  });

  on("rag:history", ({ items }) => {
    S.history = items || [];
    renderThread();
    renderSuggest();
  });

  on("ollama:pull", (p) => {
    S.pulls.set(p.name, p);
    renderEmbed();
    if (p.done && !p.error && !p.cancelled && (S.st.embed?.suggested || []).some((s) => fullName(s.name) === p.name)) {
      toast(`${p.name} installé : calcul des vecteurs des documents…`);
    }
  });

  on("chat:models", ({ models }) => { S.models = models || []; renderStatus(); });
  on("chat:available", () => { renderStatus(); renderDetail(); });
  on("window:shown", () => {
    S.windowVisible = true;
    if (ctx.isActive("rag") && dirty.size) flushRefresh();
  });
  on("window:hidden", () => { S.windowVisible = false; });

  // ── Initialisation ──────────────────────────
  syncRoots();
  // Dossiers dépliés oubliés : ceux qui ne sont plus dans la liste
  for (const k of [...S.expanded]) {
    if (!roots.some((r) => k === r.key || k.startsWith(r.key.replace(/\\$/, "") + "\\"))) S.expanded.delete(k);
  }
  saveSet(EXPANDED_KEY, S.expanded);
  renderTree();
  renderStatus();
  renderThread();
  renderDetail();
  setTimeout(renderStatus, 0);    // pastille : la barre latérale est construite après les vues
  const saved = loadText(SELECTED_KEY);
  if (saved) setTimeout(() => reveal(saved, { focus: false }), 0);

  return {
    el,
    onShow() {
      if (dirty.size) flushRefresh();
      renderStatus();
    },
    onWindowShown() { if (ctx.isActive("rag") && dirty.size) flushRefresh(); },
    /** Glisser-déposer : dossiers ajoutés à la liste, fichier indexé montré. */
    async onDrop(paths) {
      const res = await api.rag_add_paths(paths);
      if (!res) return;
      for (const p of res.added) { S.expanded.add(keyOf(p)); }
      saveSet(EXPANDED_KEY, S.expanded);
      if (res.added.length) {
        await refreshState();
        toast(res.added.length > 1 ? `${res.added.length} dossiers ajoutés : indexation en cours` : "Dossier ajouté : indexation en cours");
        reveal(res.added[res.added.length - 1]);
      } else if (res.reveal) reveal(res.reveal);
      for (const err of res.errors) toast(err, "error");
    },
    focus() {
      if (!roots.length) addBtn.focus();
      else if (!input.disabled) input.focus();
    },
  };
}
