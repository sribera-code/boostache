// Vue Fichiers : tout sur un fichier ou un dossier déposé (files.py) — fiche, aperçu,
// contenu d'un dossier, propriétés de Windows, processus qui le bloquent (locks.py) —
// avec un résumé et des questions à Ollama.
import { h, plural, parseDate, formatShort, formatFullDate } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, openModal, toast } from "../ui.js";
import { renderMarkdown } from "../markdown.js";
import { createComposer, createSuggestions, conversationSpeech } from "../composer.js";

const MAX_ITEMS = 12;
const BUSY = new Set(["reading", "thinking"]);
const READ_ALL = "files:all";
const USES_SHOWN = 3;        // processus : éléments utilisés montrés avant « N autres »
const DENIED_SHOWN = 6;
const CLOSE_WAIT = 1500;     // fermeture demandée : nouvelle analyse après (ms)
const LOCKS_STALE = 5000;    // section réaffichée : processus réexaminés si l'analyse est plus ancienne (ms)
const TEXT_BOX = 2000;       // aperçu de texte : au-delà, replié derrière « Afficher plus »

const LOCK_KINDS = {
  exe:  { icon: "app-window", tip: "Programme lancé depuis cet emplacement" },
  dll:  { icon: "box",        tip: "Bibliothèque (DLL) chargée" },
  dir:  { icon: "folder",     tip: "Dossier ouvert (dossier courant d'une console, surveillance…)" },
  file: { icon: "file",       tip: "Fichier ouvert" },
};
// Répartition par type : une teinte fixe par catégorie (palette catégorielle validée pour le
// fond sombre), « Autres » en gris neutre
const CATEGORY_COLORS = {
  documents: "#3987e5", images: "#d95926", videos: "#199e70", audio: "#c98500",
  archives: "#d55181", code: "#008300", programs: "#9085e9", other: "#5c5c5c",
};
const CATEGORY_ICONS = {
  documents: "file-text", images: "image", videos: "play", audio: "audio-lines", archives: "box",
  code: "file-code", programs: "app-window", other: "file", folder: "folder",
};
const SUMMARY_TITLES = { text: "Résumé", image: "Description", folder: "Présentation", archive: "Présentation" };
// Suggestions au-dessus du champ (gardées quand des questions ont été posées), selon l'élément
const SUGGESTIONS = {
  text: [
    { icon: "list-checks", label: "Points clés", ask: "Quels sont les points clés de ce document ?" },
    { icon: "calendar-clock", label: "Dates et montants", ask: "Quelles dates, échéances et montants contient-il ?" },
    { icon: "flag", label: "Ce que je dois faire", ask: "Y a-t-il une action à faire ou une échéance pour moi dans ce document ?" },
  ],
  image: [
    { icon: "image", label: "Décrire en détail", ask: "Décris cette image en détail." },
    { icon: "text-select", label: "Lire le texte", ask: "Recopie le texte visible sur cette image." },
  ],
  folder: [
    { icon: "hard-drive", label: "Qu'est-ce qui prend de la place ?", ask: "Qu'est-ce qui prend le plus de place dans ce dossier ?" },
    { icon: "clock", label: "Fichiers récents", ask: "Quels fichiers ont été modifiés le plus récemment ?" },
    { icon: "trash-2", label: "Faire le ménage", ask: "Qu'est-ce qui pourrait être supprimé ou archivé sans risque dans ce dossier ?" },
  ],
  archive: [{ icon: "folder", label: "Que contient l'archive ?", ask: "Que contient cette archive, en résumé ?" }],
  "": [{ icon: "lightbulb", label: "À quoi sert ce fichier ?", ask: "À quoi sert ce fichier, d'après son type et ses propriétés ?" }],
};
const FOLLOW_UPS = [
  { icon: "arrow-down", label: "Développer", ask: "Développe ta dernière réponse." },
  { icon: "lightbulb", label: "Plus simplement", ask: "Explique plus simplement ta dernière réponse." },
];

const baseName = (p) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
const sameKey = (a, b) => a.toLowerCase().replace(/[\\/]+$/, "") === b.toLowerCase().replace(/[\\/]+$/, "");

/** Taille comme l'Explorateur (unités de 1 024). */
function bytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} octet${n > 1 ? "s" : ""}`;
  const units = ["Ko", "Mo", "Go", "To"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toLocaleString("fr-FR", { maximumFractionDigits: v < 10 ? 1 : 0 })} ${units[i]}`;
}

/** « il y a 3 jours », « dans 2 h »… */
function relative(d) {
  const s = (Date.now() - d.getTime()) / 1000;
  const abs = Math.abs(s);
  const [v, unit] = abs < 60 ? [0, ""] : abs < 3600 ? [abs / 60, "min"] : abs < 86400 ? [abs / 3600, "h"]
    : abs < 86400 * 30 ? [abs / 86400, "jour"] : abs < 86400 * 365 ? [abs / 86400 / 30.44, "mois"]
      : [abs / 86400 / 365.25, "an"];
  if (!unit) return "à l'instant";
  const n = Math.round(v);
  const word = unit === "jour" || unit === "an" ? `${unit}${n > 1 ? "s" : ""}` : unit;
  return s >= 0 ? `il y a ${n} ${word}` : `dans ${n} ${word}`;
}

