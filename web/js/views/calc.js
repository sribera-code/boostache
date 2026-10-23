// Vue Calculatrice : console Python pour les calculs (calc.py, interpréteur dans un processus à part).
// Un calcul de l'historique se modifie sur place : lui et ceux qui le suivent sont recalculés.
// En bas, l'assistant (Ollama) en conversation : il fait les calculs demandés en français,
// modifie les calculs précédents, explique un résultat ou une erreur.
import { h, plural, parseDate, formatShort } from "../dom.js";
import { markFound } from "../palette.js";
import { ico, iconButton, openMenu, openMenuBelow, openModal, toast } from "../ui.js";
import { renderMarkdown } from "../markdown.js";
import { createComposer, createSuggestions, conversationSpeech } from "../composer.js";

const SIDE_KEY = "boostache.calc.side";
const CONVO_KEY = "boostache.calc.convo";     // "0" : conversation de l'assistant repliée
const HISTORY_MAX = 200;       // calculs rappelés par ↑ ↓
const QA_BUSY = new Set(["thinking", "running"]);
const READ_ALL = "calc:all";                  // lecture de toute la conversation (bouton Lire)
const sourceOf = (id) => `calc:${id}`;        // lecture d'une réponse
// Demandes toutes prêtes (createSuggestions) : « fill » remplit le champ à compléter, « ask » est envoyée
const STARTERS = [
  { icon: "calculator", label: "Mensualité d'un prêt", fill: "Mensualité d'un prêt de … € sur … ans à … %" },
  { icon: "scroll-text", label: "Prix TTC", fill: "Prix TTC de … € HT (TVA à 20 %)" },
  { icon: "calendar-clock", label: "Jours avant une date", fill: "Combien de jours avant le … ?" },
  { icon: "arrow-up", label: "Évolution en %", fill: "Évolution en % de … à …" },
  { icon: "lightbulb", label: "Que sais-tu calculer ?", ask: "Que peux-tu calculer pour moi ? Donne quelques exemples courts." },
];
const TICK = 500;              // durée d'un calcul en cours, rafraîchie toutes les… (ms)
const THIN = " ";         // espace fine insécable (séparateur des milliers)

// Aide : extraits insérés dans le champ (… : emplacement du curseur)
const HELP = [
  ["Base", [
    ["2**10", "Puissance"], ["17 // 5", "Division entière"], ["17 % 5", "Reste"], ["round(…, 2)", "Arrondi à 2 décimales"],
    ["abs(…)", "Valeur absolue"], ["_", "Dernier résultat (aussi ans)"]]],
  ["Maths", [
    ["sqrt(…)", "Racine carrée"], ["cbrt(…)", "Racine cubique"], ["exp(…)", "Exponentielle"], ["log(…)", "Logarithme népérien (log(x, base))"],
    ["log10(…)", "Logarithme décimal"], ["sin(radians(…))", "Sinus d'un angle en degrés"], ["degrees(…)", "Radians → degrés"],
    ["factorial(…)", "Factorielle"], ["comb(…, k)", "Combinaisons (k parmi n)"], ["gcd(…)", "PGCD"], ["lcm(…)", "PPCM"],
    ["pi", "π"], ["e", "Nombre d'Euler"], ["inf", "Infini"]]],
  ["Statistiques", [
    ["mean([…])", "Moyenne"], ["median([…])", "Médiane"], ["stdev([…])", "Écart type (échantillon)"],
    ["sum([…])", "Somme"], ["max([…])", "Maximum"]]],
  ["Calcul exact", [
    ["Fraction(1, 3) + Fraction(1, 6)", "Fractions exactes"], ['Decimal("0.1") + Decimal("0.2")', "Décimaux exacts (argent)"]]],
  ["Pratique", [
    ["ttc(…)", "Prix TTC d'un prix HT (tva=20 par défaut)"], ["ht(…)", "Prix HT d'un prix TTC"],
    ["variation(…, après)", "Évolution en % de avant à après"], ["pourcentage(…, total)", "Part en % du total"],
    ["mensualite(200000, 3.5, 20)", "Mensualité d'un prêt : capital, taux annuel %, années"],
    ["interets(1000, 3, 10)", "Capital après intérêts composés : capital, taux %, années"]]],
  ["Dates", [
    ["date.today()", "Aujourd'hui"], ["(date(2026, 12, 25) - date.today()).days", "Jours avant une date"],
    ["datetime.now() + timedelta(days=90)", "Dans 90 jours"], ["jour(date(2027, 7, 14))", "Jour de la semaine"]]],
  ["Tableaux (numpy)", [
    ["np.linspace(0, 10, 5)", "5 valeurs régulières de 0 à 10"], ["np.array([1, 2, 3]) * 2", "Calcul sur tout un tableau"],
    ["np.cumsum([…])", "Sommes cumulées"]]],
  ["Programmation", [
    ["[x**2 for x in range(10)]", "Liste calculée"], ["def f(x):\n    return …", "Fonction (Maj+Entrée : nouvelle ligne)"],
    ["for i in range(5):\n    print(i, …)", "Boucle"]]],
];

