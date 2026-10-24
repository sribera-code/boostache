// Vue Comparer : deux textes (fichiers ou textes collés) côte à côte, leurs différences
// mot à mot et leur fusion, différence par différence (compare.py) — avec l'IA : explication
// des différences, questions, choix de la fusion d'après une consigne.
import { h, plural, parseDate, formatShort, formatFullDate, isTyping } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, openModal, toast } from "../ui.js";
import { renderMarkdown } from "../markdown.js";
import { createComposer, createSuggestions, conversationSpeech } from "../composer.js";

const SIDES = ["left", "right"];
const SIDE_NAMES = { left: "Gauche", right: "Droite" };
const OTHER = { left: "right", right: "left" };
const BUSY = new Set(["reading", "thinking"]);
const READ_ALL = "compare:all";
const CONTEXT = 3;            // lignes identiques gardées de part et d'autre d'une différence
const GAP_MIN = 4;            // moins de lignes identiques cachées : elles restent affichées
const GAP_STEP = 1000;        // lignes identiques dévoilées par clic, au plus
const ROW_LIMIT = 4000;       // lignes affichées avant « Afficher la suite »
const AI_KEY = "boostache.compare.ai";
const AI_WIDE = 1100;         // section au moins aussi large : l'IA s'ouvre d'office à côté
const CHOICE_TEXT = {
  left: "gauche gardée", right: "droite gardée", left_right: "les deux gardées, gauche puis droite",
  right_left: "les deux gardées, droite puis gauche", text: "version fusionnée",
};
// Suggestions au-dessus du champ (gardées quand des questions ont été posées)
const SUGGESTIONS = [
  { icon: "flag", label: "Changements importants", ask: "Quels changements modifient le sens ou les informations (dates, montants, noms, conditions) ?" },
  { icon: "lightbulb", label: "Quelle version garder ?", ask: "Laquelle des deux versions me conseillez-vous de garder, et pourquoi ?" },
  { icon: "arrow-left", label: "Seulement à gauche", ask: "Qu'est-ce qui figure à gauche mais plus à droite ?" },
  { icon: "arrow-right", label: "Seulement à droite", ask: "Qu'est-ce qui a été ajouté à droite ?" },
];
const FOLLOW_UPS = [
  { icon: "arrow-down", label: "Développer", ask: "Développe ta dernière réponse." },
  { icon: "lightbulb", label: "Plus simplement", ask: "Explique plus simplement ta dernière réponse." },
];
// Consignes toutes faites pour la fusion par l'IA
const HINTS = [
  { label: "Garder les ajouts des deux côtés", text: "Garde les ajouts des deux côtés ; en cas de contradiction, prends la droite." },
  { label: "La droite est la plus récente", text: "La version de droite est la plus récente : préfère-la, sauf pour ce qu'elle aurait perdu par erreur." },
  { label: "La gauche fait foi", text: "La version de gauche fait foi : ne prends à droite que les ajouts qui la complètent." },
];

const baseName = (p) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;

/** « il y a 3 jours »… (comme la section Fichiers) */
function relative(d) {
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "à l'instant";
  const [v, unit] = s < 3600 ? [s / 60, "min"] : s < 86400 ? [s / 3600, "h"] : s < 86400 * 30 ? [s / 86400, "jour"]
    : s < 86400 * 365 ? [s / 86400 / 30.44, "mois"] : [s / 86400 / 365.25, "an"];
  const n = Math.round(v);
  return `il y a ${n} ${unit === "jour" || unit === "an" ? `${unit}${n > 1 ? "s" : ""}` : unit}`;
}