export function createFilesView(ctx, state) {
  const { api, on, store } = ctx;
  const S = {
    items: [],                  // éléments de la barre du haut (voir item())
    active: null,               // élément affiché
    models: state.chat?.models || [],
    model: state.files?.model || "",
  };
  const answers = new Map();    // id → { item, root, … } (réponses affichées)

  /** Élément déposé : fiche et tout ce qui se charge ensuite. */
  const item = (path) => ({
    path, info: null, details: null, text: null, scan: null, hashes: null, loading: false,
    locks: { result: null, scanning: false, error: "", admin: false, at: 0, seq: 0,
             busy: new Set(), denied: new Set(), expanded: new Set() },
    thread: [], autoTried: false, showAllText: false, showAllProps: false,
  });

  // ─────────────────────────────────────────────
  //  Structure
  // ─────────────────────────────────────────────
  const pickButtons = (primary) => [
    h("button", { class: `btn sm ${primary ? "primary" : ""}`.trim(), type: "button", onClick: pickFolder },
      ico("folder-open", 14), "Choisir un dossier"),
    h("button", { class: "btn sm", type: "button", onClick: pickFile }, ico("file", 14), "Choisir un fichier"),
  ];
  const headPick = h("div", { class: "fx-head-pick" }, ...pickButtons(false));
  const emptyPick = pickButtons(true);
  const empty = h("div", { class: "empty fx-empty", dataset: { drop: "files" } },
    h("div", { class: "glyph" }, ico("folder-search", 22)),
    h("h3", { text: "Déposez un fichier ou un dossier" }),
    h("p", { text: "Taille, dates, propriétaire, propriétés de Windows, aperçu, contenu d'un dossier, "
      + "applications qui l'utilisent (et empêchent de le supprimer), résumé et questions à Ollama." }),
    h("div", { class: "empty-actions" }, ...emptyPick),
    h("div", { class: "hints" }, h("span", {}, ico("arrow-down", 13), "ou glissez-le ici")));

  const strip = h("div", { class: "fx-strip", role: "tablist", "aria-label": "Éléments déposés" });
  const infoCol = h("div", { class: "fx-info" });
  const aiHead = h("div", { class: "fx-ai-head" });
  const thread = h("div", { class: "asst-thread fx-thread" });
  const aiNote = h("div", { class: "fx-ai-note", hidden: true });
  const aiScroll = h("div", { class: "fx-ai-scroll" }, aiNote, thread);

  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle des réponses",
    dataset: { tip: "Modèle qui résume et répond aux questions" },
    onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget) },
    ico("sparkles", 14), modelName, ico("chevron-down", 14));
  let readingMode = "answers";
  const composer = createComposer(ctx, {
    label: "Question sur l'élément", maxlength: 4000, maxHeight: 160,
    tools: [iconButton("mic", "Dictée vocale", () => { composer.input.focus(); api.dictate(); }, { kbd: "Win+H" }), modelBtn],
    speakKey: "files_speak",
    onAutoRead: (on) => { const entry = busyEntry(); if (on && entry) autoRead(entry); },
    read: {
      source: READ_ALL,
      match: (source) => source === READ_ALL || (source.startsWith("files:") && answers.has(source.slice(6))),
      speak: (mode) => {
        readingMode = mode;
        ctx.speak(threadSpeech(mode), READ_ALL, true, !!busyEntry());
      },
    },
    onSend: () => ask(),
    onStop: () => api.files_stop(),
    sendTip: "Demander", stopTip: "Arrêter la réponse",
  });
  const suggest = createSuggestions(composer, (text) => ask(text));
  const aiCol = h("div", { class: "fx-ai" }, aiHead, aiScroll, h("div", { class: "fx-composer" }, suggest.el, composer.el));
  const body = h("div", { class: "fx-body", hidden: true }, infoCol, aiCol);
  const dropOverlay = h("div", { class: "drop-overlay" },
    h("div", { class: "inner" }, ico("folder-search", 22), "Déposez pour tout savoir de l'élément"));
  const el = h("section", { class: "files" },
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Fichiers" }),
      h("span", { class: "view-sub", text: "tout sur un fichier ou un dossier" }),
      h("div", { class: "head-spacer" }), headPick),
    empty, strip, body, dropOverlay);

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "F5" && !ev.ctrlKey && !ev.altKey && ctx.isFocused("files") && S.active) refresh(S.active);
  });

  // ─────────────────────────────────────────────
  //  Éléments
  // ─────────────────────────────────────────────
  async function pickFolder() {
    const path = await api.pick_folder();
    if (path) open([path]);
  }

  async function pickFile() {
    const paths = await api.locks_pick_file();
    if (paths?.length) open(paths);
  }

  /** Éléments ajoutés en tête de la barre ; le premier est affiché. */
  function open(paths) {
    const added = [];
    for (const path of [...paths].reverse()) {
      const i = S.items.findIndex((it) => sameKey(it.path, path));
      const it = i >= 0 ? S.items.splice(i, 1)[0] : item(path);
      S.items.unshift(it);
      added.unshift(it);
    }
    for (const it of S.items.splice(MAX_ITEMS)) api.files_forget(it.path);
    remember();
    select(added[0]);
  }

  function remove(it) {
    const i = S.items.indexOf(it);
    if (i < 0) return;
    S.items.splice(i, 1);
    api.files_forget(it.path);
    remember();
    if (S.active === it) select(S.items[Math.min(i, S.items.length - 1)] || null);
    else renderStrip();
  }

  const remember = () => api.files_remember(S.items.map((it) => it.path));

  function select(it) {
    S.active = it;
    render();
    if (it) load(it);
  }

  /** Fiche, puis en parallèle : propriétés de Windows, parcours, texte, processus, résumé. */
  async function load(it, force = false) {
    if (it.loading || (it.info && !force)) {
      maybeSummarize(it);
      if (Date.now() - it.locks.at > LOCKS_STALE && !it.locks.scanning && !it.locks.admin && it.info?.exists) scanLocks(it);
      return;
    }
    it.loading = true;
    const info = await api.files_inspect(it.path);
    it.loading = false;
    if (!info) return;
    it.info = info;
    it.path = info.path;
    if (S.active === it) render();
    if (!info.exists) return;
    api.files_details(it.path).then((d) => { it.details = d; if (S.active === it) { renderInfo(); renderAi(); } });
    if (info.dir) api.files_scan(it.path).then((st) => { if (st && !it.scan) { it.scan = st; if (S.active === it) renderScan(); } });
    if (info.summary === "text") api.files_text(it.path).then((t) => { it.text = t; if (S.active === it) renderInfo(); });
    const thread = await api.files_thread(it.path);
    if (thread?.length) { it.thread = thread; if (S.active === it) renderThread(); }
    if (S.active === it) scanLocks(it);
    maybeSummarize(it);
  }

  async function refresh(it) {
    await api.files_refresh(it.path);
    Object.assign(it, { info: null, details: null, text: null, scan: null, hashes: null });
    it.locks.result = null;
    if (S.active === it) render();
    load(it, true);
  }

  /** Résumé automatique (réglage), une fois par élément, quand Ollama répond. */
  function maybeSummarize(it) {
    if (S.active !== it || it.autoTried || !it.info?.summary || !store.ollama) return;
    if (store.settings.files_auto_summary === false) return;
    if (it.thread.some((e) => e.kind === "summary")) return;
    it.autoTried = true;
    summarize(it);
  }

  async function summarize(it) {
    const res = await api.files_summarize(it.path);
    if (!res?.ok && res?.error) toast(res.error, "error");
  }

  /** preset : question d'une suggestion (le champ garde son texte), sinon celle du champ. */
  async function ask(preset) {
    const it = S.active;
    const question = (preset ?? composer.input.value).trim();
    if (!it || !question) return;
    const res = await api.files_ask(it.path, question);
    if (res?.ok) { if (preset === undefined) composer.setValue(""); }
    else if (res?.error) toast(res.error, "error");
  }

  // ─────────────────────────────────────────────
  //  Affichage
  // ─────────────────────────────────────────────
  function render() {
    const has = S.items.length > 0;
    empty.hidden = has;
    strip.hidden = !has;
    body.hidden = !S.active;
    headPick.hidden = !has;
    renderStrip();
    if (!S.active) return;
    renderInfo();
    renderAi();
    renderThread();
  }

  function renderStrip() {
    strip.replaceChildren(...S.items.map((it) => {
      const info = it.info;
      const chip = h("div", { class: `fx-chip${it === S.active ? " active" : ""}`, role: "tab", tabindex: "0",
        "aria-selected": String(it === S.active), dataset: { tip: it.path },
        onClick: () => { if (S.active !== it) select(it); },
        onKeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(it); } },
        onAuxclick: (ev) => { if (ev.button === 1) remove(it); } },
      ico(info ? CATEGORY_ICONS[info.category] || "file" : "file", 13),
      h("span", { class: "fx-chip-name", text: info?.name || baseName(it.path) }),
      h("button", { class: "fx-chip-x", type: "button", "aria-label": "Retirer",
        onClick: (ev) => { ev.stopPropagation(); remove(it); } }, ico("x", 12)));
      chip.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        openMenu([
          { label: "Retirer", icon: "x", onSelect: () => remove(it) },
          { label: "Retirer les autres", disabled: S.items.length < 2,
            onSelect: () => { for (const other of S.items.filter((x) => x !== it)) remove(other); } },
          { label: "Tout retirer", onSelect: () => { for (const x of [...S.items]) remove(x); } },
        ], ev.clientX, ev.clientY);
      });
      return chip;
    }));
  }

  function card(title, icon, ...content) {
    return h("div", { class: "fx-card" },
      title ? h("div", { class: "fx-card-head" }, ico(icon, 15), h("span", { class: "fx-card-title", text: title })) : null,
      ...content);
  }

  let scanSlot = null;
  let locksSlot = null;

  function renderInfo() {
    const it = S.active;
    const info = it.info;
    if (!info) {
      infoCol.replaceChildren(h("div", { class: "fx-loading" }, ico("loader-circle", 16), "Lecture…"));
      return;
    }
    if (!info.exists) {
      infoCol.replaceChildren(card(null, "", h("div", { class: "fx-missing" }, ico("triangle-alert", 18),
        h("div", {}, h("b", { text: info.name }), h("p", { text: `Introuvable : ${info.error || "l'élément n'existe plus."}` })),
        h("button", { class: "btn sm", type: "button", onClick: () => remove(it) }, "Retirer"))));
      return;
    }
    scanSlot = h("div");
    locksSlot = h("div");
    infoCol.replaceChildren(heroCard(it), factsCard(it),
      info.dir ? scanSlot : previewCard(it), locksSlot, propsCard(it), info.dir ? null : hashCard(it));
    if (info.dir) renderScan();
    renderLocks();
  }

  function heroCard(it) {
    const { info, details } = it;
    const thumb = details?.thumbnail ? h("img", { src: details.thumbnail, alt: "" }) : ico(CATEGORY_ICONS[info.category] || "file", 30);
    const link = info.link || details?.link_target;
    const actions = h("div", { class: "fx-actions" },
      h("button", { class: "btn sm", type: "button", onClick: () => openItem(it) },
        ico(info.dir ? "folder-open" : "external-link", 14), info.dir ? "Ouvrir" : `Ouvrir${info.opens_with ? ` avec ${info.opens_with}` : ""}`),
      info.dir ? null : h("button", { class: "btn sm", type: "button", onClick: () => api.reveal_path(info.path) }, ico("folder", 14), "Explorateur"),
      iconButton("copy", "Copier le chemin", () => ctx.copy(info.path, "Chemin copié"), { size: 15, cls: "sm" }),
      iconButton("refresh-cw", "Actualiser", () => refresh(it), { size: 15, cls: "sm", kbd: "F5" }),
      info.root ? null : iconButton("trash-2", "Mettre à la corbeille…", () => confirmTrash(it), { size: 15, cls: "sm danger" }));
    return h("div", { class: "fx-card fx-hero" },
      h("div", { class: "fx-thumb" }, thumb),
      h("div", { class: "fx-hero-text" },
        h("div", { class: "fx-name", text: info.name, dataset: { tip: info.name } }),
        h("div", { class: "fx-type", text: [info.type, info.ext && !info.type?.toLowerCase().includes(info.ext.slice(1)) ? info.ext : ""].filter(Boolean).join(" · ") }),
        h("button", { class: "fx-path", type: "button", dataset: { tip: "Ouvrir le dossier parent ici" },
          onClick: () => info.parent && !sameKey(info.parent, info.path) && open([info.parent]) }, info.path),
        info.attributes.length || link ? h("div", { class: "fx-attrs" },
          ...info.attributes.map((a) => h("span", { class: `fx-attr${a.key === "online" ? " warn" : ""}`, text: a.label, dataset: { tip: a.tip } })),
          link ? h("button", { class: "fx-attr link", type: "button", dataset: { tip: `Afficher la cible : ${link}` },
            onClick: () => open([link]) }, ico("arrow-left-right", 11), info.link ? "Lien" : "Raccourci", " → ", baseName(link)) : null) : null,
        actions));
  }

  async function openItem(it) {
    const ok = await api.files_open(it.path);
    if (!ok) toast("Ouverture impossible.", "error");
  }

  function confirmTrash(it) {
    const info = it.info;
    const users = (it.locks.result?.processes || []).filter((p) => p.own !== "self");
    openModal({
      title: `Mettre « ${info.name} » à la corbeille ?`,
      width: 460,
      body: h("div", { class: "lk-confirm" },
        h("p", { text: info.dir ? "Le dossier et tout son contenu vont à la corbeille (récupérables)."
          : "Le fichier va à la corbeille (récupérable)." }),
        users.length ? h("p", { text: `${plural(users.length, "processus l'utilise", "processus l'utilisent")} : la suppression peut échouer.` }) : null),
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: "Mettre à la corbeille", icon: "trash-2", kind: "danger", onClick: async () => {
          const res = await api.locks_recycle(info.path);
          if (res?.ok) { toast(`${info.name} mis à la corbeille.`); remove(it); return true; }
          toast("Suppression impossible : l'élément est encore utilisé, protégé ou en lecture seule.", "error", 4500);
          scanLocks(it);
          return true;
        } },
      ],
    });
  }

  function row(label, ...value) {
    const parts = value.flat().filter((v) => v != null && v !== "" && v !== false);
    if (!parts.length) return null;
    return h("div", { class: "fx-row" }, h("div", { class: "fx-label", text: label }), h("div", { class: "fx-value" }, ...parts));
  }

  function dateRow(label, iso) {
    const d = parseDate(iso);
    if (!d) return null;
    const text = d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }) + ` à ${formatShort(d).slice(-5)}`;
    return row(label, h("span", { text, dataset: { tip: formatFullDate(d) } }), h("span", { class: "muted", text: ` · ${relative(d)}` }));
  }

  function factsCard(it) {
    const { info, details, scan, text } = it;
    const vol = info.volume || {};
    const sizeText = info.dir
      ? (scan ? [h("span", { text: bytes(scan.size) }), h("span", { class: "muted", text: scan.done ? ` · ${plural(scan.files, "fichier", "fichiers")}, ${plural(scan.dirs, "dossier", "dossiers")}` : " · calcul…" })] : "calcul…")
      : [h("span", { text: bytes(info.size) }), info.size >= 1024 ? h("span", { class: "muted", text: ` (${info.size.toLocaleString("fr-FR")} octets)` }) : null];
    const disk = !info.dir && info.disk != null && info.disk !== info.size
      ? row("Sur le disque", bytes(info.disk), info.disk === 0 ? h("span", { class: "muted", text: " · contenu en ligne (OneDrive)" }) : null) : null;
    const volText = vol.root ? [`${vol.root}${vol.label ? ` « ${vol.label} »` : ""}`,
      h("span", { class: "muted", text: ` · ${[vol.fs, vol.kind].filter(Boolean).join(", ")}${vol.total ? ` · ${bytes(vol.free)} libres sur ${bytes(vol.total)}` : ""}` })] : null;
    const doc = text && !text.error ? [
      text.pages ? `${plural(text.pages, text.kind === "slides" ? "diapositive" : "page", text.kind === "slides" ? "diapositives" : "pages")} · ` : "",
      `${plural(text.words, "mot", "mots")}`, h("span", { class: "muted", text: ` · ${text.chars.toLocaleString("fr-FR")} caractères` })] : null;
    return card("Informations", "info",
      h("div", { class: "fx-grid" },
        row("Taille", sizeText), disk,
        row("Contenu", doc),
        dateRow("Créé le", info.created), dateRow("Modifié le", info.modified), dateRow("Dernier accès", info.accessed),
        row("Propriétaire", details?.owner || ""),
        row("S'ouvre avec", info.opens_with),
        row("Lecteur", volText)));
  }

  function previewCard(it) {
    const { info, text } = it;
    if (info.image) {
      const im = info.image;
      return card("Aperçu", "image",
        h("div", { class: "fx-image" }, h("img", { src: im.url, alt: info.name })),
        h("div", { class: "fx-caption", text: `${im.width} × ${im.height} pixels · ${im.format}${im.frames > 1 ? ` · ${im.frames} images` : ""}` }));
    }
    if (info.archive) {
      const arc = info.archive;
      const ratio = arc.size ? Math.round((1 - arc.packed / arc.size) * 100) : 0;
      return card("Contenu de l'archive", "box",
        h("div", { class: "fx-caption", text: `${plural(arc.count, "fichier", "fichiers")} · ${bytes(arc.size)} décompressés`
          + (ratio > 0 ? ` (compression ${ratio} %)` : "") }),
        h("div", { class: "fx-list" }, ...arc.entries.map((e) => h("div", { class: "fx-list-row" },
          ico(e.dir ? "folder" : "file", 13), h("span", { class: "fx-list-name", text: e.name }),
          e.dir ? null : h("span", { class: "fx-list-size", text: bytes(e.size) })))),
        arc.more ? h("div", { class: "fx-caption", text: `… et ${plural(arc.more, "autre entrée", "autres entrées")}` }) : null);
    }
    const preview = info.preview;
    const excerpt = !preview && text && !text.error ? text.excerpt : "";
    if (!preview && !excerpt) {
      if (info.preview_error) return card("Aperçu", "eye", h("div", { class: "fx-caption", text: `Aperçu impossible : ${info.preview_error}` }));
      if (text?.error && info.summary === "text") return card("Aperçu", "eye", h("div", { class: "fx-caption", text: text.error }));
      return null;
    }
    const content = preview ? preview.text : excerpt;
    const code = h("code", { class: preview?.lang ? `hljs language-${preview.lang}` : "hljs" });
    if (preview?.lang && window.hljs?.getLanguage(preview.lang)) {
      try { code.innerHTML = window.hljs.highlight(content, { language: preview.lang, ignoreIllegals: true }).value; }
      catch { code.textContent = content; }
    } else code.textContent = content;
    const long = content.length > TEXT_BOX && !it.showAllText;
    const box = h("pre", { class: `fx-text${long ? " clipped" : ""}${preview ? "" : " prose"}` }, code);
    return card(preview ? "Aperçu" : "Début du texte", preview ? "file-code" : "file-text", box,
      long ? h("button", { class: "fx-more", type: "button", text: "Afficher plus",
        onClick: () => { it.showAllText = true; renderInfo(); } }) : null,
      (preview?.more || (!preview && text && text.chars > content.length))
        ? h("div", { class: "fx-caption", text: "Début seulement — le résumé et les questions lisent tout le texte." }) : null);
  }

  function propsCard(it) {
    const props = it.details?.props || [];
    if (!it.details) return card("Propriétés de Windows", "list-checks", h("div", { class: "fx-loading" }, ico("loader-circle", 14), "Lecture…"));
    if (!props.length) return null;
    const shown = it.showAllProps ? props : props.slice(0, 14);
    return card("Propriétés de Windows", "list-checks",
      h("div", { class: "fx-grid" }, ...shown.map(([label, value]) => row(label, h("span", { class: "fx-sel", text: value })))),
      props.length > shown.length ? h("button", { class: "fx-more", type: "button", text: `Afficher les ${props.length} propriétés`,
        onClick: () => { it.showAllProps = true; renderInfo(); } }) : null);
  }

  function hashCard(it) {
    const hs = it.hashes;
    const value = (label, v) => row(label, h("button", { class: "fx-hash", type: "button", text: v, dataset: { tip: "Copier" },
      onClick: () => ctx.copy(v, `${label} copié`) }));
    return card("Empreintes", "scan",
      hs && hs !== "busy" && !hs.error ? h("div", { class: "fx-grid" }, value("SHA-256", hs.sha256), value("MD5", hs.md5))
        : h("div", { class: "fx-inline" },
          h("button", { class: `btn sm${hs === "busy" ? " busy" : ""}`, type: "button", disabled: hs === "busy", onClick: async () => {
            it.hashes = "busy";
            renderInfo();
            it.hashes = await api.files_hashes(it.path);
            if (it.hashes?.error) toast(it.hashes.error, "error");
            if (S.active === it) renderInfo();
          } }, ico(hs === "busy" ? "loader-circle" : "scan", 14), hs === "busy" ? "Calcul…" : "Calculer SHA-256 et MD5"),
          h("span", { class: "muted", text: "Pour vérifier qu'un fichier téléchargé est intact ou trouver des doublons." })));
  }

  // ── Contenu d'un dossier ───────────────────
  function renderScan() {
    const it = S.active;
    if (!scanSlot || !it?.info?.dir) return;
    const st = it.scan;
    if (!st || st.files === undefined) {
      scanSlot.replaceChildren(card("Contenu", "folder", h("div", { class: "fx-loading" }, ico("loader-circle", 14), "Parcours du dossier…")));
      return;
    }
    const busy = !st.done;
    const head = h("div", { class: "fx-scan-head" },
      h("span", { class: "fx-scan-total" }, h("b", { text: bytes(st.size) }),
        ` · ${plural(st.files, "fichier", "fichiers")} · ${plural(st.dirs, "dossier", "dossiers")}`),
      busy ? h("span", { class: "fx-scan-busy" }, ico("loader-circle", 13), "parcours…") : null,
      h("div", { class: "head-spacer" }),
      busy ? h("button", { class: "btn ghost sm", type: "button", onClick: () => api.files_stop_scan(it.path) }, "Arrêter") : null);
    const notes = [
      st.cancelled ? "Parcours arrêté : chiffres partiels." : "",
      st.online ? `${plural(st.online, "fichier OneDrive en ligne seulement", "fichiers OneDrive en ligne seulement")} (${bytes(st.online_size)}, pas sur le disque).` : "",
      st.errors ? `${plural(st.errors, "élément inaccessible", "éléments inaccessibles")} (droits).` : "",
      st.links ? `${plural(st.links, "lien ignoré", "liens ignorés")} (jonctions, liens symboliques).` : "",
    ].filter(Boolean);
    const total = st.size || 1;
    const cats = st.categories.filter((c) => c.size > 0 || c.count > 0);
    const bar = h("div", { class: "fx-bar", role: "img", "aria-label": "Répartition de la taille par type de fichier" },
      ...cats.filter((c) => c.size / total >= 0.004).map((c) => h("span", { class: "fx-seg",
        style: { flexGrow: String(c.size), background: CATEGORY_COLORS[c.key] },
        dataset: { tip: `${c.label} : ${bytes(c.size)} (${Math.round((c.size / total) * 100)} %) · ${plural(c.count, "fichier", "fichiers")}` } })));
    const legend = h("div", { class: "fx-legend" }, ...cats.map((c) => h("div", { class: "fx-legend-row" },
      h("span", { class: "fx-swatch", style: { background: CATEGORY_COLORS[c.key] } }),
      h("span", { class: "fx-legend-label", text: c.label }),
      h("span", { class: "fx-legend-value", text: bytes(c.size) }),
      h("span", { class: "fx-legend-pct", text: `${(c.size / total * 100).toLocaleString("fr-FR", { maximumFractionDigits: c.size / total < 0.1 ? 1 : 0 })} %` }),
      h("span", { class: "fx-legend-count", text: plural(c.count, "fichier", "fichiers") }))));
    const maxChild = st.children[0]?.size || 1;
    const children = st.children.length ? h("div", { class: "fx-sub" }, h("div", { class: "fx-sub-title", text: "Le plus lourd" }),
      ...st.children.map((c) => h("button", { class: "fx-list-row button", type: "button", dataset: { tip: "Afficher ici" },
        onClick: () => open([`${it.path.replace(/[\\/]+$/, "")}\\${c.name}`]) },
      ico(c.dir ? "folder" : "file", 13), h("span", { class: "fx-list-name", text: c.name }),
      h("span", { class: "fx-meter" }, h("i", { style: { width: `${Math.max(2, (c.size / maxChild) * 100)}%` } })),
      h("span", { class: "fx-list-size", text: bytes(c.size) }))),
      st.child_count > st.children.length ? h("div", { class: "fx-caption", text: `… et ${st.child_count - st.children.length} autres au premier niveau` }) : null) : null;
    const fileList = (title, files, value) => (files.length ? h("div", { class: "fx-sub" }, h("div", { class: "fx-sub-title", text: title }),
      ...files.map((f) => h("button", { class: "fx-list-row button", type: "button", dataset: { tip: f.path },
        onClick: () => open([f.path]) }, ico("file", 13),
      h("span", { class: "fx-list-name", text: f.path.slice(it.path.replace(/[\\/]+$/, "").length + 1) }),
      h("span", { class: "fx-list-size", text: value(f) })))) : null);
    scanSlot.replaceChildren(card("Contenu", "folder", head,
      cats.length ? bar : null, cats.length ? legend : null,
      notes.length ? h("div", { class: "fx-caption", text: notes.join(" ") }) : null,
      h("div", { class: "fx-subs" }, children,
        fileList("Plus gros fichiers", st.largest, (f) => bytes(f.size)),
        fileList("Modifiés récemment", st.newest, (f) => { const d = parseDate(f.modified); return d ? relative(d) : ""; }))));
  }

  // ── Processus qui utilisent l'élément (locks.py) ───────────
  const killable = (p) => !p.critical && p.own !== "self" && p.own !== "webview";

  async function scanLocks(it, { elevated = it.locks.admin } = {}) {
    const L = it.locks;
    if (it.info?.root) { L.error = "Lecteur entier : les processus ne sont pas recherchés."; renderLocks(); return; }
    const seq = ++L.seq;
    L.scanning = true;
    if (S.active === it) renderLocks();
    const res = await api.locks_scan([it.path], elevated);
    if (seq !== L.seq) return;
    L.scanning = false;
    if (!res || res.cancelled) {
      if (res?.cancelled) { L.admin = false; toast("Analyse en administrateur annulée.", "info"); }
    } else {
      L.error = res.error || "";
      if (!res.error) {
        L.result = res;
        L.admin = elevated;
        L.at = Date.now();
        const alive = new Set(res.processes.map((p) => p.pid));
        for (const set of [L.denied, L.expanded]) for (const pid of set) if (!alive.has(pid)) set.delete(pid);
      }
    }
    if (S.active === it) renderLocks();
  }

  async function kill(it, procs, elevated = false) {
    const L = it.locks;
    for (const p of procs) L.busy.add(p.pid);
    renderLocks();
    const res = await api.locks_kill(procs.map((p) => ({ pid: p.pid, start: p.start, name: p.name })), elevated);
    for (const p of procs) L.busy.delete(p.pid);
    if (!res || res.cancelled) { renderLocks(); return; }
    if (res.error) { toast(res.error, "error"); renderLocks(); return; }
    const name = (pid) => procs.find((p) => p.pid === pid)?.name || `PID ${pid}`;
    for (const pid of res.killed) L.denied.delete(pid);
    if (res.denied.length && !elevated && L.admin && !L.result?.app_elevated) {
      if (res.killed.length) toastKilled(it, res.killed, name);
      await kill(it, procs.filter((p) => res.denied.includes(p.pid)), true);
      return;
    }
    for (const pid of res.denied) L.denied.add(pid);
    if (res.killed.length) toastKilled(it, res.killed, name);
    if (res.denied.length) {
      toast(res.denied.length > 1 ? `Windows refuse de terminer ${res.denied.length} processus : lancés en administrateur ?`
        : `Windows refuse de terminer ${name(res.denied[0])} : lancé en administrateur ?`, "error", 4000);
    }
    if (res.failed.length) toast(`Impossible de terminer ${res.failed.map(name).join(", ")}.`, "error");
    await scanLocks(it);
  }

  function toastKilled(it, pids, name) {
    const shell = pids.some((pid) => it.locks.result?.processes.find((p) => p.pid === pid)?.shell);
    toast(shell && pids.length === 1 ? "Explorateur redémarré."
      : pids.length > 1 ? `${pids.length} processus terminés.` : `${name(pids[0])} terminé.`);
  }

  async function closeApp(it, p) {
    const n = await api.locks_close(p.pid);
    if (!n) { toast(`${p.name} n'a pas pu être prévenu (lancé en administrateur ?) : terminez-le.`, "error", 4000); return; }
    toast(`Fermeture demandée à ${p.name} : il peut proposer d'enregistrer.`, "info", 3500);
    setTimeout(() => scanLocks(it), CLOSE_WAIT);
  }

  function confirmKill(it, p) {
    const windows = p.shell ? [] : p.windows;
    openModal({
      title: p.shell ? "Redémarrer l'Explorateur ?" : `Terminer ${p.name} ?`,
      width: 460,
      body: h("div", { class: "lk-confirm" },
        h("p", { text: p.shell ? "Les fenêtres de l'Explorateur et la barre des tâches se ferment, puis reviennent aussitôt."
          : "Le processus est arrêté immédiatement : ce qui n'a pas été enregistré est perdu." }),
        windows.length ? h("ul", {}, ...windows.map((t) => h("li", { text: t }))) : null),
      actions: [
        { label: "Annuler", kind: "ghost" },
        windows.length ? { label: "Fermer normalement", kind: "primary", onClick: () => { closeApp(it, p); } } : null,
        { label: p.shell ? "Redémarrer" : "Terminer", icon: p.shell ? "rotate-ccw" : "power", kind: "danger",
          onClick: () => { kill(it, [p], it.locks.denied.has(p.pid)); } },
      ].filter(Boolean),
    });
  }

  function confirmKillAll(it, procs) {
    openModal({
      title: `Terminer ${plural(procs.length, "processus", "processus")} ?`,
      width: 480,
      body: h("div", { class: "lk-confirm" },
        h("p", { text: "Ils sont arrêtés immédiatement : ce qui n'a pas été enregistré dans ces applications est perdu." }),
        h("ul", {}, ...procs.map((p) => h("li", {}, p.name, p.windows[0] ? h("span", { class: "muted", text: ` — ${p.windows[0]}` }) : null)))),
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: "Tout terminer", icon: "power", kind: "danger", onClick: () => { kill(it, procs); } },
      ],
    });
  }

  function renderLocks() {
    const it = S.active;
    if (!locksSlot || !it?.info) return;
    const L = it.locks;
    const r = L.result;
    const what = it.info.dir ? "ce dossier" : "ce fichier";
    const refresh = iconButton("refresh-cw", L.admin ? "Réexaminer (en administrateur)" : "Réexaminer",
      () => scanLocks(it), { size: 14, cls: `sm ${L.scanning ? "busy" : ""}`.trim() });
    refresh.disabled = L.scanning || !!it.info.root;
    let status;
    if (L.error && !r) status = h("div", { class: "lk-status-text" }, ico("info", 15), L.error);
    else if (!r) status = h("div", { class: "lk-status-text busy" }, ico("loader-circle", 15), `Recherche de ce qui utilise ${what}…`);
    else if (r.processes.length) {
      status = h("div", { class: "lk-status-text" }, ico("lock", 15),
        `${plural(r.processes.length, "processus utilise", "processus utilisent")} ${what}`);
    } else status = h("div", { class: "lk-status-text ok" }, ico("lock-open", 15), `Aucune application n'utilise ${what} : il peut être supprimé`);
    const all = (r?.processes || []).filter((p) => killable(p) && !p.shell);
    const actions = h("div", { class: "lk-status-actions" }, refresh,
      all.length > 1 ? h("button", { class: "btn sm danger", type: "button", disabled: L.scanning,
        onClick: () => confirmKillAll(it, all) }, ico("power", 14), "Tout terminer") : null);
    const denied = r?.denied || [];
    const names = denied.slice(0, DENIED_SHOWN).join(", ") + (denied.length > DENIED_SHOWN ? "…" : "");
    const note = denied.length ? h("div", { class: "lk-note" }, ico("info", 14),
      h("span", { class: "lk-note-text", text: r.elevated
        ? `${plural(denied.length, "processus protégé n'a", "processus protégés n'ont")} pas pu être examinés (${names}).`
        : `${plural(denied.length, "processus n'a", "processus n'ont")} pas pu être examinés : services et applications lancées en administrateur (${names}).` }),
      r.elevated ? null : h("button", { class: "btn sm", type: "button", disabled: L.scanning,
        dataset: { tip: "Windows demandera l'autorisation" }, onClick: () => scanLocks(it, { elevated: true }) },
      ico("shield", 14), "Analyser en administrateur")) : null;
    locksSlot.replaceChildren(card("Utilisé par", "lock",
      h("div", { class: "lk-status" }, status, actions),
      r?.processes.length ? h("div", { class: "lk-list" }, ...r.processes.map((p) => procRow(it, p))) : null,
      note));
  }

  /** Élément utilisé, relatif à l'élément affiché (son nom compris). */
  function relativeUse(it, path) {
    const real = (it.locks.result?.targets?.[0]?.real || it.path).replace(/[\\/]+$/, "");
    const low = path.toLowerCase();
    if (low === real.toLowerCase()) return baseName(real);
    if (low.startsWith(`${real.toLowerCase()}\\`)) return `${baseName(real)}${path.slice(real.length)}`;
    return path;
  }

  function procRow(it, p) {
    const L = it.locks;
    const busy = L.busy.has(p.pid);
    const tags = [];
    if (p.own === "self" || p.own === "webview") {
      tags.push(h("span", { class: "lk-tag", text: "Boostache", dataset: { tip: p.own === "self"
        ? "Boostache lui-même : dossier indexé dans Documents, enregistrement, capture…"
        : "Navigateur intégré de Boostache (WhatsApp, Gmail, interface)" } }));
    } else if (p.own === "child") {
      tags.push(h("span", { class: "lk-tag", text: "lancé par Boostache", dataset: { tip: "Une console de Boostache, par exemple" } }));
    }
    if (p.critical) tags.push(h("span", { class: "lk-tag warn", text: "Windows", dataset: { tip: "Processus de Windows : le terminer ferait planter ou redémarrer Windows" } }));
    if (L.denied.has(p.pid)) tags.push(h("span", { class: "lk-tag warn", text: "administrateur" }));
    const shown = L.expanded.has(p.pid) ? p.uses : p.uses.slice(0, USES_SHOWN);
    const more = p.count - shown.length;
    const uses = h("div", { class: "lk-uses" },
      ...shown.map((u) => h("button", { class: "lk-use", type: "button", dataset: { tip: `${LOCK_KINDS[u.kind].tip} — ${u.path}` },
        onClick: () => api.reveal_path(u.path) }, ico(LOCK_KINDS[u.kind].icon, 13), h("span", { text: relativeUse(it, u.path) }))),
      more > 0 && p.uses.length > shown.length
        ? h("button", { class: "lk-more", type: "button", text: `+ ${plural(more, "autre", "autres")}`,
          onClick: () => { L.expanded.add(p.pid); renderLocks(); } })
        : more > 0 ? h("span", { class: "lk-more muted", text: `+ ${plural(more, "autre", "autres")} non affichés` }) : null);
    const actions = h("div", { class: "lk-actions" });
    if (killable(p)) {
      if (p.shell) {
        actions.append(h("button", { class: "btn sm", type: "button", disabled: busy,
          dataset: { tip: "Ses fenêtres et la barre des tâches se ferment puis reviennent" },
          onClick: () => confirmKill(it, p) }, ico("rotate-ccw", 14), "Redémarrer"));
      } else {
        if (p.windows.length) {
          actions.append(h("button", { class: "btn sm lk-close", type: "button", disabled: busy,
            dataset: { tip: "Fermer ses fenêtres, comme avec la croix (il peut proposer d'enregistrer)" },
            onClick: () => closeApp(it, p) }, "Fermer"));
        }
        const admin = L.denied.has(p.pid) && !L.result?.app_elevated;
        actions.append(h("button", { class: `btn sm lk-kill ${busy ? "busy" : ""}`.trim(), type: "button", disabled: busy,
          dataset: admin ? { tip: "Windows demandera l'autorisation" } : {},
          onClick: () => confirmKill(it, p) }, ico(busy ? "loader-circle" : admin ? "shield" : "power", 14),
        admin ? "Terminer en administrateur" : "Terminer"));
      }
    }
    const rowEl = h("div", { class: `lk-proc ${busy ? "busy" : ""}`.trim() },
      h("div", { class: "lk-icon" }, p.icon ? h("img", { src: p.icon, alt: "" }) : ico("app-window", 17)),
      h("div", { class: "lk-main" },
        h("div", { class: "lk-title" }, h("span", { class: "lk-name", text: p.name }), h("span", { class: "lk-pid", text: `PID ${p.pid}` }), ...tags),
        p.windows.length ? h("div", { class: "lk-windows", text: p.windows.join(" · "), dataset: { tip: p.windows.join("\n") } }) : null,
        uses),
      actions);
    rowEl.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      openMenu([
        { label: "Fermer", icon: "x", disabled: !killable(p) || p.shell || !p.windows.length, onSelect: () => closeApp(it, p) },
        { label: p.shell ? "Redémarrer l'Explorateur…" : "Terminer…", icon: p.shell ? "rotate-ccw" : "power", danger: true,
          disabled: !killable(p), onSelect: () => confirmKill(it, p) },
        "-",
        { label: "Afficher le programme dans l'Explorateur", icon: "folder-open", disabled: !p.exe, onSelect: () => api.reveal_path(p.exe) },
        { label: "Copier le chemin du programme", icon: "copy", disabled: !p.exe, onSelect: () => ctx.copy(p.exe) },
      ], ev.clientX, ev.clientY);
    });
    return rowEl;
  }

  // ─────────────────────────────────────────────
  //  Résumé et questions
  // ─────────────────────────────────────────────
  function modelItems() {
    return [
      { section: "Modèle des résumés et questions" },
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === S.model, onSelect: async () => {
          await ctx.saveSetting("files_model", m);
          S.model = m;
          renderAi();
        } }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Résumer automatiquement", checked: store.settings.files_auto_summary !== false,
        onSelect: () => ctx.saveSetting("files_auto_summary", store.settings.files_auto_summary === false) },
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  function renderAi() {
    const it = S.active;
    if (!it) return;
    const info = it.info;
    const kind = info?.summary || "";
    const ollama = !!store.ollama;
    const summary = it.thread.find((e) => e.kind === "summary");
    const busy = !!busyEntry();
    aiHead.replaceChildren(
      ico("sparkles", 15), h("span", { class: "fx-card-title", text: kind ? SUMMARY_TITLES[kind] + " et questions" : "Questions" }),
      h("div", { class: "head-spacer" }),
      kind && ollama ? h("button", { class: "btn sm", type: "button", disabled: busy && summary && BUSY.has(summary.state),
        onClick: () => summarize(it) }, ico(summary ? "rotate-ccw" : "sparkles", 14),
      summary ? "Recommencer" : kind === "image" ? "Décrire" : kind === "text" ? "Résumer" : "Présenter") : null);
    let note = "";
    if (!ollama) note = "Ollama ne répond pas : résumé et questions indisponibles.";
    else if (info?.exists && !kind) {
      note = info.attributes?.some((a) => a.key === "online")
        ? "Fichier OneDrive en ligne seulement : son contenu n'est pas lu (il serait téléchargé). Vous pouvez poser des questions sur ses propriétés."
        : "Pas de résumé pour ce type de fichier : vous pouvez poser des questions sur ses propriétés.";
    } else if (info?.exists && kind && !summary && store.settings.files_auto_summary === false) {
      note = "Résumé automatique désactivé (menu du modèle).";
    }
    aiNote.hidden = !note;
    aiNote.textContent = note;
    composer.update({ busy, ready: ollama && !!info?.exists });
    composer.input.disabled = !ollama;
    // Suggestions selon l'élément ; la suite de la dernière réponse s'il y en a une
    const answered = it.thread.some((e) => e.state === "done" && e.answer);
    suggest.set(ollama && info?.exists ? [...(SUGGESTIONS[kind] || []), ...(answered ? FOLLOW_UPS : [])] : [], busy);
    composer.setPlaceholder(info?.dir ? "Question sur ce dossier…" : kind === "image" ? "Question sur cette image…" : "Question sur ce fichier…");
    modelName.textContent = S.model || "Aucun modèle";
    modelBtn.hidden = !ollama;
  }

  const sourceOf = (id) => `files:${id}`;
  const titleOf = (entry) => entry.kind === "summary" ? (entry.label || "Résumé") : entry.question;
  const busyEntry = () => [...answers.values()].find((e) => BUSY.has(e.item.state)) || null;

  function autoRead(entry) {
    entry.autoRead = true;
    ctx.speak(entry.item.answer, sourceOf(entry.item.id), true, true);
  }

  function threadSpeech(mode) {
    const it = S.active;
    return conversationSpeech((it?.thread || []).map((e) => answers.get(e.id)?.item || e)
      .map((e) => ({ ask: titleOf(e), answer: e.answer })), mode);
  }

  function feedAnswer(entry) {
    const final = !BUSY.has(entry.item.state);
    ctx.feed(sourceOf(entry.item.id), entry.item.answer, final);
    if (ctx.feeding(READ_ALL)) ctx.feed(READ_ALL, threadSpeech(readingMode), final);
  }

  function qaEl(itm) {
    const source = sourceOf(itm.id);
    const answer = h("div", { class: "md asst-answer" });
    const phase = h("div", { class: "asst-phase" });
    const meta = h("div", { class: "asst-meta" });
    const speak = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(entry.item.answer, source, true), { size: 15, cls: "sm" });
    speak.dataset.speakSource = source;
    speak.dataset.tipIdle = "Lire";
    const retry = () => (entry.item.kind === "summary" ? summarize(S.active)
      : api.files_ask(S.active.path, entry.item.question).then((res) => { if (!res?.ok && res?.error) toast(res.error, "error"); }));
    const actions = h("div", { class: "asst-actions" },
      iconButton("copy", "Copier la réponse", () => ctx.copy(entry.item.answer, "Copié"), { size: 15, cls: "sm" }),
      speak,
      iconButton("notebook-pen", "Ajouter à une note", (ev) => ctx.pickDestination("notes", ev.currentTarget,
        (slot) => ctx.emit("note:insert", { text: noteText(entry.item), slot })), { size: 15, cls: "sm" }),
      iconButton("rotate-ccw", itm.kind === "summary" ? "Recommencer" : "Reposer la question", retry, { size: 15, cls: "sm" }),
      iconButton("x", "Retirer", () => api.files_delete(S.active.path, itm.id), { size: 15, cls: "sm" }));
    const reply = h("div", { class: "asst-reply" }, phase, answer, meta, actions);
    const question = h("div", { class: `asst-question${itm.kind === "summary" ? " fx-task" : ""}` });
    const root = h("div", { class: "asst-qa", dataset: { id: itm.id } }, question, reply);
    reply.addEventListener("contextmenu", (ev) => {
      const sel = window.getSelection().toString();
      ev.preventDefault();
      openMenu([
        sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
        { label: "Copier la réponse", icon: "copy", onSelect: () => ctx.copy(entry.item.answer, "Copié") },
        { label: "Lire", icon: "volume-2", onSelect: () => ctx.speak(entry.item.answer, source, true) },
        { label: "Ajouter à une note", icon: "notebook-pen", onSelect: () => ctx.emit("note:insert", { text: noteText(entry.item), slot: "new" }) },
        "-",
        { label: "Retirer", icon: "x", onSelect: () => api.files_delete(S.active.path, itm.id) },
      ], ev.clientX, ev.clientY);
    });
    const entry = { item: itm, root, question, answer, phase, meta, actions, reply, shown: null };
    answers.set(itm.id, entry);
    updateQa(entry);
    return root;
  }

  function noteText(itm) {
    const name = S.active?.info?.name || "";
    return `## ${itm.kind === "summary" ? `${itm.label || "Résumé"} de ${name}` : itm.question}\n\n${itm.answer}`;
  }

  function updateQa(entry) {
    const itm = entry.item;
    const busy = BUSY.has(itm.state);
    entry.question.replaceChildren(itm.kind === "summary" ? ico("sparkles", 13) : "", h("span", { text: titleOf(itm) }));
    entry.reply.classList.toggle("busy", busy);
    entry.phase.hidden = !busy || (itm.state === "thinking" && !!itm.answer);
    entry.phase.replaceChildren(h("span", { class: "pane-busy" }), h("span", { text: itm.phase || "…" }));
    if (entry.shown !== itm.answer) {
      entry.shown = itm.answer;
      entry.answer.innerHTML = itm.answer ? renderMarkdown(itm.answer, { highlight: !busy }) : "";
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
    const it = S.active;
    thread.replaceChildren(...(it?.thread || []).map(qaEl));
    renderAi();
    ctx.emit("speak-sources-changed");
  }

  const nearBottom = () => aiScroll.scrollHeight - aiScroll.scrollTop - aiScroll.clientHeight < 80;
  const scrollDown = () => { aiScroll.scrollTop = aiScroll.scrollHeight; };

  const itemOf = (key) => S.items.find((it) => sameKey(it.path, key) || (it.info && it.info.key === key));

  on("files:qa", ({ item: itm }) => {
    const it = itemOf(itm.key);
    if (!it) return;
    const i = it.thread.findIndex((e) => e.id === itm.id);
    if (i >= 0) it.thread[i] = itm;
    else if (itm.kind === "summary") { it.thread = it.thread.filter((e) => e.kind !== "summary"); it.thread.unshift(itm); }
    else it.thread.push(itm);
    if (S.active !== it) return;
    const entry = answers.get(itm.id);
    if (entry) {
      entry.item = itm;
      updateQa(entry);
      if (itm.state === "thinking" && !entry.autoRead && composer.autoRead()) autoRead(entry);
      feedAnswer(entry);
      renderAi();
      return;
    }
    const follow = itm.kind !== "summary";
    renderThread();
    if (follow) scrollDown();
  });

  on("files:delta", ({ id, text }) => {
    const entry = answers.get(id);
    if (!entry) {
      for (const it of S.items) {
        const e = it.thread.find((x) => x.id === id);
        if (e) e.answer = (e.answer || "") + text;
      }
      return;
    }
    const follow = nearBottom();
    entry.item = { ...entry.item, answer: (entry.item.answer || "") + text, state: "thinking" };
    const stored = S.active?.thread.find((e) => e.id === id);
    if (stored) stored.answer = entry.item.answer;
    updateQa(entry);
    feedAnswer(entry);
    if (follow && entry.item.kind !== "summary") scrollDown();
  });

  on("files:thread", ({ key, items }) => {
    const it = itemOf(key);
    if (!it) return;
    it.thread = items || [];
    if (S.active === it) renderThread();
  });

  on("files:scan", (st) => {
    const it = itemOf(st.key);
    if (!it) return;
    it.scan = st;
    if (S.active !== it) return;
    renderScan();
    if (st.done) renderInfo();      // taille totale dans « Informations »
  });

  on("chat:models", ({ models }) => {
    S.models = models || [];
    api.files_model().then((m) => { S.model = m || ""; if (S.active) renderAi(); });
  });
  on("chat:available", () => {
    if (!S.active) return;
    renderAi();
    maybeSummarize(S.active);
  });
  on("setting", ({ key }) => {
    if (key === "files_model") api.files_model().then((m) => { S.model = m || ""; if (S.active) renderAi(); });
  });

  // Éléments de la session précédente (chargés à l'affichage)
  S.items = (state.files?.recent || []).map(item);
  S.active = S.items[0] || null;
  render();

  return {
    el,
    onShow() { if (S.active) load(S.active); },
    /** Glisser-déposer : un élément par fichier ou dossier déposé. */
    onDrop(paths) { open(paths); },
    focus() {
      (S.active ? composer.input : emptyPick[0])?.focus();
    },
  };
}