/** Nombre (texte de Python) en notation française : 1 234 567,89 ; 1,5 × 10³⁰. */
function frenchNumber(text) {
  if (!text) return "";
  if (/^-?\d+$/.test(text)) {
    const neg = text.startsWith("-");
    const digits = neg ? text.slice(1) : text;
    if (digits.length < 5) return "";
    return (neg ? "−" : "") + digits.replace(/\B(?=(\d{3})+(?!\d))/g, THIN);
  }
  const n = Number(text);
  if (!Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  if (abs !== 0 && (abs >= 1e15 || abs < 1e-4)) {
    const exp = Math.floor(Math.log10(abs));
    const mant = n / 10 ** exp;
    const sup = String(exp).replace(/[-0-9]/g, (c) => "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"["-0123456789".indexOf(c)]);
    return `${mant.toLocaleString("fr-FR", { maximumFractionDigits: 10 })} × 10${sup}`;
  }
  return n.toLocaleString("fr-FR", { maximumFractionDigits: 12 }).replace(/ /g, THIN);
}

function highlight(code) {
  const el = h("code", { class: "hljs language-python" });
  if (window.hljs?.getLanguage("python")) {
    try { el.innerHTML = window.hljs.highlight(code, { language: "python", ignoreIllegals: true }).value; return el; }
    catch { /* texte brut */ }
  }
  el.textContent = code;
  return el;
}

export function createCalcView(ctx, state) {
  const { api, on, store } = ctx;
  const boot = state.calc || {};
  const S = {
    entries: boot.entries || [],
    vars: boot.vars || [],
    session: boot.session || "",
    running: false,
    started: 0,
    history: (boot.entries || []).map((e) => e.code).slice(-HISTORY_MAX),
    cursor: -1,          // position dans l'historique (↑ ↓), -1 : texte en cours
    draft: "",
    editing: null,       // calcul modifié sur place (son n)
    convo: boot.conversation || [],
    model: boot.model || "",
    models: state.chat?.models || [],
  };
  let side = true;
  let convoOpen = true;
  try {
    side = localStorage.getItem(SIDE_KEY) !== "0";
    convoOpen = localStorage.getItem(CONVO_KEY) !== "0";
  } catch { /* stockage indisponible */ }

  // ─────────────────────────────────────────────
  //  Structure
  // ─────────────────────────────────────────────
  const log = h("div", { class: "calc-log", role: "log", "aria-live": "polite" });
  const intro = h("div", { class: "empty calc-intro" },
    h("div", { class: "glyph" }, ico("calculator", 22)),
    h("h3", { text: "Calculez en Python" }),
    h("p", {}, "Tapez un calcul puis Entrée : ", h("code", { text: "12 * 1.2" }), ", ", h("code", { text: "sqrt(2)" }), ", ",
      h("code", { text: "ttc(49.90)" }), ", ", h("code", { text: "x = 3" }), "… Les fonctions de ", h("code", { text: "math" }),
      " sont prêtes, les variables restent d'un calcul à l'autre. Ou demandez à l'assistant, en bas, en français."));
  const input = h("textarea", { class: "calc-input", rows: "1", spellcheck: "false", "aria-label": "Calcul",
    placeholder: "Calcul Python — Entrée : calculer · Maj+Entrée : nouvelle ligne · ↑ : calculs précédents" });
  const runBtn = h("button", { class: "send-btn calc-run", type: "button", "aria-label": "Calculer",
    dataset: { tip: "Calculer", kbd: "Entrée" }, onClick: () => run() }, ico("play", 15, 2.2));
  const status = h("div", { class: "calc-status", hidden: true });
  const inputWrap = h("div", { class: "calc-input-wrap" },
    h("div", { class: "calc-editor" }, h("span", { class: "calc-prompt", text: "›" }), input, runBtn), status);
  const varsEl = h("div", { class: "calc-vars" });
  const helpEl = h("div", { class: "calc-help" }, ...HELP.map(([title, items]) => h("div", { class: "calc-help-group" },
    h("div", { class: "calc-side-title", text: title }),
    h("div", { class: "calc-chips" }, ...items.map(([code, tip]) => h("button", { class: "calc-chip", type: "button",
      text: code.includes("\n") ? code.split("\n")[0] + " …" : code, dataset: { tip },
      onClick: () => insert(code) }))))),
  h("div", { class: "calc-tips" },
    h("div", { class: "calc-side-title", text: "Astuces" }),
    h("p", {}, "Écrivez ", h("code", { text: "3.5" }), " (point) : ", h("code", { text: "3,5" }), " fait deux nombres."),
    h("p", { text: "Clic sur un calcul : le modifier (ceux qui le suivent sont recalculés). Clic sur un résultat : le copier." }),
    h("p", { text: "En bas, l'assistant calcule ce que vous lui demandez en français et peut modifier vos calculs : « mets 25 ans au lieu de 20 »." }),
    h("p", { text: "Un calcul trop long s'interrompt (les variables sont alors effacées)." })));
  const sidePanel = h("aside", { class: "calc-side" },
    h("div", { class: "calc-side-title head", text: "Variables" }), varsEl, helpEl);
  const sideBtn = iconButton("panel-right", "", () => setSide(!side), { size: 15, cls: "sm" });

  // Assistant, en bas : conversation, suggestions (toujours proposées), zone de saisie
  const turns = new Map();       // id → réponse affichée
  const convo = h("div", { class: "asst-thread calc-convo", role: "log", "aria-label": "Conversation avec l'assistant" });
  const convoScroll = h("div", { class: "calc-convo-scroll" }, convo);
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle de l'assistant",
    dataset: { tip: "Modèle de l'assistant — il lui faut un modèle qui sait utiliser des outils (qwen3.5, gemma4…)" },
    onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget) },
  ico("sparkles", 14), modelName, ico("chevron-down", 14));
  const clearConvoBtn = iconButton("eraser", "Effacer la conversation", () => api.calc_clear_conversation());
  const foldBtn = iconButton("chevron-down", "", () => setConvoOpen(!convoOpen), { cls: "pane-fold" });
  let readingMode = "answers";   // ce que suit la lecture de toute la conversation
  const composer = createComposer(ctx, {
    label: "Demande à l'assistant de la calculatrice", maxlength: 2000, maxHeight: 140,
    tools: [iconButton("mic", "Dictée vocale", () => { composer.input.focus(); api.dictate(); }, { kbd: "Win+H" }), modelBtn],
    extra: [foldBtn, clearConvoBtn],
    speakKey: "calc_speak",
    // Activée pendant une réponse : elle est lue tout de suite
    onAutoRead: (on) => { const t = busyTurn(); if (on && t?.item.answer) autoRead(t); },
    read: {
      source: READ_ALL,
      match: (source) => source === READ_ALL || (source.startsWith("calc:") && turns.has(source.slice(5))),
      speak: (mode) => {
        readingMode = mode;
        ctx.speak(convoSpeech(mode), READ_ALL, true, !!busyTurn());
      },
    },
    onSend: () => ask(),
    onStop: () => api.calc_stop(),
    sendTip: "Demander à l'assistant", stopTip: "Arrêter la réponse",
  });
  const suggest = createSuggestions(composer, (text) => ask(text));
  const assist = h("div", { class: "calc-assist", dataset: { ollama: "" } }, convoScroll, suggest.el, composer.el);

  const el = h("section", { class: "calc" },
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Calculatrice" }),
      h("span", { class: "view-sub", text: "console Python" }),
      h("div", { class: "head-spacer" }),
      iconButton("rotate-ccw", "Réinitialiser : effacer les variables", () => reset(), { size: 15, cls: "sm" }),
      iconButton("trash-2", "Effacer l'historique…", () => confirmClear(), { size: 15, cls: "sm" }),
      sideBtn),
    h("div", { class: "calc-body" },
      h("div", { class: "calc-main" }, h("div", { class: "calc-scroll" }, intro, log), inputWrap, assist),
      sidePanel));

  function setSide(on) {
    side = on;
    sidePanel.hidden = !on;
    sideBtn.classList.toggle("on", on);
    sideBtn.dataset.tip = on ? "Masquer les variables et l'aide" : "Variables et aide";
    sideBtn.setAttribute("aria-label", sideBtn.dataset.tip);
    try { localStorage.setItem(SIDE_KEY, on ? "1" : "0"); } catch { /* stockage indisponible */ }
  }

  // ─────────────────────────────────────────────
  //  Saisie
  // ─────────────────────────────────────────────
  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
  }

  /** Extrait inséré au curseur ; « … » marque où placer le curseur. */
  function insert(code) {
    const at = code.indexOf("…");
    const text = code.replace("…", "");
    input.focus();
    const start = input.selectionStart;
    const selected = input.value.slice(start, input.selectionEnd);
    // Une sélection devient l'argument (sqrt(…) autour de « 2 »)
    const filled = at >= 0 && selected ? text.slice(0, at) + selected + text.slice(at) : text;
    input.setRangeText(filled, start, input.selectionEnd, "end");
    if (at >= 0) {
      const pos = start + at + (selected ? selected.length : 0);
      input.setSelectionRange(pos, pos);
    }
    autosize();
  }

  input.addEventListener("input", () => { S.cursor = -1; autosize(); });
  input.addEventListener("keydown", (ev) => {
    if (ev.isComposing) return;
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      // Ligne qui ouvre un bloc (def, for, if…) : nouvelle ligne indentée plutôt qu'un calcul incomplet
      const before = input.value.slice(0, input.selectionStart);
      if (/:\s*$/.test(before.split("\n").pop())) {
        const indent = (before.split("\n").pop().match(/^\s*/)[0] || "") + "    ";
        input.setRangeText(`\n${indent}`, input.selectionStart, input.selectionEnd, "end");
        autosize();
        return;
      }
      if (!S.running) run();         // pendant un calcul : seul le bouton l'interrompt
    } else if (ev.key === "Tab" && !ev.shiftKey) {
      ev.preventDefault();
      input.setRangeText("    ", input.selectionStart, input.selectionEnd, "end");
    } else if (ev.key === "Escape" && input.value) {
      ev.preventDefault();
      input.value = "";
      S.cursor = -1;
      autosize();
    } else if ((ev.key === "ArrowUp" || ev.key === "ArrowDown") && !ev.shiftKey && !ev.ctrlKey) {
      const value = input.value;
      const first = !value.slice(0, input.selectionStart).includes("\n");
      const last = !value.slice(input.selectionEnd).includes("\n");
      if ((ev.key === "ArrowUp" && first) || (ev.key === "ArrowDown" && last)) {
        if (recall(ev.key === "ArrowUp" ? -1 : 1)) ev.preventDefault();
      }
    }
  });

  /** ↑ ↓ : calculs précédents, puis retour au texte en cours. */
  function recall(step) {
    const n = S.history.length;
    if (!n) return false;
    if (S.cursor === -1) {
      if (step > 0) return false;
      S.draft = input.value;
      S.cursor = n - 1;
    } else {
      const next = S.cursor + step;
      if (next >= n) { S.cursor = -1; input.value = S.draft; autosize(); return true; }
      S.cursor = Math.max(0, next);
    }
    input.value = S.history[S.cursor];
    autosize();
    input.setSelectionRange(input.value.length, input.value.length);
    return true;
  }

  // ─────────────────────────────────────────────
  //  Calculs
  // ─────────────────────────────────────────────
  let tick = null;

  function setRunning(on) {
    S.running = on;
    S.started = on ? Date.now() : 0;
    clearInterval(tick);
    runBtn.replaceChildren(ico(on ? "square" : "play", on ? 13 : 15, 2.2));
    runBtn.dataset.tip = on ? "Interrompre le calcul" : "Calculer";
    runBtn.setAttribute("aria-label", runBtn.dataset.tip);
    runBtn.classList.toggle("stop", on);
    status.hidden = !on;
    if (on) {
      const show = () => {
        const s = Math.floor((Date.now() - S.started) / 1000);
        status.replaceChildren(h("span", { class: "pane-busy" }), h("span", { text: s >= 1 ? `Calcul en cours… ${s} s` : "Calcul en cours…" }),
          s >= 2 ? h("button", { class: "btn ghost sm", type: "button", onClick: interrupt }, "Interrompre") : null);
      };
      show();
      tick = setInterval(show, TICK);
    }
  }

  /** État reçu de Python (calcul de l'assistant, calculs modifiés…) ; flash : calculs à faire ressortir. */
  function applyState(st, flash = []) {
    const known = new Set(S.entries.map((e) => e.n));
    const follow = nearBottom();
    S.entries = st.entries || S.entries;
    S.vars = st.vars || [];
    S.session = st.session ?? S.session;
    const fresh = S.entries.filter((e) => !known.has(e.n)).map((e) => e.n);
    renderLog([...flash, ...fresh]);
    renderVars();
    renderSuggestions();
    if (fresh.length && follow) scrollDown();
  }

  async function run(code = input.value) {
    if (S.running) { interrupt(); return; }
    if (!code.trim()) return;
    setRunning(true);
    const res = await api.calc_run(code);
    setRunning(false);
    if (!res?.ok) { toast(res?.error || "Calcul impossible.", "error"); return; }
    if (code === input.value) { input.value = ""; autosize(); }
    S.cursor = -1;
    if (S.history[S.history.length - 1] !== code) S.history.push(code);
    S.history = S.history.slice(-HISTORY_MAX);
    if (!S.entries.some((e) => e.n === res.entry.n)) S.entries.push(res.entry);
    S.vars = res.vars || [];
    S.session = res.session || S.session;
    renderLog();
    renderVars();
    renderSuggestions();
    scrollDown();
    input.focus();
  }

  /** Calcul modifié : lui et ceux qui le suivent sont recalculés (calc.py, recompute). → réussite */
  async function recompute(e, code) {
    if (S.running) { toast("Un calcul est en cours : attendez qu'il finisse.", "error"); return false; }
    setRunning(true);
    const res = await api.calc_edit(e.n, code);
    setRunning(false);
    if (res?.entries) {
      S.editing = null;
      S.editDraft = null;
      applyState(res, res.changed || []);
    }
    if (!res?.ok) { toast(res?.error || "Calcul impossible.", "error"); return false; }
    const others = (res.changed || []).length - 1;
    if (others > 0) toast(`Calcul modifié : ${plural(others, "calcul suivant recalculé", "calculs suivants recalculés")}.`);
    return true;
  }

  async function interrupt() {
    await api.calc_interrupt();
    toast("Calcul interrompu : les variables ont été effacées.", "info");
  }

  async function reset() {
    const res = await api.calc_reset();
    if (!res?.ok) { toast(res?.error || "Réinitialisation impossible.", "error"); return; }
    S.vars = res.vars || [];
    S.session = res.session;
    renderLog();
    renderVars();
    toast("Variables effacées.", "info");
  }

  async function replay(session) {
    if (S.running) return;
    setRunning(true);
    const res = await api.calc_replay(session);
    setRunning(false);
    if (!res?.ok) { toast(res?.error || "Impossible de rejouer ces calculs.", "error"); return; }
    if (res.entries) S.entries = res.entries;
    S.vars = res.vars || [];
    S.session = res.session || S.session;
    renderLog();
    renderVars();
    toast(res.done < res.total ? `${res.done} calculs rejoués sur ${res.total} (arrêt sur une erreur).`
      : `${res.total} calculs rejoués : leurs variables sont revenues.`);
  }

  function confirmClear() {
    if (!S.entries.length) return;
    openModal({
      title: "Effacer l'historique des calculs ?",
      width: 420,
      body: h("p", { class: "lk-confirm", text: "Les calculs affichés disparaissent ; les variables restent." }),
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: "Effacer", icon: "trash-2", kind: "danger", onClick: async () => {
          await api.calc_clear();
          S.entries = [];
          renderLog();
          renderSuggestions();
          return true;
        } },
      ],
    });
  }

  // ─────────────────────────────────────────────
  //  Modification d'un calcul
  // ─────────────────────────────────────────────
  function startEdit(e) {
    S.editing = e.n;
    S.editDraft = null;
    S.editFocus = true;
    renderLog();
  }

  function cancelEdit() {
    S.editing = null;
    S.editDraft = null;
    renderLog();
    input.focus();
  }

  /** Code du calcul, modifiable à sa place : Entrée recalcule (et les suivants), Échap annule. */
  function editorEl(e) {
    const area = h("textarea", { class: "calc-edit-input", rows: "1", spellcheck: "false",
      "aria-label": `Modifier le calcul #${e.n}` });
    area.value = S.editDraft ?? e.code;
    const fit = () => {
      area.style.height = "auto";
      area.style.height = `${Math.min(area.scrollHeight, 300)}px`;
    };
    // Calculs recalculés avec lui : ceux qui le suivent dans sa session
    const later = e.session ? S.entries.filter((x) => x.session === e.session && x.n > e.n).length : 0;
    const submitBtn = h("button", { class: "btn primary sm", type: "button", dataset: { kbd: "Entrée" },
      onClick: () => submit() }, ico("play", 13), "Recalculer");
    const box = h("div", { class: "calc-edit" }, area,
      h("div", { class: "calc-edit-bar" },
        h("span", { class: "calc-edit-hint", text: later
          ? `${plural(later, "calcul suivant sera recalculé", "calculs suivants seront recalculés")} · Maj+Entrée : nouvelle ligne`
          : "Maj+Entrée : nouvelle ligne" }),
        h("button", { class: "btn ghost sm", type: "button", dataset: { kbd: "Échap" }, onClick: cancelEdit }, "Annuler"),
        submitBtn));

    async function submit() {
      const code = area.value;
      if (!code.trim()) { toast("Le calcul est vide.", "error"); return; }
      if (code === e.code) { cancelEdit(); return; }
      submitBtn.disabled = true;
      if (!await recompute(e, code)) submitBtn.disabled = false;
    }
    area.addEventListener("input", () => { S.editDraft = area.value; fit(); });
    area.addEventListener("keydown", (ev) => {
      if (ev.isComposing) return;
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        const line = area.value.slice(0, area.selectionStart).split("\n").pop();
        if (/:\s*$/.test(line)) {        // ouvre un bloc : nouvelle ligne indentée
          area.setRangeText(`\n${line.match(/^\s*/)[0]}    `, area.selectionStart, area.selectionEnd, "end");
          fit();
          return;
        }
        submit();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        cancelEdit();
      } else if (ev.key === "Tab" && !ev.shiftKey) {
        ev.preventDefault();
        area.setRangeText("    ", area.selectionStart, area.selectionEnd, "end");
      }
    });
    const focus = S.editFocus;
    S.editFocus = false;
    requestAnimationFrame(() => {
      fit();
      if (!focus) return;
      area.focus();
      area.setSelectionRange(area.value.length, area.value.length);
    });
    return box;
  }

  // ─────────────────────────────────────────────
  //  Affichage
  // ─────────────────────────────────────────────
  const scrollDown = () => { const sc = log.parentElement; sc.scrollTop = sc.scrollHeight; };
  const nearBottom = () => { const sc = log.parentElement; return sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80; };

  /** Calcul n de l'historique montré (référence sous une réponse de l'assistant). */
  function showEntry(n) {
    const node = log.querySelector(`.calc-entry[data-n="${n}"]`);
    if (!node) return;
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    node.classList.remove("flash");
    void node.offsetWidth;            // relance l'animation
    node.classList.add("flash");
  }

  function entryEl(e) {
    const badges = h("span", { class: "calc-badges" },
      e.by === "ia" ? h("span", { class: "calc-badge ia", dataset: { tip: "Calcul de l'assistant" } }, ico("sparkles", 11)) : null,
      e.edited ? h("span", { class: "calc-badge", dataset: { tip: `Modifié${e.edited_by === "ia" ? " par l'assistant" : ""}`
        + (parseDate(e.edited) ? ` (${formatShort(parseDate(e.edited))})` : "") } }, ico("pencil", 11)) : null,
      h("span", { class: "calc-n", text: `#${e.n}` }));
    const codeBlock = S.editing === e.n ? editorEl(e)
      : h("pre", { class: "calc-code", dataset: { tip: "Modifier ce calcul (ceux qui le suivent sont recalculés)" },
        onClick: () => { if (!window.getSelection().toString() && !S.running) startEdit(e); } },
      highlight(e.code), badges);
    const out = [];
    if (e.output) out.push(h("pre", { class: "calc-output", text: e.output.replace(/\n$/, "") }));
    if (e.result != null) {
      const fr = frenchNumber(e.number);
      const approx = ["Fraction", "Decimal"].includes(e.type);
      out.push(h("div", { class: "calc-result" },
        h("button", { class: "calc-value", type: "button", text: e.result, dataset: { tip: "Copier" },
          onClick: () => ctx.copy(e.result, "Résultat copié") }),
        fr && fr !== e.result ? h("button", { class: "calc-fr", type: "button", text: `${approx ? "≈ " : ""}${fr}`,
          dataset: { tip: "Notation française — copier" }, onClick: () => ctx.copy(fr, "Copié") }) : null));
    }
    if (e.error) {
      const lines = e.error.split("\n");
      const lastLine = lines[lines.length - 1];
      const full = h("pre", { class: "calc-error", text: e.error, hidden: true });
      const short = h("div", { class: `calc-error-line${e.interrupted ? " muted" : ""}` }, ico(e.interrupted ? "square" : "triangle-alert", 13),
        h("span", { text: lastLine }),
        lines.length > 1 ? h("button", { class: "calc-more", type: "button", text: "détails",
          onClick: (ev) => { full.hidden = !full.hidden; ev.currentTarget.textContent = full.hidden ? "détails" : "masquer"; } }) : null);
      out.push(short, full);
    }
    if (!out.length) out.push(h("div", { class: "calc-none", text: "✓" }));
    const root = h("div", { class: `calc-entry${e.by === "ia" ? " by-ia" : ""}`, dataset: { n: String(e.n) } },
      codeBlock, h("div", { class: "calc-out" }, ...out));
    root.addEventListener("contextmenu", (ev) => {
      if (window.getSelection().toString() || ev.target.closest(".calc-edit")) return;
      ev.preventDefault();
      const ollama = !!store.ollama && !!S.model;
      openMenu([
        { label: "Modifier", icon: "pencil", disabled: S.running, onSelect: () => startEdit(e) },
        { label: "Reprendre dans le champ", icon: "arrow-down", onSelect: () => { input.value = e.code; autosize(); input.focus(); } },
        { label: "Recalculer à la suite", icon: "play", onSelect: () => run(e.code) },
        { label: "Copier le calcul", icon: "copy", onSelect: () => ctx.copy(e.code) },
        { label: "Copier le résultat", icon: "copy", disabled: e.result == null, onSelect: () => ctx.copy(e.result) },
        ollama ? "-" : null,
        ollama ? { label: "Expliquer (assistant)", icon: "sparkles",
          onSelect: () => ask(`Explique simplement le calcul #${e.n} et son résultat.`) } : null,
        ollama && e.error && !e.interrupted ? { label: "Corriger l'erreur (assistant)", icon: "sparkles",
          onSelect: () => ask(`Le calcul #${e.n} donne une erreur : explique-la simplement et corrige le calcul.`) } : null,
        "-",
        { label: "Retirer de l'historique", icon: "x", onSelect: async () => {
          await api.calc_delete(e.n);
          S.entries = S.entries.filter((x) => x !== e);
          renderLog();
          renderSuggestions();
        } },
      ], ev.clientX, ev.clientY);
    });
    return root;
  }

  /** flash : calculs (n) qui viennent de changer, mis en évidence. */
  function renderLog(flash = []) {
    intro.hidden = S.entries.length > 0;
    const nodes = [];
    S.entries.forEach((e, i) => {
      const node = entryEl(e);
      if (flash.includes(e.n)) node.classList.add("flash");
      nodes.push(node);
      const next = S.entries[i + 1];
      // Fin des calculs d'une session passée de l'interpréteur : leurs variables sont perdues
      if (e.session && e.session !== S.session && (!next || next.session !== e.session)) {
        const ok = S.entries.some((x) => x.session === e.session && !x.error);
        nodes.push(h("div", { class: "calc-sep" }, h("span", { text: "Variables effacées depuis ces calculs" }),
          ok ? h("button", { class: "btn ghost sm", type: "button", dataset: { tip: "Recalculer ces calculs pour retrouver leurs variables" },
            onClick: () => replay(e.session) }, ico("rotate-ccw", 13), "Rejouer") : null));
      }
    });
    log.replaceChildren(...nodes);
  }

  function renderVars() {
    if (!S.vars.length) {
      varsEl.replaceChildren(h("div", { class: "calc-empty", text: "Aucune variable : x = 42 en crée une." }));
      return;
    }
    varsEl.replaceChildren(...S.vars.map((v) => h("button", { class: "calc-var", type: "button",
      dataset: { tip: `${v.type} — clic : insérer` }, onClick: () => insert(v.name) },
    h("span", { class: "calc-var-name", text: v.name }),
    h("span", { class: "calc-var-value", text: v.type === "fonction" ? v.value.slice(v.name.length) : `= ${v.value}` }))));
  }

  // ─────────────────────────────────────────────
  //  Assistant
  // ─────────────────────────────────────────────
  function modelItems() {
    return [
      { section: "Modèle de l'assistant" },
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === S.model, onSelect: async () => {
          await ctx.saveSetting("calc_model", m);
          S.model = m;
          renderAssist();
        } }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  const busyTurn = () => [...turns.values()].find((t) => QA_BUSY.has(t.item.state)) || null;
  const convoAtEnd = () => convoScroll.scrollHeight - convoScroll.scrollTop - convoScroll.clientHeight < 60;
  const scrollConvo = () => requestAnimationFrame(() => { convoScroll.scrollTop = convoScroll.scrollHeight; });

  function setConvoOpen(open) {
    convoOpen = open;
    try { localStorage.setItem(CONVO_KEY, open ? "1" : "0"); } catch { /* stockage indisponible */ }
    renderAssist();
    if (open) scrollConvo();
  }

  function renderAssist() {
    const has = S.convo.length > 0;
    convoScroll.hidden = !has || !convoOpen;
    foldBtn.hidden = clearConvoBtn.hidden = !has;
    foldBtn.classList.toggle("folded", !convoOpen);
    const tip = convoOpen ? "Masquer la conversation" : "Afficher la conversation";
    foldBtn.dataset.tip = tip;
    foldBtn.setAttribute("aria-label", tip);
    const ollama = !!store.ollama;
    composer.update({ busy: !!busyTurn(), ready: ollama && !!S.model });
    composer.input.disabled = !ollama;
    composer.setPlaceholder(S.model
      ? "Demandez un calcul en français, ou de modifier un calcul : « mets 25 ans au lieu de 20 »…"
      : "Aucun modèle Ollama installé : l'assistant en a besoin (section Ollama).");
    modelName.textContent = S.model || "Aucun modèle";
    renderSuggestions();
  }

  /** Suggestions au-dessus du champ, proposées même quand la conversation n'est pas vide :
   *  sur le dernier calcul (corriger, expliquer), puis des demandes toutes prêtes. */
  function renderSuggestions() {
    const last = S.entries[S.entries.length - 1];
    const items = [];
    if (last?.error && !last.interrupted) {
      items.push({ icon: "triangle-alert", label: "Corriger l'erreur", main: true,
        tip: `L'assistant explique l'erreur du calcul #${last.n} et le corrige`,
        ask: `Le calcul #${last.n} donne une erreur : explique-la simplement et corrige le calcul.` });
    } else if (last) {
      items.push({ icon: "lightbulb", label: "Expliquer le dernier calcul", tip: `Ce que fait le calcul #${last.n} et ce que veut dire son résultat`,
        ask: `Explique simplement le calcul #${last.n} et son résultat.` });
    }
    if (S.entries.length > 1) {
      items.push({ icon: "list-checks", label: "Vérifier mes calculs", tip: "L'assistant relit vos derniers calculs : erreur de formule, d'unité, de pourcentage…",
        ask: "Relis mes derniers calculs : y a-t-il une erreur de formule, d'unité ou de pourcentage ? Réponds brièvement." });
    }
    suggest.set([...items, ...STARTERS], !!busyTurn());
  }

  /** preset : demande d'une suggestion ou d'un menu (le champ garde son texte), sinon celle du champ. */
  async function ask(preset) {
    const question = (preset ?? composer.input.value).trim();
    if (!question) return;
    if (!preset) composer.setValue("");
    if (!convoOpen) setConvoOpen(true);
    const res = await api.calc_ask(question);
    if (!res?.ok) {
      toast(res?.error || "L'assistant ne répond pas.", "error");
      if (!preset && !composer.input.value) composer.setValue(question);
    }
  }

  /** Nombre court pour les références de calculs (1 159,92 ; 0,3333) ; sinon le texte de Python. */
  function shortNumber(number, result) {
    const n = Number(number);
    const abs = Math.abs(n);
    if (number && Number.isFinite(n) && abs < 1e15 && (n === 0 || abs >= 1e-4)) {
      return n.toLocaleString("fr-FR", { maximumFractionDigits: abs >= 100 ? 2 : abs >= 1 ? 4 : 6 }).replace(/\s/g, THIN);
    }
    return frenchNumber(number) || result;
  }

  /** Calcul fait ou modifié par l'assistant, sous sa réponse : clic → le calcul dans l'historique. */
  function refEl(ref) {
    if (ref.op === "refused") {
      return h("span", { class: "calc-ref error", dataset: { tip: ref.code } }, ico("x", 12),
        h("span", { class: "calc-ref-code", text: ref.error }));
    }
    const exists = S.entries.some((e) => e.n === ref.n);
    const value = ref.error || (ref.result != null ? `= ${shortNumber(ref.number, ref.result)}` : "✓");
    const what = ref.op === "edit"
      ? `Calcul #${ref.n} modifié${ref.count > 1 ? `, ${plural(ref.count - 1, "suivant recalculé", "suivants recalculés")}` : ""}`
      : `Calcul #${ref.n}`;
    return h("button", { class: `calc-ref${ref.error ? " error" : ""}`, type: "button", disabled: !exists,
      dataset: { tip: exists ? `${what} — clic : le voir dans l'historique` : `${what} — plus dans l'historique` },
      onClick: () => showEntry(ref.n) },
    ico(ref.op === "edit" ? "pencil" : "play", 12), h("span", { class: "calc-ref-n", text: `#${ref.n}` }),
    h("span", { class: "calc-ref-code", text: ref.code.split("\n")[0] }), h("span", { class: "calc-ref-val", text: value }));
  }

  function turnEl(item) {
    const source = sourceOf(item.id);
    const answer = h("div", { class: "md asst-answer" });
    const phase = h("div", { class: "asst-phase" });
    const refs = h("div", { class: "calc-refs" });
    const meta = h("div", { class: "asst-meta" });
    const speak = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(entry.item.answer, source, true), { size: 15, cls: "sm" });
    speak.dataset.speakSource = source;
    speak.dataset.tipIdle = "Lire";
    const actions = h("div", { class: "asst-actions" },
      iconButton("copy", "Copier la réponse", () => ctx.copy(entry.item.answer, "Copié"), { size: 15, cls: "sm" }),
      speak,
      iconButton("rotate-ccw", "Reposer la question", () => ask(entry.item.question), { size: 15, cls: "sm" }),
      iconButton("x", "Retirer", () => api.calc_delete_turn(item.id), { size: 15, cls: "sm" }));
    const reply = h("div", { class: "asst-reply" }, phase, answer, refs, meta, actions);
    const root = h("div", { class: "asst-qa" }, h("div", { class: "asst-question", text: item.question }), reply);
    reply.addEventListener("contextmenu", (ev) => {
      const sel = window.getSelection().toString();
      ev.preventDefault();
      openMenu([
        sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
        { label: "Copier la réponse", icon: "copy", disabled: !entry.item.answer, onSelect: () => ctx.copy(entry.item.answer, "Copié") },
        { label: "Lire", icon: "volume-2", disabled: !entry.item.answer, onSelect: () => ctx.speak(entry.item.answer, source, true) },
        "-",
        { label: "Retirer", icon: "x", onSelect: () => api.calc_delete_turn(item.id) },
      ], ev.clientX, ev.clientY);
    });
    const entry = { item, root, answer, phase, refs, meta, actions, reply, shown: null, autoRead: false };
    turns.set(item.id, entry);
    updateTurn(entry);
    return root;
  }

  function updateTurn(entry) {
    const it = entry.item;
    const busy = QA_BUSY.has(it.state);
    entry.reply.classList.toggle("busy", busy);
    entry.phase.hidden = !busy || (it.state === "thinking" && !!it.answer);
    entry.phase.replaceChildren(h("span", { class: "pane-busy" }), h("span", { text: it.phase || "…" }));
    if (entry.shown !== it.answer) {
      entry.shown = it.answer;
      entry.answer.innerHTML = it.answer ? renderMarkdown(it.answer, { highlight: !busy }) : "";
    }
    entry.answer.hidden = !it.answer;
    const calcs = it.calcs || [];
    entry.refs.replaceChildren(...calcs.map(refEl));
    entry.refs.hidden = !calcs.length;
    const notes = [];
    if (it.state === "stopped") notes.push(h("span", { class: "asst-note", text: "Arrêtée." }));
    if (it.state === "error") notes.push(h("span", { class: "asst-note error", text: it.error || "Échec." }));
    const at = parseDate(it.at);
    entry.meta.replaceChildren(...notes, busy ? "" : h("span", { class: "asst-model", text: `${it.model}${at ? ` · ${formatShort(at)}` : ""}` }));
    entry.actions.hidden = busy || !it.answer;
  }

  function renderConvo() {
    turns.clear();
    convo.replaceChildren(...S.convo.map(turnEl));
    renderAssist();
    ctx.emit("speak-sources-changed");
  }

  // ── Lecture à voix haute des réponses ──
  function autoRead(entry) {
    entry.autoRead = true;
    ctx.speak(entry.item.answer, sourceOf(entry.item.id), true, true);
  }

  function convoSpeech(mode) {
    return conversationSpeech(S.convo.map((t) => ({ ask: t.question, answer: t.answer })), mode);
  }

  function feedAnswer(entry) {
    const final = !QA_BUSY.has(entry.item.state);
    ctx.feed(sourceOf(entry.item.id), entry.item.answer, final);
    if (ctx.feeding(READ_ALL)) ctx.feed(READ_ALL, convoSpeech(readingMode), final);
  }

  // ── Évènements Python ──
  on("calc:state", (st) => applyState(st));
  on("calc:running", ({ on: running }) => { if (running !== S.running) setRunning(running); });

  on("calc:qa", ({ item }) => {
    const i = S.convo.findIndex((t) => t.id === item.id);
    if (i >= 0) S.convo[i] = item;
    else S.convo.push(item);
    const entry = turns.get(item.id);
    if (!entry) {
      renderConvo();
      scrollConvo();
      return;
    }
    const follow = convoAtEnd();
    entry.item = item;
    updateTurn(entry);
    feedAnswer(entry);
    renderAssist();
    if (follow) scrollConvo();
  });

  on("calc:delta", ({ id, text }) => {
    const i = S.convo.findIndex((t) => t.id === id);
    if (i < 0) return;
    const item = { ...S.convo[i], answer: (S.convo[i].answer || "") + text };
    S.convo[i] = item;
    const entry = turns.get(id);
    if (!entry) return;
    const follow = convoAtEnd();
    entry.item = item;
    updateTurn(entry);
    if (!entry.autoRead && composer.autoRead()) autoRead(entry);
    else feedAnswer(entry);
    if (follow) scrollConvo();
  });

  on("calc:conversation", ({ items }) => {
    S.convo = items || [];
    renderConvo();
  });

  const refreshModel = () => api.calc_model().then((m) => { S.model = m || ""; renderAssist(); });
  on("chat:models", ({ models }) => { S.models = models || []; refreshModel(); });
  on("chat:available", () => renderAssist());
  on("setting", ({ key }) => { if (key === "calc_model") refreshModel(); });

  setSide(side);
  renderLog();
  renderVars();
  renderConvo();

  return {
    el,
    onShow() {
      api.calc_warm_up();
      requestAnimationFrame(() => { autosize(); scrollDown(); composer.autosize(); });
      scrollConvo();
    },
    focus() { input.focus(); },
    /** Recherche (Ctrl+K) : le calcul trouvé, centré et éclairé. */
    reveal(target, words) {
      showEntry(target?.n);
      markFound(log.querySelector(`.calc-entry[data-n="${target?.n}"]`), words);
    },
  };
}