export function createCompareView(ctx, state) {
  const { api, on, store } = ctx;
  const S = {
    loaded: false,
    sides: state.compare?.sides || { left: null, right: null },
    lines: { left: [], right: [] },
    blocks: [],
    stats: null,
    options: { space: false, case: false },
    hint: "",
    thread: [],
    model: state.compare?.model || "",
    models: state.chat?.models || [],
    resolving: null,          // choix de l'IA en cours {total, done, current}
    current: -1,              // différence sélectionnée (navigation, Alt+← →)
    expanded: new Map(),      // lignes identiques cachées (première ligne) → lignes dévoilées
    showSame: false,          // toutes les lignes identiques affichées
    rowLimit: ROW_LIMIT,
    key: "",                  // comparaison affichée (textes et options)
    autoKey: "",              // comparaison déjà expliquée d'office
  };
  const answers = new Map();  // id → réponse affichée
  const blockEls = new Map(); // différence → élément
  let aiOpen = null;          // panneau de l'IA (null : pas encore décidé)
  try {
    const saved = localStorage.getItem(AI_KEY);
    if (saved !== null) aiOpen = saved === "1";
  } catch { /* stockage indisponible */ }

  // ─────────────────────────────────────────────
  //  Structure
  // ─────────────────────────────────────────────
  const sourceEls = Object.fromEntries(SIDES.map((key) => [key, h("div", { class: "cmp-source", dataset: { drop: key } })]));
  const swapBtn = iconButton("arrow-left-right", "Inverser gauche et droite", () => swap(), { size: 15, cls: "sm cmp-swap" });
  const sources = h("div", { class: "cmp-sources" }, sourceEls.left, swapBtn, sourceEls.right);

  const statsEl = h("div", { class: "cmp-stats" });
  const prevBtn = iconButton("chevron-up", "Différence précédente", () => go(-1), { size: 15, cls: "sm", kbd: "Alt+↑" });
  const nextBtn = iconButton("chevron-down", "Différence suivante", () => go(1), { size: 15, cls: "sm", kbd: "Alt+↓" });
  const posEl = h("span", { class: "cmp-pos" });
  const mergeEl = h("span", { class: "cmp-merge-count" });
  const allBtn = h("button", { class: "btn sm", type: "button", "aria-label": "Choix pour toutes les différences",
    onClick: (ev) => openMenuBelow(allItems(), ev.currentTarget, "right") }, "Tout", ico("chevron-down", 13));
  const aiPickBtn = h("button", { class: "btn sm", type: "button", dataset: { ollama: "" }, onClick: () => resolve(null) },
    ico("sparkles", 14), h("span", { class: "cmp-btn-label", text: "Choisir avec l'IA" }));
  const resolvingEl = h("span", { class: "cmp-resolving", hidden: true, dataset: { ollama: "" } });
  const resultBtn = h("button", { class: "btn sm primary", type: "button", onClick: () => openResult() },
    ico("git-merge", 14), "Résultat…");
  const bar = h("div", { class: "cmp-bar", hidden: true },
    h("div", { class: "cmp-nav" }, prevBtn, nextBtn, posEl), statsEl, h("div", { class: "head-spacer" }),
    mergeEl, allBtn, resolvingEl, aiPickBtn, resultBtn);

  const message = h("div", { class: "empty cmp-message", hidden: true });
  const grid = h("div", { class: "cmp-grid" });
  const scroll = h("div", { class: "cmp-scroll", tabindex: "-1" }, message, grid);
  const mainCol = h("div", { class: "cmp-main" }, bar, scroll);

  // ── IA : explication, fusion, questions ──
  const explainBtn = h("button", { class: "btn sm", type: "button", onClick: () => explain() });
  const aiHead = h("div", { class: "fx-ai-head" }, ico("sparkles", 15), h("span", { class: "fx-card-title", text: "IA" }),
    h("div", { class: "head-spacer" }), explainBtn,
    iconButton("x", "Fermer le panneau de l'IA", () => setAi(false), { size: 15, cls: "sm cmp-ai-close" }));
  const aiNote = h("div", { class: "fx-ai-note", hidden: true });
  const hintInput = h("textarea", { class: "field cmp-hint", rows: "2", maxlength: "2000", spellcheck: "true",
    "aria-label": "Consigne pour la fusion",
    placeholder: "Consigne (facultatif) : « la droite est la plus récente », « garde les prix de gauche »…" });
  let hintTimer = null;
  hintInput.addEventListener("input", () => {
    clearTimeout(hintTimer);
    hintTimer = setTimeout(() => { S.hint = hintInput.value; api.compare_hint(hintInput.value); }, 500);
  });
  const resolveBtn = h("button", { class: "btn sm primary", type: "button", onClick: () => resolve(null) });
  const resolveInfo = h("span", { class: "cmp-ai-info" });
  const mergeCard = h("div", { class: "cmp-ai-card", hidden: true },
    h("div", { class: "fx-card-head" }, ico("git-merge", 15), h("span", { class: "fx-card-title", text: "Fusion par l'IA" })),
    h("p", { class: "cmp-ai-desc", text: "Pour chaque différence sans choix, l'IA garde la gauche, la droite, les deux, "
      + "ou écrit une version qui réunit les deux. Vous vérifiez, puis vous enregistrez le résultat." }),
    hintInput,
    h("div", { class: "cmp-hint-chips" }, ...HINTS.map((t) => h("button", { class: "pane-chip", type: "button", text: t.label,
      dataset: { tip: t.text }, onClick: () => setHint(t.text) }))),
    h("div", { class: "cmp-ai-actions" }, resolveBtn, resolveInfo));
  const thread = h("div", { class: "asst-thread fx-thread" });
  const aiScroll = h("div", { class: "fx-ai-scroll" }, aiNote, mergeCard, thread);

  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle de l'IA",
    dataset: { tip: "Modèle qui explique, répond et choisit la fusion" },
    onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget) },
  ico("sparkles", 14), modelName, ico("chevron-down", 14));
  let readingMode = "answers";
  const composer = createComposer(ctx, {
    label: "Question sur les différences", maxlength: 4000, maxHeight: 140,
    tools: [iconButton("mic", "Dictée vocale", () => { composer.input.focus(); api.dictate(); }, { kbd: "Win+H" }), modelBtn],
    speakKey: "compare_speak",
    onAutoRead: (on) => { const entry = busyEntry(); if (on && entry) autoRead(entry); },
    read: {
      source: READ_ALL,
      match: (source) => source === READ_ALL || (source.startsWith("compare:") && answers.has(source.slice(8))),
      speak: (mode) => {
        readingMode = mode;
        ctx.speak(threadSpeech(mode), READ_ALL, true, !!busyEntry());
      },
    },
    onSend: () => ask(),
    onStop: () => api.compare_stop(),
    sendTip: "Demander", stopTip: "Arrêter la réponse",
  });
  composer.setPlaceholder("Question sur les différences…");
  const suggest = createSuggestions(composer, (text) => ask(text));
  const aiPanel = h("aside", { class: "fx-ai cmp-ai", hidden: true }, aiHead, aiScroll,
    h("div", { class: "fx-composer" }, suggest.el, composer.el));

  const aiBtn = iconButton("panel-right", "", () => setAi(!aiPanel.hidden), { size: 15, cls: "sm" });
  const optionsBtn = iconButton("sliders-horizontal", "Options de la comparaison",
    (ev) => openMenuBelow(optionItems(), ev.currentTarget, "right"), { size: 15, cls: "sm" });
  const dropOverlay = h("div", { class: "drop-overlay" },
    h("div", { class: "inner" }, ico("git-compare", 22), "Déposez un fichier sur un côté, ou deux fichiers pour les comparer"));
  const el = h("section", { class: "compare" },
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Comparer" }),
      h("span", { class: "view-sub", text: "deux textes : différences et fusion" }),
      h("div", { class: "head-spacer" }), optionsBtn, aiBtn),
    sources,
    h("div", { class: "cmp-body" }, mainCol, aiPanel),
    dropOverlay);

  // Copie d'un seul côté : la sélection commencée dans une colonne ne prend pas l'autre
  grid.addEventListener("pointerdown", (ev) => {
    const cell = ev.target.closest?.(".cmp-text");
    if (cell) grid.dataset.sel = cell.classList.contains("cmp-l") ? "left" : "right";
  });

  document.addEventListener("keydown", (ev) => {
    if (!ctx.isFocused("compare") || isTyping(ev.target) || document.querySelector(".modal-backdrop")) return;
    if (ev.key === "F5" && !ev.ctrlKey && !ev.altKey) { reload(); return; }
    if (!ev.altKey || ev.ctrlKey || ev.shiftKey) return;
    const keys = { ArrowDown: () => go(1), ArrowUp: () => go(-1), ArrowLeft: () => keep("left"), ArrowRight: () => keep("right") };
    if (!keys[ev.key]) return;
    ev.preventDefault();
    ev.stopPropagation();
    keys[ev.key]();
  }, true);

  // ─────────────────────────────────────────────
  //  État reçu de Python
  // ─────────────────────────────────────────────
  /** État complet (compare_state, ou réponse d'une action) ; faux en cas d'erreur. */
  function apply(st) {
    if (!st) return false;
    if (st.error) { toast(st.error, "error"); return false; }
    const key = comparisonKey(st);
    if (key !== S.key) {           // autre comparaison : lignes dépliées et position oubliées
      S.key = key;
      S.expanded.clear();
      S.rowLimit = ROW_LIMIT;
      S.current = -1;
    }
    Object.assign(S, { loaded: true, sides: st.sides, lines: st.lines, blocks: st.blocks, stats: st.stats,
      options: st.options, hint: st.hint, thread: st.thread || [], resolving: st.resolving });
    if (st.model) S.model = st.model;
    if (S.current >= S.blocks.length) S.current = S.blocks.length - 1;
    if (document.activeElement !== hintInput) hintInput.value = S.hint || "";
    render();
    maybeExplain();
    return true;
  }

  function comparisonKey(st) {
    const sig = (st.blocks || []).map((b) => b.sig).join(",");
    return `${JSON.stringify(st.options)}|${st.lines?.left.length}|${st.lines?.right.length}|${sig}`;
  }

  async function refresh(check = false) {
    apply(await api.compare_state(check));
  }

  // ─────────────────────────────────────────────
  //  Sources
  // ─────────────────────────────────────────────
  async function load(key, path) {
    return apply(await api.compare_load(key, path));
  }

  async function pick(key) {
    const res = await api.compare_pick(key);
    if (res) apply(res);
  }

  async function paste(key) {
    apply(await api.compare_paste(key));
  }

  async function swap() {
    if (!S.sides.left && !S.sides.right) return;
    apply(await api.compare_swap());
  }

  async function reload() {
    if (!S.sides.left && !S.sides.right) return;
    if (apply(await api.compare_reload())) toast("Fichiers relus.", "info", 1600);
  }

  async function clearSide(key) {
    apply(await api.compare_clear(key));
  }

  async function setOptions(options) {
    apply(await api.compare_options(options));
  }

  /** Texte saisi ou collé à la main (nouveau, ou celui d'un côté « texte » à modifier). */
  function editText(key) {
    const s = S.sides[key];
    const own = s?.kind === "text";
    const name = h("input", { class: "field", type: "text", value: own ? s.name : "", spellcheck: "false",
      placeholder: "Nom (facultatif)", "aria-label": "Nom du texte" });
    const area = h("textarea", { class: "field mono cmp-edit-area", spellcheck: "false", autofocus: true,
      placeholder: "Collez ou tapez le texte…", "aria-label": "Texte" });
    area.value = own ? S.lines[key].join("\n") : "";
    const modal = openModal({
      title: `Texte de ${SIDE_NAMES[key].toLowerCase()}`, width: 760,
      body: h("div", { class: "cmp-edit" }, name, area),
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: "Comparer", icon: "git-compare", kind: "primary", onClick: async () => {
          const res = await api.compare_text(key, area.value, name.value);
          if (res?.error) { modal.setError(res.error); return false; }
          apply(res);
          return true;
        } },
      ],
    });
  }

  function sourceItems(key) {
    const s = S.sides[key];
    const file = s?.kind === "file";
    return [
      { label: "Choisir un autre fichier…", icon: "folder-open", onSelect: () => pick(key) },
      { label: "Coller le presse-papiers", icon: "clipboard-paste", onSelect: () => paste(key) },
      { label: s?.kind === "text" ? "Modifier le texte…" : "Saisir un texte…", icon: "pencil", onSelect: () => editText(key) },
      "-",
      file ? { label: "Ouvrir avec son application", icon: "external-link", onSelect: () => api.compare_open(key) } : null,
      file ? { label: "Afficher dans l'Explorateur", icon: "folder", onSelect: () => api.reveal_path(s.path) } : null,
      file ? { label: "Copier le chemin", icon: "copy", onSelect: () => ctx.copy(s.path, "Chemin copié") } : null,
      { label: "Copier le texte", icon: "copy", disabled: !S.lines[key].length,
        onSelect: () => ctx.copy(S.lines[key].join("\n"), "Texte copié") },
      "-",
      { label: "Retirer", icon: "x", onSelect: () => clearSide(key) },
    ].filter(Boolean);
  }

  function renderSources() {
    for (const key of SIDES) sourceCard(key);
    swapBtn.disabled = !S.sides.left && !S.sides.right;
  }

  function sourceCard(key) {
    const s = S.sides[key];
    const card = sourceEls[key];
    const tag = h("span", { class: `cmp-side-tag ${key}`, text: SIDE_NAMES[key] });
    if (!s) {
      card.className = "cmp-source empty";
      card.replaceChildren(tag, h("div", { class: "cmp-pick" },
        h("button", { class: "btn sm", type: "button", onClick: () => pick(key) }, ico("folder-open", 14), "Choisir un fichier"),
        h("button", { class: "btn sm", type: "button", onClick: () => paste(key) }, ico("clipboard-paste", 14), "Coller"),
        h("button", { class: "btn sm ghost", type: "button", onClick: () => editText(key) }, ico("pencil", 14), "Saisir…")),
      h("div", { class: "cmp-drop-hint" }, ico("arrow-down", 12), "ou glissez un fichier ici"));
      return;
    }
    card.className = `cmp-source${s.error ? " error" : ""}`;
    const modified = parseDate(s.modified);
    const meta = s.error ? [h("span", { class: "cmp-src-error", text: s.error })] : !S.loaded ? ["lecture…"] : [
      s.kind === "text" ? "texte collé" : s.document ? "texte extrait du document" : s.encoding,
      s.newline && s.kind === "file" ? s.newline : "",
      plural(s.lines, "ligne", "lignes"),
      modified ? h("span", { text: `modifié ${relative(modified)}`, dataset: { tip: formatFullDate(modified) } }) : "",
      s.truncated ? h("span", { class: "cmp-src-warn", text: "début seulement (très long)" }) : "",
      s.mixed ? h("span", { class: "cmp-src-warn", text: "fins de ligne mélangées",
        dataset: { tip: "Le résultat enregistré les uniformise" } }) : "",
    ].filter(Boolean).flatMap((part, i) => (i ? [h("span", { class: "cmp-dot", text: "·" }), part] : [part]));
    const more = iconButton("ellipsis", "Autres actions", (ev) => openMenuBelow(sourceItems(key), ev.currentTarget, "right"),
      { size: 15, cls: "sm" });
    card.replaceChildren(tag,
      h("div", { class: "cmp-src-icon" }, ico(s.kind === "text" ? "type" : s.document ? "file-text" : "file", 17)),
      h("div", { class: "cmp-src-text" },
        h("div", { class: "cmp-src-name", text: s.name, dataset: { tip: s.path || s.name } }),
        h("div", { class: "cmp-src-meta" }, ...meta)),
      h("div", { class: "cmp-src-actions" },
        s.kind === "file" ? iconButton("refresh-cw", "Relire le fichier", () => reload(), { size: 15, cls: "sm", kbd: "F5" })
          : iconButton("pencil", "Modifier le texte", () => editText(key), { size: 15, cls: "sm" }),
        more));
    card.oncontextmenu = (ev) => { ev.preventDefault(); openMenu(sourceItems(key), ev.clientX, ev.clientY); };
  }

  // ─────────────────────────────────────────────
  //  Différences
  // ─────────────────────────────────────────────
  const both = () => !!S.stats;
  const openCount = () => S.blocks.filter((b) => !b.choice).length;

  function render() {
    renderSources();
    renderBar();
    renderGrid();
    renderAi();
    renderThread();
  }

  function renderBar() {
    const n = S.blocks.length;
    bar.hidden = !both() || !n;
    if (bar.hidden) return;
    const st = S.stats;
    const counts = [[st.changed, "modifiée", "modifiées", "chg"], [st.removed, "seulement à gauche", "seulement à gauche", "del"],
      [st.added, "seulement à droite", "seulement à droite", "add"]].filter(([v]) => v > 0);
    statsEl.replaceChildren(
      h("b", { text: plural(n, "différence", "différences") }),
      ...counts.map(([v, one, many, cls]) => h("span", { class: `cmp-stat ${cls}`, text: `${v.toLocaleString("fr-FR")} ${v > 1 ? many : one}` })));
    statsEl.dataset.tip = [...counts.map(([v, one, many]) => `${plural(v, "ligne", "lignes")} ${v > 1 ? many : one}`),
      `${plural(st.same, "ligne identique", "lignes identiques")}`, `textes semblables à ${st.similarity} %`].join(" · ");
    posEl.textContent = S.current >= 0 ? `${S.current + 1} / ${n}` : "";
    prevBtn.disabled = S.current === 0;
    nextBtn.disabled = S.current === n - 1;
    const open = openCount();
    mergeEl.textContent = open ? `${n - open} / ${n} choisies` : "Tout est choisi";
    mergeEl.classList.toggle("done", !open);
    mergeEl.dataset.tip = open ? `${plural(open, "différence n'a", "différences n'ont")} pas encore de choix`
      : "Chaque différence a un choix : le résultat est prêt";
    const r = S.resolving;
    resolvingEl.hidden = !r;
    aiPickBtn.hidden = !!r;
    if (r) {
      resolvingEl.replaceChildren(h("span", { class: "pane-busy" }),
        h("span", { text: `IA : ${Math.min(r.done + 1, r.total)} / ${r.total}` }),
        iconButton("square", "Arrêter l'IA", () => api.compare_resolve_stop(), { size: 12, cls: "sm" }));
    }
    aiPickBtn.disabled = !open;
    aiPickBtn.dataset.tip = open ? `L'IA propose un choix pour ${plural(open, "différence", "différences")} sans choix`
      : "Toutes les différences ont un choix";
  }

  function renderGrid() {
    blockEls.clear();
    const { left, right } = S.sides;
    if (!both()) {
      grid.replaceChildren();
      showMessage(!left && !right ? ["git-compare", "Comparez deux textes",
        "Deux versions d'un fichier, d'un contrat, d'un code, ou deux textes collés : les différences mot à mot, puis la "
        + "fusion, différence par différence. L'IA explique ce qui a changé et propose quoi garder."]
        : (left?.error || right?.error) ? ["triangle-alert", "Un des deux fichiers est illisible", (left?.error || right?.error)]
          : ["git-compare", `Ajoutez le texte de ${left ? "droite" : "gauche"}`,
            "Choisissez un fichier, collez un texte ou glissez un fichier sur l'emplacement vide, en haut."]);
      return;
    }
    if (!S.blocks.length) {
      grid.replaceChildren();
      const ignored = [S.options.space && "les espaces", S.options.case && "les majuscules"].filter(Boolean);
      showMessage(["circle-check", "Les deux textes sont identiques",
        ignored.length ? `(en ignorant ${ignored.join(" et ")})` : `${plural(S.lines.left.length, "ligne", "lignes")}, aucune différence.`]);
      return;
    }
    message.hidden = true;
    const a = S.lines.left;
    const out = [];
    let rows = 0;
    let ia = 0;
    let ib = 0;
    for (let k = 0; k <= S.blocks.length; k++) {
      const blk = S.blocks[k];
      rows += sameRows(out, ia, blk ? blk.a0 : a.length, ib, k === 0, !blk);
      if (!blk) break;
      if (rows > S.rowLimit) {
        out.push(h("button", { class: "cmp-more", type: "button", onClick: () => { S.rowLimit += ROW_LIMIT; renderGrid(); } },
          ico("arrow-down", 13), `Afficher la suite (${plural(S.blocks.length - k, "différence", "différences")})`));
        break;
      }
      out.push(blockEl(k));
      rows += blk.rows.length;
      ia = blk.a1;
      ib = blk.b1;
    }
    const digits = String(Math.max(a.length, S.lines.right.length)).length;
    grid.style.setProperty("--ln", `${digits + 2}ch`);
    grid.replaceChildren(...out);
  }

  function showMessage([icon, title, text]) {
    message.hidden = false;
    message.replaceChildren(h("div", { class: "glyph" }, ico(icon, 22)), h("h3", { text: title }), h("p", { text }));
  }

  /** Lignes identiques [a0, a1) à gauche (b0… à droite) : seules celles autour des
   *  différences, le reste replié. → lignes affichées. */
  function sameRows(out, a0, a1, b0, first, last) {
    const n = a1 - a0;
    if (n <= 0) return 0;
    const row = (i) => sameRow(i, b0 + (i - a0));
    const head = first ? 0 : CONTEXT;
    const tail = last ? 0 : CONTEXT;
    const hidden = n - head - tail;
    if (S.showSame || hidden < GAP_MIN) {
      for (let i = a0; i < a1; i++) out.push(row(i));
      return n;
    }
    const start = a0 + head;
    const revealed = Math.min(hidden, S.expanded.get(start) || 0);
    for (let i = a0; i < start + revealed; i++) out.push(row(i));
    const rest = hidden - revealed;
    if (rest > 0) {
      out.push(h("button", { class: "cmp-gap", type: "button", dataset: { tip: rest > GAP_STEP ? `Afficher ${GAP_STEP} lignes de plus` : "Afficher ces lignes" },
        onClick: () => { S.expanded.set(start, revealed + Math.min(rest, GAP_STEP)); renderGrid(); } },
      ico("unfold-vertical", 13), `${plural(rest, "ligne identique", "lignes identiques")}`));
    }
    for (let i = a1 - tail; i < a1; i++) out.push(row(i));
    return head + revealed + tail + (rest > 0 ? 1 : 0);
  }

  function sameRow(i, j) {
    return h("div", { class: "cmp-row same" },
      h("span", { class: "cmp-ln", text: String(i + 1) }), h("span", { class: "cmp-l cmp-text", text: S.lines.left[i] }),
      h("span", { class: "cmp-gut" }),
      h("span", { class: "cmp-ln", text: String(j + 1) }), h("span", { class: "cmp-r cmp-text", text: S.lines.right[j] }));
  }

  /** Texte d'une ligne, parties changées marquées ; null : pas de ligne de ce côté. */
  function textCell(side, text, spans, kind) {
    if (text == null) return h("span", { class: `${side} cmp-text filler` });
    const cell = h("span", { class: `${side} cmp-text ${kind}` });
    let pos = 0;
    for (const [s, e] of spans || []) {
      if (s > pos) cell.append(text.slice(pos, s));
      cell.append(h("mark", { text: text.slice(s, e) }));
      pos = e;
    }
    if (pos < text.length) cell.append(text.slice(pos));
    return cell;
  }

  const kept = (choice) => ({
    left: ["left", "left_right", "right_left"].includes(choice),
    right: ["right", "left_right", "right_left"].includes(choice),
  });

  function blockEl(k) {
    const blk = S.blocks[k];
    const { left: a, right: b } = S.lines;
    const keep = kept(blk.choice);
    const cls = ["cmp-block", blk.choice ? `chosen ch-${blk.choice}` : "open", keep.left ? "keep-l" : "",
      keep.right ? "keep-r" : "", k === S.current ? "current" : "", blk.ai ? `ai-${blk.ai.state}` : ""];
    const rows = blk.rows.map(([i, j, ls, rs], r) => h("div", { class: "cmp-row" },
      h("span", { class: "cmp-ln", text: i != null ? String(i + 1) : "" }),
      textCell("cmp-l", i != null ? a[i] : null, ls, "del"),
      r === 0 ? gutter(blk) : h("span", { class: "cmp-gut" }),
      h("span", { class: "cmp-ln", text: j != null ? String(j + 1) : "" }),
      textCell("cmp-r", j != null ? b[j] : null, rs, "add")));
    const blockNode = h("div", { class: cls.filter(Boolean).join(" "), dataset: { index: String(k) } }, ...rows, ...blockNotes(blk));
    blockNode.addEventListener("click", (ev) => { if (!ev.target.closest("button")) setCurrent(k, false); });
    blockNode.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      setCurrent(k, false);
      openMenu(blockItems(k), ev.clientX, ev.clientY);
    });
    blockEls.set(k, blockNode);
    return blockNode;
  }

  /** Boutons de la différence, entre les deux colonnes : garder la gauche, autres choix, garder la droite. */
  function gutter(blk) {
    const keep = kept(blk.choice);
    const hasL = blk.a1 > blk.a0;
    const hasR = blk.b1 > blk.b0;
    const keepBtn = (side, on) => {
      const tip = side === "left"
        ? (hasL ? "Garder la gauche" : "Garder la gauche : sans ces lignes")
        : (hasR ? "Garder la droite" : "Garder la droite : sans ces lignes");
      return h("button", { class: `cmp-keep ${side}${on ? " on" : ""}`, type: "button", "aria-pressed": String(on),
        "aria-label": tip, dataset: { tip: on && blk.choice === side ? `${tip} (cliquer : annuler)` : tip, kbd: side === "left" ? "Alt+←" : "Alt+→" },
        onClick: () => choose(blk.index, blk.choice === side ? null : side) }, ico(side === "left" ? "arrow-left" : "arrow-right", 13));
    };
    const ai = blk.ai?.state;
    const status = ai === "running" ? ["loader-circle", "L'IA choisit…"] : ai === "waiting" ? ["clock", "En attente de l'IA"]
      : blk.by === "ia" ? ["sparkles", `Choix de l'IA : ${CHOICE_TEXT[blk.choice]}${blk.reason ? ` — ${blk.reason}` : ""}`]
        : blk.choice === "text" ? ["pencil", "Version écrite à la main"]
          : blk.choice ? ["ellipsis", `${CHOICE_TEXT[blk.choice]} — autres choix`] : ["ellipsis", "Autres choix"];
    const menuBtn = h("button", { class: `cmp-menu${blk.by === "ia" ? " ia" : ""}${ai ? ` ${ai}` : ""}`, type: "button",
      "aria-label": status[1], dataset: { tip: status[1] },
      onClick: (ev) => { setCurrent(blk.index, false); openMenuBelow(blockItems(blk.index), ev.currentTarget); } },
    ico(status[0], 13));
    return h("span", { class: "cmp-gut" }, keepBtn("left", keep.left), menuBtn, keepBtn("right", keep.right));
  }

  /** Sous la différence : version fusionnée, raison du choix de l'IA, échec. */
  function blockNotes(blk) {
    const out = [];
    if (blk.choice === "text") {
      const lines = (blk.text || "").split("\n");
      out.push(h("div", { class: `cmp-merged${blk.by === "ia" ? " ia" : ""}` },
        h("div", { class: "cmp-merged-head" },
          ico(blk.by === "ia" ? "sparkles" : "pencil", 13),
          h("b", { text: blk.by === "ia" ? "Version fusionnée par l'IA" : "Version écrite" }),
          blk.reason ? h("span", { class: "cmp-reason", text: blk.reason }) : null,
          h("div", { class: "head-spacer" }),
          h("button", { class: "btn ghost sm", type: "button", onClick: () => editMerged(blk.index) }, ico("pencil", 13), "Modifier"),
          h("button", { class: "btn ghost sm", type: "button", onClick: () => choose(blk.index, null) }, ico("undo-2", 13), "Annuler")),
        h("pre", { class: "cmp-merged-text", text: blk.text ? lines.join("\n") : "(aucune ligne)" })));
    } else if (blk.by === "ia" && blk.reason) {
      out.push(h("div", { class: "cmp-note ia" }, ico("sparkles", 12),
        h("span", {}, h("b", { text: `${CHOICE_TEXT[blk.choice][0].toUpperCase()}${CHOICE_TEXT[blk.choice].slice(1)} : ` }), blk.reason)));
    }
    if (blk.ai?.state === "error") {
      out.push(h("div", { class: "cmp-note error" }, ico("triangle-alert", 12),
        h("span", { text: `L'IA n'a pas pu choisir : ${blk.ai.error || "échec"}` }),
        h("button", { class: "btn ghost sm", type: "button", onClick: () => resolve([blk.index]) }, "Réessayer")));
    }
    return out;
  }

  function blockItems(k) {
    const blk = S.blocks[k];
    const c = blk.choice;
    const hasBoth = blk.a1 > blk.a0 && blk.b1 > blk.b0;
    const sel = window.getSelection().toString();
    const { left: a, right: b } = S.lines;
    return [
      sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
      sel ? "-" : null,
      { section: `Différence ${k + 1} — ${where(blk)}` },
      { label: "Garder la gauche", icon: "arrow-left", hint: "Alt+←", checked: c === "left", onSelect: () => choose(k, "left") },
      { label: "Garder la droite", icon: "arrow-right", hint: "Alt+→", checked: c === "right", onSelect: () => choose(k, "right") },
      { label: "Garder les deux : gauche puis droite", checked: c === "left_right", disabled: !hasBoth, onSelect: () => choose(k, "left_right") },
      { label: "Garder les deux : droite puis gauche", checked: c === "right_left", disabled: !hasBoth, onSelect: () => choose(k, "right_left") },
      "-",
      store.ollama ? { label: blk.by === "ia" ? "Redemander à l'IA" : "Fusionner avec l'IA", icon: "sparkles",
        disabled: !!S.resolving, onSelect: () => resolve([k]) } : null,
      { label: c === "text" ? "Modifier la version fusionnée…" : "Écrire la version fusionnée…", icon: "pencil", onSelect: () => editMerged(k) },
      c ? { label: "Annuler le choix", icon: "undo-2", onSelect: () => choose(k, null) } : null,
      "-",
      { label: "Copier la gauche", icon: "copy", disabled: blk.a1 === blk.a0, onSelect: () => ctx.copy(a.slice(blk.a0, blk.a1).join("\n")) },
      { label: "Copier la droite", icon: "copy", disabled: blk.b1 === blk.b0, onSelect: () => ctx.copy(b.slice(blk.b0, blk.b1).join("\n")) },
    ].filter(Boolean);
  }

  function where(blk) {
    const range = (s, e) => (e - s === 1 ? `ligne ${s + 1}` : `lignes ${s + 1} à ${e}`);
    if (blk.a0 === blk.a1) return `seulement à droite`;
    if (blk.b0 === blk.b1) return `seulement à gauche`;
    return range(blk.a0, blk.a1);
  }

  function setCurrent(k, scrollTo = true) {
    if (k < 0 || k >= S.blocks.length) return;
    blockEls.get(S.current)?.classList.remove("current");
    S.current = k;
    if (!blockEls.has(k)) {          // au-delà des lignes affichées
      S.rowLimit = Infinity;
      renderGrid();
    }
    const node = blockEls.get(k);
    node?.classList.add("current");
    if (scrollTo) node?.scrollIntoView({ block: "center", behavior: "smooth" });
    renderBar();
  }

  /** Différence suivante (1) ou précédente (-1) ; la première, depuis le haut de l'affichage. */
  function go(delta) {
    const n = S.blocks.length;
    if (!n) return;
    if (S.current < 0) {
      const top = scroll.getBoundingClientRect().top;
      const below = [...blockEls.entries()].find(([, node]) => node.getBoundingClientRect().bottom > top + 4);
      setCurrent(delta > 0 ? (below ? below[0] : 0) : n - 1);
      return;
    }
    setCurrent(Math.max(0, Math.min(n - 1, S.current + delta)));
  }

  /** Alt+← / Alt+→ : garder ce côté pour la différence sélectionnée (encore : annuler). */
  function keep(side) {
    const blk = S.blocks[S.current];
    if (blk) choose(S.current, blk.choice === side ? null : side);
  }

  async function choose(k, choice, text = "") {
    const res = await api.compare_choose(k, choice, text);
    if (!res || res.error) {
      if (res?.error) { toast(res.error, "error"); refresh(); }
      return;
    }
    updateBlock(res);
  }

  function updateBlock(blk) {
    if (!S.blocks[blk.index] || S.blocks[blk.index].sig !== blk.sig) return;
    S.blocks[blk.index] = blk;
    blockEls.get(blk.index)?.replaceWith(blockEl(blk.index));
    renderBar();
    renderAi();
  }

  async function chooseAll(choice, onlyOpen = false) {
    apply(await api.compare_choose_all(choice, onlyOpen));
  }

  function allItems() {
    const open = openCount();
    return [
      { section: "Toutes les différences" },
      { label: "Garder la gauche partout", icon: "arrow-left", onSelect: () => chooseAll("left") },
      { label: "Garder la droite partout", icon: "arrow-right", onSelect: () => chooseAll("right") },
      { label: "Garder les deux partout", onSelect: () => chooseAll("left_right") },
      "-",
      { section: `Sans choix (${open})` },
      { label: "Garder la gauche", icon: "arrow-left", disabled: !open, onSelect: () => chooseAll("left", true) },
      { label: "Garder la droite", icon: "arrow-right", disabled: !open, onSelect: () => chooseAll("right", true) },
      "-",
      { label: "Annuler tous les choix", icon: "undo-2", disabled: open === S.blocks.length, onSelect: () => chooseAll(null) },
    ];
  }

  function optionItems() {
    const has = !!(S.sides.left || S.sides.right);
    return [
      { section: "Comparaison" },
      { label: "Ignorer les espaces", checked: S.options.space, onSelect: () => setOptions({ ...S.options, space: !S.options.space }) },
      { label: "Ignorer les majuscules", checked: S.options.case, onSelect: () => setOptions({ ...S.options, case: !S.options.case }) },
      { label: "Afficher les lignes identiques", checked: S.showSame, onSelect: () => { S.showSame = !S.showSame; renderGrid(); } },
      "-",
      { label: "Relire les fichiers", icon: "refresh-cw", hint: "F5", disabled: !has, onSelect: () => reload() },
      { label: "Inverser gauche et droite", icon: "arrow-left-right", disabled: !has, onSelect: () => swap() },
      { label: "Tout retirer", icon: "x", disabled: !has, onSelect: async () => { await clearSide("left"); clearSide("right"); } },
    ];
  }

  /** Version fusionnée écrite à la main (les deux versions rappelées au-dessus). */
  function editMerged(k) {
    const blk = S.blocks[k];
    const left = S.lines.left.slice(blk.a0, blk.a1);
    const right = S.lines.right.slice(blk.b0, blk.b1);
    const current = blk.choice === "text" ? blk.text
      : blk.choice === "right" ? right.join("\n")
        : blk.choice === "left_right" ? [...left, ...right].join("\n")
          : blk.choice === "right_left" ? [...right, ...left].join("\n") : left.join("\n");
    const ref = (title, lines) => h("div", { class: "cmp-ref-side" }, h("div", { class: "cmp-ref-title", text: title }),
      h("pre", { class: "cmp-ref-text", text: lines.length ? lines.join("\n") : "(aucune ligne)" }));
    const area = h("textarea", { class: "field mono cmp-edit-area", spellcheck: "false", autofocus: true,
      "aria-label": "Version fusionnée" });
    area.value = current;
    openModal({
      title: `Différence ${k + 1} : version fusionnée`,
      subtitle: "Ce texte remplace la différence dans le résultat.",
      width: 860,
      body: h("div", { class: "cmp-edit" }, h("div", { class: "cmp-refs" }, ref("Gauche", left), ref("Droite", right)), area),
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: "Valider", icon: "check", kind: "primary", onClick: () => { choose(k, "text", area.value); } },
      ],
    });
  }

  // ─────────────────────────────────────────────
  //  Résultat
  // ─────────────────────────────────────────────
  async function openResult() {
    let fallback = "left";
    let res = await api.compare_result(fallback);
    if (!res || res.error) { if (res?.error) toast(res.error, "error"); return; }
    let edited = false;
    const area = h("textarea", { class: "field mono cmp-result-area", spellcheck: "false", "aria-label": "Texte fusionné" });
    area.value = res.text;
    const info = h("span", { class: "cmp-result-info" });
    const warn = h("div", { class: "cmp-result-warn", hidden: true });
    const showInfo = () => {
      const lines = area.value ? area.value.replace(/\n$/, "").split("\n").length : 0;
      info.textContent = `${plural(lines, "ligne", "lignes")}${edited ? " · retouché ici" : ""}`;
    };
    area.addEventListener("input", () => { edited = true; showInfo(); seg.querySelectorAll("button").forEach((x) => { x.disabled = true; }); });
    const seg = h("div", { class: "segmented" }, ...SIDES.map((key) => h("button", { type: "button", text: SIDE_NAMES[key].toLowerCase(),
      class: key === fallback ? "active" : "", dataset: { side: key }, onClick: async () => {
        if (edited || key === fallback) return;
        fallback = key;
        res = await api.compare_result(fallback);
        if (!res || res.error) return;
        area.value = res.text;
        area.setSelectionRange(0, 0);
        area.scrollTop = 0;
        for (const btn of seg.children) btn.classList.toggle("active", btn.dataset.side === fallback);
        showInfo();
      } })));
    const unresolved = res.unresolved ? h("div", { class: "cmp-unresolved" }, ico("info", 14),
      h("span", { text: `${plural(res.unresolved, "différence n'a", "différences n'ont")} pas de choix : on y garde la version de` }), seg) : null;
    showInfo();
    const writable = (key) => S.sides[key]?.writable;
    let armed = "";                 // remplacement à confirmer (second clic)
    let utf8 = false;
    const saveAction = (target, label) => ({
      label, icon: target === "as" ? "save" : "file", kind: target === "as" ? "primary" : "",
      onClick: async (btn) => {
        if (target !== "as" && armed !== target) {
          armed = target;
          btn.lastChild.textContent = `Confirmer : ${label.toLowerCase()}`;
          warn.hidden = false;
          warn.replaceChildren(ico("triangle-alert", 14), h("span", { text: `« ${S.sides[target].name} » sera remplacé par le résultat ; `
            + "l'ancienne version ira à la corbeille. Cliquez à nouveau pour confirmer." }));
          return false;
        }
        const out = await api.compare_save(target, area.value, utf8);
        if (out == null) return false;             // fenêtre Enregistrer fermée
        if (out.error) { toast(out.error, "error"); return false; }
        if (!out.ok && out.encoding) {
          utf8 = true;
          warn.hidden = false;
          warn.replaceChildren(ico("triangle-alert", 14), h("span", { text: `${out.error} Cliquez à nouveau pour enregistrer en UTF-8.` }));
          return false;
        }
        apply(out.state);
        toast(`Enregistré : ${baseName(out.path)}`);
        return true;
      },
    });
    openModal({
      title: "Résultat de la fusion",
      subtitle: "Parties identiques, plus le choix fait pour chaque différence. Vous pouvez encore retoucher le texte ici.",
      width: 980,
      body: h("div", { class: "cmp-result" }, unresolved, area, h("div", { class: "cmp-result-foot" }, info), warn),
      actions: [
        { label: "Copier", icon: "copy", left: true, iconOnly: true, onClick: () => { ctx.copy(area.value, "Résultat copié"); return false; } },
        { label: "Ajouter à une nouvelle note", icon: "notebook-pen", left: true, iconOnly: true,
          onClick: () => { ctx.emit("note:insert", { text: area.value, slot: "new" }); return true; } },
        writable("left") ? saveAction("left", "Remplacer la gauche") : null,
        writable("right") ? saveAction("right", "Remplacer la droite") : null,
        saveAction("as", "Enregistrer sous…"),
      ].filter(Boolean),
    });
    setTimeout(() => { area.focus(); area.setSelectionRange(0, 0); area.scrollTop = 0; }, 40);
  }

  // ─────────────────────────────────────────────
  //  IA
  // ─────────────────────────────────────────────
  function setAi(on, persist = true) {
    aiOpen = on;
    aiPanel.hidden = !on;
    aiBtn.classList.toggle("on", on);
    aiBtn.dataset.tip = on ? "Masquer le panneau de l'IA" : "IA : explication, questions, fusion";
    aiBtn.setAttribute("aria-label", aiBtn.dataset.tip);
    if (persist) {
      try { localStorage.setItem(AI_KEY, on ? "1" : "0"); } catch { /* stockage indisponible */ }
    }
  }

  function setHint(text) {
    hintInput.value = text;
    S.hint = text;
    api.compare_hint(text);
    hintInput.focus();
  }

  async function explain() {
    if (aiPanel.hidden) setAi(true);
    const res = await api.compare_explain();
    if (!res?.ok && res?.error) toast(res.error, "error");
  }

  /** Explication d'office (réglage), une fois par comparaison, section affichée. */
  function maybeExplain() {
    if (!store.ollama || store.settings.compare_auto_explain === false || !ctx.isActive("compare")) return;
    if (!both() || !S.blocks.length || S.thread.some((e) => e.kind === "explain") || S.autoKey === S.key) return;
    S.autoKey = S.key;
    api.compare_explain();
  }

  /** preset : question d'une suggestion (le champ garde son texte), sinon celle du champ. */
  async function ask(preset) {
    const question = (preset ?? composer.input.value).trim();
    if (!question) return;
    const res = await api.compare_ask(question);
    if (res?.ok) { if (preset === undefined) composer.setValue(""); }
    else if (res?.error) toast(res.error, "error");
  }

  async function resolve(indices) {
    const res = await api.compare_resolve(indices);
    if (!res?.ok && res?.error) toast(res.error, "error");
  }

  function modelItems() {
    return [
      { section: "Modèle de l'IA" },
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === S.model, onSelect: async () => {
          await ctx.saveSetting("compare_model", m);
          S.model = m;
          renderAi();
        } }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Expliquer automatiquement", checked: store.settings.compare_auto_explain !== false,
        onSelect: () => ctx.saveSetting("compare_auto_explain", store.settings.compare_auto_explain === false) },
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  function renderAi() {
    const ollama = !!store.ollama;
    const n = S.blocks.length;
    const ready = ollama && both() && n > 0;
    const explanation = S.thread.find((e) => e.kind === "explain");
    const busy = !!busyEntry();
    explainBtn.hidden = !ready;
    explainBtn.disabled = !!explanation && BUSY.has(explanation.state);
    explainBtn.replaceChildren(ico(explanation ? "rotate-ccw" : "sparkles", 14), explanation ? "Recommencer" : "Expliquer");
    let note = "";
    if (!ollama) note = "Ollama ne répond pas : l'IA est indisponible. La comparaison et la fusion restent possibles.";
    else if (!both()) note = "Ajoutez deux textes : l'IA expliquera leurs différences et vous aidera à les fusionner.";
    else if (!n) note = "Les deux textes sont identiques : rien à expliquer ni à fusionner.";
    else if (!explanation && store.settings.compare_auto_explain === false) note = "Explication automatique désactivée (menu du modèle).";
    aiNote.hidden = !note;
    aiNote.textContent = note;
    mergeCard.hidden = !ready;
    const open = openCount();
    resolveBtn.hidden = !!S.resolving;
    resolveBtn.disabled = !open;
    resolveBtn.replaceChildren(ico("sparkles", 14), open ? `Choisir pour ${plural(open, "différence", "différences")}` : "Tout est choisi");
    resolveInfo.replaceChildren(...(S.resolving
      ? [h("span", { class: "pane-busy" }), `Différence ${Math.min(S.resolving.done + 1, S.resolving.total)} sur ${S.resolving.total}…`,
        h("button", { class: "btn ghost sm", type: "button", onClick: () => api.compare_resolve_stop() }, "Arrêter")]
      : open ? [] : ["Clic droit sur une différence : « Redemander à l'IA »."]));
    composer.update({ busy, ready: ollama && both() });
    composer.input.disabled = !ollama;
    const answered = S.thread.some((e) => e.state === "done" && e.answer);
    suggest.set(ready ? [...SUGGESTIONS, ...(answered ? FOLLOW_UPS : [])] : [], busy);
    modelName.textContent = S.model || "Aucun modèle";
    modelBtn.hidden = !ollama;
  }

  const sourceOf = (id) => `compare:${id}`;
  const titleOf = (entry) => (entry.kind === "explain" ? "Explication des différences" : entry.question);
  const busyEntry = () => [...answers.values()].find((e) => BUSY.has(e.item.state)) || null;

  function autoRead(entry) {
    entry.autoRead = true;
    ctx.speak(entry.item.answer, sourceOf(entry.item.id), true, true);
  }

  function threadSpeech(mode) {
    return conversationSpeech(S.thread.map((e) => answers.get(e.id)?.item || e)
      .map((e) => ({ ask: titleOf(e), answer: e.answer })), mode);
  }

  function feedAnswer(entry) {
    const final = !BUSY.has(entry.item.state);
    ctx.feed(sourceOf(entry.item.id), entry.item.answer, final);
    if (ctx.feeding(READ_ALL)) ctx.feed(READ_ALL, threadSpeech(readingMode), final);
  }

  /** « différence 3 » dans une réponse : lien vers la différence. */
  function linkDifferences(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) if (/différences?\s+(n°\s*)?\d/i.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
    for (const node of nodes) {
      if (node.parentElement.closest("code, pre, a, button")) continue;
      const frag = document.createDocumentFragment();
      const text = node.nodeValue;
      const re = /(différences?\s+(?:n°\s*)?)(\d+)/gi;
      let pos = 0;
      let m;
      while ((m = re.exec(text))) {
        const k = Number(m[2]) - 1;
        if (k < 0 || k >= S.blocks.length) continue;
        frag.append(text.slice(pos, m.index), h("button", { class: "cmp-ref", type: "button", text: m[0],
          dataset: { tip: "Afficher cette différence" }, onClick: () => setCurrent(k) }));
        pos = m.index + m[0].length;
      }
      if (!pos) continue;
      frag.append(text.slice(pos));
      node.replaceWith(frag);
    }
  }

  function qaEl(itm) {
    const source = sourceOf(itm.id);
    const answer = h("div", { class: "md asst-answer" });
    const phase = h("div", { class: "asst-phase" });
    const meta = h("div", { class: "asst-meta" });
    const speak = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(entry.item.answer, source, true), { size: 15, cls: "sm" });
    speak.dataset.speakSource = source;
    speak.dataset.tipIdle = "Lire";
    const retry = () => (entry.item.kind === "explain" ? explain()
      : api.compare_ask(entry.item.question).then((res) => { if (!res?.ok && res?.error) toast(res.error, "error"); }));
    const actions = h("div", { class: "asst-actions" },
      iconButton("copy", "Copier la réponse", () => ctx.copy(entry.item.answer, "Copié"), { size: 15, cls: "sm" }),
      speak,
      iconButton("notebook-pen", "Ajouter à une note", (ev) => ctx.pickDestination("notes", ev.currentTarget,
        (slot) => ctx.emit("note:insert", { text: noteText(entry.item), slot })), { size: 15, cls: "sm" }),
      iconButton("rotate-ccw", itm.kind === "explain" ? "Recommencer" : "Reposer la question", retry, { size: 15, cls: "sm" }),
      iconButton("x", "Retirer", () => api.compare_delete(itm.id), { size: 15, cls: "sm" }));
    const reply = h("div", { class: "asst-reply" }, phase, answer, meta, actions);
    const question = h("div", { class: `asst-question${itm.kind === "explain" ? " fx-task" : ""}` });
    const root = h("div", { class: "asst-qa", dataset: { id: itm.id } }, question, reply);
    reply.addEventListener("contextmenu", (ev) => {
      if (ev.target.closest(".cmp-ref")) return;
      const sel = window.getSelection().toString();
      ev.preventDefault();
      openMenu([
        sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
        { label: "Copier la réponse", icon: "copy", onSelect: () => ctx.copy(entry.item.answer, "Copié") },
        { label: "Lire", icon: "volume-2", onSelect: () => ctx.speak(entry.item.answer, source, true) },
        { label: "Ajouter à une note", icon: "notebook-pen", onSelect: () => ctx.emit("note:insert", { text: noteText(entry.item), slot: "new" }) },
        "-",
        { label: "Retirer", icon: "x", onSelect: () => api.compare_delete(itm.id) },
      ].filter(Boolean), ev.clientX, ev.clientY);
    });
    const entry = { item: itm, root, question, answer, phase, meta, actions, reply, shown: null };
    answers.set(itm.id, entry);
    updateQa(entry);
    return root;
  }

  function noteText(itm) {
    const names = SIDES.map((k) => S.sides[k]?.name).filter(Boolean).join(" et ");
    return `## ${itm.kind === "explain" ? `Différences entre ${names}` : itm.question}\n\n${itm.answer}`;
  }

  function updateQa(entry) {
    const itm = entry.item;
    const busy = BUSY.has(itm.state);
    entry.question.replaceChildren(itm.kind === "explain" ? ico("sparkles", 13) : "", h("span", { text: titleOf(itm) }));
    entry.reply.classList.toggle("busy", busy);
    entry.phase.hidden = !busy || (itm.state === "thinking" && !!itm.answer);
    entry.phase.replaceChildren(h("span", { class: "pane-busy" }), h("span", { text: itm.phase || "…" }));
    if (entry.shown !== itm.answer) {
      entry.shown = itm.answer;
      entry.answer.innerHTML = itm.answer ? renderMarkdown(itm.answer, { highlight: !busy }) : "";
      linkDifferences(entry.answer);
    }
    entry.answer.hidden = !itm.answer;
    const notes = [];
    if (itm.note) notes.push(h("span", { class: "asst-note", text: itm.note }));
    if (itm.state === "stopped") notes.push(h("span", { class: "asst-note", text: "Arrêtée." }));
    if (itm.state === "error") notes.push(h("span", { class: "asst-note error", text: itm.error || "Échec." }));
    const at = parseDate(itm.at);
    entry.meta.replaceChildren(...notes, busy ? "" : h("span", { class: "asst-model", text: `${itm.model}${at ? ` · ${formatShort(at)}` : ""}` }));
    entry.actions.hidden = busy || !itm.answer;
  }

  function renderThread() {
    answers.clear();
    thread.replaceChildren(...S.thread.map(qaEl));
    renderAi();
    ctx.emit("speak-sources-changed");
  }

  const nearBottom = () => aiScroll.scrollHeight - aiScroll.scrollTop - aiScroll.clientHeight < 80;
  const scrollDown = () => { aiScroll.scrollTop = aiScroll.scrollHeight; };

  on("compare:qa", ({ item: itm }) => {
    const i = S.thread.findIndex((e) => e.id === itm.id);
    if (i >= 0) S.thread[i] = itm;
    else if (itm.kind === "explain") { S.thread = S.thread.filter((e) => e.kind !== "explain"); S.thread.unshift(itm); }
    else S.thread.push(itm);
    const entry = answers.get(itm.id);
    if (entry) {
      entry.item = itm;
      updateQa(entry);
      if (itm.state === "thinking" && !entry.autoRead && composer.autoRead()) autoRead(entry);
      feedAnswer(entry);
      renderAi();
      return;
    }
    renderThread();
    if (itm.kind !== "explain") scrollDown();
  });

  on("compare:delta", ({ id, text }) => {
    const entry = answers.get(id);
    const stored = S.thread.find((e) => e.id === id);
    if (!entry) { if (stored) stored.answer = (stored.answer || "") + text; return; }
    const follow = nearBottom();
    entry.item = { ...entry.item, answer: (entry.item.answer || "") + text, state: "thinking" };
    if (stored) stored.answer = entry.item.answer;
    updateQa(entry);
    feedAnswer(entry);
    if (follow && entry.item.kind !== "explain") scrollDown();
  });

  on("compare:thread", ({ items }) => {
    S.thread = items || [];
    renderThread();
  });

  on("compare:block", ({ block }) => updateBlock(block));

  on("compare:resolve", (data) => {
    S.resolving = data.finished ? null : data;
    renderBar();
    renderAi();
    if (!data.finished || data.stopped || !data.count) return;
    if (data.failures) {
      toast(`L'IA n'a pas pu choisir pour ${plural(data.failures, "différence", "différences")} : voir sous chacune.`, "error");
    } else if (data.count > 1) {
      toast(`L'IA a fait ${data.count} choix : vérifiez-les (✦) avant d'enregistrer le résultat.`, "success", 4000);
    }
  });

  on("chat:models", ({ models }) => {
    S.models = models || [];
    api.compare_model().then((m) => { S.model = m || ""; renderAi(); });
  });
  on("chat:available", () => { renderAi(); maybeExplain(); });
  on("setting", ({ key }) => {
    if (key === "compare_model") api.compare_model().then((m) => { S.model = m || ""; renderAi(); });
  });

  // Section affichée avant que l'état ne soit chargé : sources de la session précédente
  renderSources();

  return {
    el,
    onShow() {
      if (aiOpen === null) aiOpen = el.clientWidth >= AI_WIDE && !!store.ollama;
      setAi(aiOpen, false);
      // Fichiers relus s'ils ont changé sur le disque depuis le dernier affichage
      refresh(S.loaded);
    },
    /** Glisser-déposer : sur un côté, ou deux fichiers à comparer. */
    async onDrop(paths, zone) {
      if (zone === "left" || zone === "right") {
        if (await load(zone, paths[0]) && paths[1]) await load(OTHER[zone], paths[1]);
        return;
      }
      if (paths.length >= 2) {
        if (await load("left", paths[0])) await load("right", paths[1]);
        return;
      }
      await load(!S.sides.left ? "left" : "right", paths[0]);
    },
    /** Depuis une autre section : { action: "add", path } (un fichier à comparer), { action: "open" }. */
    reveal(target) {
      if (target?.action === "add" && target.path) {
        load(!S.sides.left ? "left" : !S.sides.right ? "right" : "right", target.path);
      } else {
        const empty = SIDES.find((k) => !S.sides[k]);
        if (empty) sourceEls[empty].querySelector("button")?.focus();
      }
    },
    focus() {
      if (both()) scroll.focus();
      else sourceEls[SIDES.find((k) => !S.sides[k]) || "left"].querySelector("button")?.focus();
    },
  };
}
