// Vues des sites intégrés (WhatsApp, Gmail) : le site tourne dans un contrôle
// WebView2 natif (webpane.py), posé par Python au-dessus de la zone .pane-host
// dont on transmet la position. Sous le site, l'assistant de réponse (Ollama).
import { h, plural } from "../dom.js";
import { renderMarkdown } from "../markdown.js";
import { ico, iconButton, openMenu, openMenuBelow, toast } from "../ui.js";
import { createComposer, conversationSpeech } from "../composer.js";
import { actionSentence, createMailList, importanceIcon } from "./maillist.js";

const WHATSAPP = {
  id: "whatsapp", title: "WhatsApp", icon: "message-circle",
  unread: ["discussion non lue", "discussions non lues"],
  firstRun: "Première ouverture : scannez le QR code avec WhatsApp sur votre téléphone (Appareils connectés). La session est ensuite conservée.",
  hint: "Consigne (facultatif) : plus formel, décline poliment…",
  idle: "Ouvrez une discussion : l'assistant pourra proposer une réponse ou améliorer votre message.",
};
const GMAIL = {
  id: "gmail", title: "Gmail", icon: "mail",
  unread: ["message non lu", "messages non lus"],
  firstRun: "Première ouverture : connectez-vous à votre compte Google. La session est ensuite conservée.",
  hint: "Une question sur vos e-mails, ou une consigne pour les boutons…",
  ask: true,       // questions sur les e-mails (champ de l'assistant)
  long: true,      // propositions = e-mails complets, affichés en entier
  digest: true,    // résumé de la boîte de réception
  list: true,      // liste des e-mails à côté du site (maillist.js)
};

const HOLE_MARGIN = 6;     // px autour d'un menu percé dans le site (son ombre)

export const createWhatsAppView = (ctx, state) => createPaneView(ctx, state, WHATSAPP);
export const createGmailView = (ctx, state) => createPaneView(ctx, state, GMAIL);

function createPaneView(ctx, state, cfg) {
  const { api, on } = ctx;
  const site = { state: "idle", message: "", unread: 0, ...(state.panes?.[cfg.id] || {}) };
  let active = false;
  let lastSent = "";

  const sub = h("span", { class: "view-sub" });
  const status = h("div", { class: "empty" });
  const host = h("div", { class: "pane-host" }, status);
  const assist = createAssistBar(ctx, state, cfg);
  const list = cfg.list ? createMailList(ctx, state, cfg, assist) : null;
  // Liste des e-mails : affichée ou masquée depuis l'en-tête
  const listBtn = list ? iconButton("panel-left", "Liste des e-mails", () => {
    listBtn.classList.toggle("on", list.toggle());
    assist.listChanged();
  }) : null;
  listBtn?.classList.toggle("on", list.shown());
  // Résumés des e-mails déjà dans la liste : le panneau du bas n'en garde que la synthèse
  if (list) assist.setListShown(list.shown);
  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: cfg.title }), sub,
      h("div", { class: "head-spacer" }),
      listBtn,
      iconButton("refresh-cw", `Recharger ${cfg.title}`, () => api.pane_reload(cfg.id))),
    list ? h("div", { class: "pane-body" }, list.el, list.splitter, host) : host,
    assist.el);

  function render() {
    sub.textContent = site.unread ? plural(site.unread, ...cfg.unread) : "";
    ctx.setBadge(cfg.id, site.unread, "unread");
    assist.setReady(site.state === "ready");
    if (site.state === "error") {
      status.replaceChildren(
        h("div", { class: "glyph" }, ico("triangle-alert", 22)),
        h("h3", { text: `${cfg.title} indisponible` }),
        h("p", { text: site.message || "Le chargement a échoué." }),
        h("div", { class: "empty-actions" },
          h("button", { class: "btn", type: "button", onClick: () => api.pane_reload(cfg.id) },
            ico("refresh-cw", 14), "Réessayer")));
    } else if (site.state === "ready") {
      status.replaceChildren();   // visible seulement sous un menu ouvert
    } else {
      status.replaceChildren(
        h("div", { class: "glyph" }, ico(cfg.icon, 22)),
        h("h3", { text: `Chargement de ${cfg.title}…` }),
        h("p", { text: cfg.firstRun }));
    }
  }

  // Position du contrôle natif, en pixels physiques de la fenêtre, et ses trous
  // (menus de l'interface qui le chevauchent : le contrôle natif les cacherait).
  // focus : à l'arrivée sur la section seulement (pas à la fermeture d'un menu)
  function sync(focus = false) {
    let next = "hidden";
    let place = null;
    if (active) {
      const r = host.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const holes = holesIn(r);
      if (holes) {
        const k = window.devicePixelRatio || 1;
        const x = Math.floor(r.left * k), y = Math.floor(r.top * k);
        place = [x, y, Math.ceil(r.right * k) - x, Math.ceil(r.bottom * k) - y, holes];
        next = JSON.stringify(place);
      }
    }
    if (next === lastSent) return;
    lastSent = next;
    if (!place) api.pane_hide(cfg.id);
    else api.pane_show(cfg.id, ...place.slice(0, 4), focus === true, place[4]);
  }

  /** Zones du site recouvertes par un menu, un panneau ou une infobulle de l'interface,
   *  en pixels physiques relatifs au site : le contrôle natif y est percé (le reste
   *  du site reste affiché). null : une modale ou un écran entier le recouvre, il est masqué.
   *  (Écran partagé : un menu de l'autre volet ne le touche pas.) */
  function holesIn(r) {
    const k = window.devicePixelRatio || 1;
    const holes = [];
    for (const o of document.querySelectorAll(`body > .menu, body > .popover, body > .tooltip,
        body > .modal-backdrop, body > .split-drop, body > .lightbox`)) {
      const b = o.getBoundingClientRect();
      if (!(b.left < r.right && b.right > r.left && b.top < r.bottom && b.bottom > r.top)) continue;
      if (!o.matches(".menu, .popover, .tooltip")) return null;
      // Marge : l'ombre portée du menu reste visible
      const x0 = Math.max(b.left - HOLE_MARGIN, r.left), y0 = Math.max(b.top - HOLE_MARGIN, r.top);
      const x1 = Math.min(b.right + HOLE_MARGIN, r.right), y1 = Math.min(b.bottom + HOLE_MARGIN, r.bottom);
      holes.push([Math.floor((x0 - r.left) * k), Math.floor((y0 - r.top) * k),
        Math.ceil((x1 - x0) * k), Math.ceil((y1 - y0) * k)]);
    }
    return holes;
  }

  new ResizeObserver(() => sync()).observe(host);
  window.addEventListener("resize", () => sync());
  on("layout", () => sync());     // volet déplacé sans changer de taille (inversion…)
  // Menu, infobulle ou modale ouverts ou fermés (ajoutés à la page, déjà placés)
  new MutationObserver(() => { if (active) sync(); }).observe(document.body, { childList: true });

  on(`${cfg.id}:state`, (data) => { Object.assign(site, data); render(); });
  on(`${cfg.id}:unread`, ({ count }) => { site.unread = count; render(); });
  on(`${cfg.id}:context`, (context) => assist.setContext(context));
  render();

  return {
    el,
    native: true,       // garde le clavier dans son propre contrôle (voir layout.focusView)
    onShow() { active = true; sync(); },
    onHide() { active = false; sync(); },
    focus() { lastSent = ""; sync(true); },
    // Menu du tray : résumer les e-mails non lus ou du jour
    reveal(target) { if (target?.action === "digest") assist.requestDigest(target.scope); },
  };
}

// ─────────────────────────────────────────────
//  Assistant de réponse (Ollama, en local). Rien n'est envoyé : le texte
//  choisi est placé dans la zone de saisie du site.
// ─────────────────────────────────────────────
const DIGEST_RUNNING = new Set(["listing", "reading", "overview"]);
const DIGEST_MAX = 50;          // e-mails résumés au plus (gmail.py)
const CONVO_MIN_H = 80;         // hauteur minimale de la conversation redimensionnée (px)
const SITE_SHARE = 0.4;         // le site et la liste des e-mails gardent cette part de la section (voir .pane-body)
const QA_RUNNING = new Set(["reading", "thinking"]);
const CONVO_TURNS = 60;         // messages gardés dans la conversation (les plus anciens partent)

/** Barre de l'assistant sous le site : boutons, zone de saisie (question ou consigne,
 *  modèle, lecture à voix haute) et au-dessus la conversation — chaque demande (bulle
 *  à droite) et sa réponse (propositions, brouillon amélioré, résumé, réponse à une question). */
function createAssistBar(ctx, state, cfg) {
  const { api, on, store } = ctx;
  const S = { models: state.chat.models || [], fallback: state.chat.default_model || "", ready: false, busy: false,
    digest: state.panes?.[cfg.id]?.digest || { state: "idle" },
    // Ce qui s'applique à la page du site (webpane.WATCH_JS) : e-mail ou discussion
    // ouvert (reply), brouillon écrit (improve) — les autres boutons sont masqués
    context: state.panes?.[cfg.id]?.context || { reply: false, improve: false },
    qa: state.panes?.[cfg.id]?.qa || { state: "idle" },
    // Liste affichée dans Gmail (événements <site>:list) : nombres des boutons « Résumer »
    mail: state.panes?.[cfg.id]?.mail || { rows: [], view: "" } };
  let listShown = () => false;   // liste des e-mails affichée à côté du site (maillist.js)
  let digestTurn = null;         // réponse qui suit le résumé en cours (événements <site>:digest)
  let qaTurn = null;             // réponse qui suit la question en cours (événements <site>:qa)
  // Lectures à voix haute de la conversation : toutes commencent par ce préfixe
  const prefix = `pane:${cfg.id}:`;
  const READ_ALL = `${prefix}all`;
  let turnCount = 0;
  let readingMode = "answers";   // ce que suit la lecture de toute la conversation
  let readingLive = false;       // … commencée pendant une génération (elle attend la suite)

  const convo = h("div", { class: "pane-convo", role: "log", "aria-label": "Conversation avec l'assistant", hidden: true });
  // Actions principales : en tête de la rangée des suggestions
  const suggestBtn = h("button", { class: "pane-chip pc-main", type: "button", onClick: () => suggest() },
    ico("sparkles", 14), "Suggérer une réponse");
  const improveBtn = h("button", { class: "pane-chip pc-main", type: "button", onClick: () => improve() },
    ico("pencil-line", 14), "Améliorer le brouillon");
  // Résumer, sans menu : un e-mail ouvert, lui seul ; sinon un bouton par choix sur
  // la liste affichée dans Gmail — les non-lus, ceux du jour, tous, la sélection (s'il
  // y en a une, en dernier : les autres ne bougent pas)
  const digestBtns = cfg.digest ? Object.fromEntries([["current", "scroll-text"], ["unread", "mail"],
    ["today", "calendar-clock"], ["all", "scroll-text"], ["selection", "text-select"]].map(([scope, icon]) => [scope, h("button",
      { class: "pane-chip pc-main", type: "button", onClick: () => startDigest(scope, null, scopeCount(scope)) },
      ico(icon, 14), h("span"))])) : {};
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle de l'assistant",
    dataset: { tip: "Modèle de l'assistant" }, onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget) },
    ico("sparkles", 14), modelName, ico("chevron-down", 14));
  const clearBtn = iconButton("eraser", "Effacer la conversation", clearConvo);
  const foldBtn = iconButton("chevron-down", "Masquer la conversation", () => setConvoShown(convo.hidden),
    { cls: "pane-fold" });
  // Zone de saisie : la question (Gmail) ou la consigne des boutons ; Entrée pose la
  // question, ou (WhatsApp) suggère une réponse — améliore le brouillon s'il n'y a que lui
  const composer = createComposer(ctx, {
    label: cfg.ask ? "Question ou consigne" : "Consigne", placeholder: cfg.hint,
    maxlength: cfg.ask ? 1000 : 500, maxHeight: 120,
    tools: [iconButton("mic", "Dictée vocale", () => { input.focus(); api.dictate(); }, { kbd: "Win+H" }), modelBtn],
    extra: [foldBtn, clearBtn],
    speakKey: `${cfg.id}_speak`,
    // Activée pendant une réponse ou un résumé : il est lu tout de suite
    onAutoRead: (on) => {
      if (!on) return;
      if (qaTurn && QA_RUNNING.has(S.qa.state)) readQA(qaTurn);
      else if (digestTurn && DIGEST_RUNNING.has(S.digest.state)) {
        digestTurn.overviewLast = true;
        ctx.speak(digestSpeech(S.digest, true), digestTurn.source, false, true);
      }
    },
    read: { source: READ_ALL, match: (source) => source.startsWith(prefix), speak: readAll },
    hasContent: (text) => !cfg.ask || !!text.trim(),      // consigne facultative
    onSend: submit,
    onStop: () => {
      if (QA_RUNNING.has(S.qa.state)) api.pane_ask_stop(cfg.id);
      if (DIGEST_RUNNING.has(S.digest.state)) api.pane_digest_stop(cfg.id);
    },
    stopTip: "Arrêter",
  });
  const { input } = composer;
  // Poignée de la conversation (bord haut) : sa hauteur se règle à la souris
  const grip = h("div", { class: "pane-grip", role: "separator", tabindex: "0",
    "aria-orientation": "horizontal", "aria-label": "Hauteur de la conversation",
    dataset: { tip: "Glisser pour changer la hauteur de la conversation — double-clic : hauteur automatique" } });
  // Demandes toutes prêtes, selon ce qui est ouvert dans le site (toujours affichées)
  const suggestRow = h("div", { class: "pane-suggest", role: "group", "aria-label": "Suggestions" });
  let suggestKey = null;
  const el = h("div", { class: "pane-assist", dataset: { ollama: "" } }, grip, convo, suggestRow, composer.el);

  function model() {
    const wanted = store.settings.assist_model;
    if (wanted && S.models.includes(wanted)) return wanted;
    if (S.fallback && S.models.includes(S.fallback)) return S.fallback;
    return S.models[0] || "";
  }

  const idle = () => !S.busy && !DIGEST_RUNNING.has(S.digest.state) && !QA_RUNNING.has(S.qa.state);

  function refresh() {
    modelName.textContent = model() || "Aucun modèle";
    const off = !idle() || !S.ready || !model();
    for (const btn of [suggestBtn, improveBtn, ...Object.values(digestBtns)]) btn.disabled = off;
    // Seulement ce qui s'applique : « Suggérer » avec un e-mail (une discussion)
    // ouvert, « Améliorer » avec un brouillon écrit
    const { reply, improve: draft } = S.context;
    suggestBtn.hidden = !reply;
    improveBtn.hidden = !draft;
    // Rien à faire (WhatsApp sans discussion ouverte) : le champ le dit
    const any = reply || draft || !!cfg.ask;
    input.disabled = !any;
    composer.setPlaceholder(any ? cfg.hint : cfg.idle);
    composer.update({ busy: !idle(), ready: any && S.ready && !!model(),
      // Arrêt : question ou résumé ; une proposition ou un brouillon se finit
      canStop: QA_RUNNING.has(S.qa.state) || DIGEST_RUNNING.has(S.digest.state),
      sendTip: cfg.ask ? "Poser la question" : reply ? "Suggérer une réponse" : "Améliorer le brouillon" });
    if (cfg.digest) refreshDigestButtons(reply);
    renderSuggestions();
  }

  /** Entrée ou bouton d'envoi : la question (Gmail) ; sinon une réponse à la discussion
   *  ouverte, ou le brouillon amélioré, avec la consigne du champ. */
  function submit() {
    if (cfg.ask) ask();
    else if (S.context.reply) suggest();
    else if (S.context.improve) improve();
  }

  /** E-mails de la liste relevée dans Gmail que résumerait ce choix. */
  function scopeCount(scope) {
    const rows = S.mail.rows || [];
    if (scope === "unread") return rows.filter((r) => r.unread).length;
    if (scope === "selection") return rows.filter((r) => r.selected).length;
    if (scope === "today") return rows.filter((r) => r.today).length;
    return scope === "all" ? Math.min(rows.length, DIGEST_MAX) : 0;
  }

  /** Boutons « Résumer » : un choix sans e-mail est masqué (aucune liste relevée
   *  encore : non lus et tous, de la boîte de réception). */
  function refreshDigestButtons(reply) {
    const known = !!S.mail.view;
    const all = (S.mail.rows || []).length;
    const unread = scopeCount("unread");
    const selected = scopeCount("selection");
    const today = scopeCount("today");
    const where = S.mail.view ? `de « ${S.mail.view} »` : "de la liste affichée dans Gmail";
    const set = (scope, shown, label, tip) => {
      const btn = digestBtns[scope];
      btn.hidden = !shown;
      btn.lastChild.textContent = label;
      btn.dataset.tip = tip;
    };
    set("current", reply, "Résumer cet e-mail", "Résume l'e-mail ouvert (tout le fil) et ce qu'il attend de vous");
    set("selection", !reply && selected > 0,
      selected > 1 ? `Résumer les ${selected} sélectionnés` : "Résumer l'e-mail sélectionné",
      "Lit les e-mails cochés dans Gmail, sans les marquer comme lus, et fait le point");
    set("unread", !reply && (!known || unread > 0),
      unread > 1 ? `Résumer les ${unread} non lus` : unread ? "Résumer l'e-mail non lu" : "Résumer les non lus",
      `Lit les e-mails non lus ${where}, sans les marquer comme lus, et fait le point`);
    set("today", !reply && (!known || today > 0),
      today > 1 ? `Résumer les ${today} e-mails d'aujourd'hui` : today ? "Résumer l'e-mail d'aujourd'hui"
        : "Résumer les e-mails d'aujourd'hui",
      `Lit les e-mails reçus aujourd'hui ${where}, sans les marquer comme lus, et fait le point`);
    set("all", !reply && (!known || all > 0),
      all > DIGEST_MAX ? `Résumer les ${DIGEST_MAX} premiers` : "Résumer tous les e-mails",
      `Lit tous les e-mails ${where}${all > DIGEST_MAX ? ` (les ${DIGEST_MAX} premiers)` : ""}, `
        + "sans les marquer comme lus, et fait le point");
  }

  // ── Suggestions ──
  /** Brouillon écrit : le retoucher ; e-mail (discussion) ouvert : y répondre ;
   *  sinon (Gmail) : des questions sur les e-mails. */
  function suggestions() {
    const { reply, improve: draft } = S.context;
    const chip = (icon, label, tip, onRun) => ({ icon, label, tip, onRun });
    if (draft) return [
      chip("type", "Corriger les fautes", "Corrige l'orthographe et la grammaire du brouillon, sans changer le style",
        () => improve("corrige seulement l'orthographe, la grammaire et la ponctuation, sans changer le style ni le sens")),
      chip("pencil-line", "Plus formel", "Réécrit le brouillon sur un ton plus formel", () => improve("plus formel")),
      chip("sparkles", "Plus chaleureux", "Réécrit le brouillon sur un ton plus chaleureux", () => improve("plus chaleureux")),
      chip("scissors", "Plus court", "Raccourcit le brouillon en gardant l'essentiel", () => improve("plus court, garde l'essentiel")),
    ];
    if (reply) return [
      cfg.ask ? chip("lightbulb", "Qu'attend-on de moi ?", "Ce que demande l'e-mail ouvert, et pour quand",
        () => ask("Que me demande l'e-mail ouvert, et pour quand ? Réponds en quelques lignes.")) : null,
      chip("check", "Accepter", "Propose des réponses qui acceptent", () => suggest("accepter, répondre positivement")),
      chip("x", "Décliner poliment", "Propose des réponses qui déclinent poliment", () => suggest("décliner poliment")),
      chip("search", "Demander des précisions", "Propose des réponses qui demandent des précisions",
        () => suggest("demander des précisions sur ce qui n'est pas clair")),
    ].filter(Boolean);
    if (!cfg.ask) return [];
    return [
      chip("flag", "Qu'est-ce qui est urgent ?", "Les e-mails urgents ou importants, et pourquoi",
        () => ask("Quels e-mails sont urgents ou importants ? Une ligne par e-mail, avec la raison.")),
      chip("list-checks", "Ce que je dois faire", "Les actions demandées dans vos e-mails, par priorité",
        () => ask("Quelles actions dois-je faire d'après mes e-mails ? Classe-les par priorité, avec les dates limites.")),
      chip("message-square", "Qui attend une réponse ?", "Les e-mails qui attendent une réponse de votre part",
        () => ask("Quels e-mails attendent une réponse de ma part ?")),
      chip("calendar-clock", "Rendez-vous et dates", "Les rendez-vous, réunions et échéances cités dans vos e-mails",
        () => ask("Quels rendez-vous, réunions ou dates à retenir trouve-t-on dans mes e-mails ?")),
      chip("file-text", "Factures à payer", "Les factures et paiements en attente, montants et dates limites",
        () => ask("Y a-t-il des factures ou des paiements à régler ? Donne les montants et les dates limites.")),
    ];
  }

  /** Rangée des actions et suggestions, au-dessus de la zone de saisie. */
  function renderSuggestions() {
    const { reply, improve: draft } = S.context;
    const key = `${reply}|${draft}`;
    if (key !== suggestKey) {
      suggestKey = key;
      suggestRow.replaceChildren(suggestBtn, improveBtn, ...Object.values(digestBtns),
        ...suggestions().map((c) => h("button", { class: "pane-chip", type: "button",
          dataset: { tip: c.tip }, onClick: c.onRun }, ico(c.icon, 13), c.label)));
    }
    suggestRow.hidden = ![...suggestRow.children].some((b) => !b.hidden);
    const off = !idle() || !S.ready || !model();
    for (const b of suggestRow.children) b.disabled = off;
  }

  function modelItems() {
    const current = model();
    return [
      { section: "Modèle de l'assistant" },
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === current,
            onSelect: () => ctx.saveSetting("assist_model", m).then(refresh) }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  // ── Conversation ──
  let atBottom = true;           // suivre la fin tant que l'utilisateur n'est pas remonté
  convo.addEventListener("scroll", () => {
    atBottom = convo.scrollHeight - convo.scrollTop - convo.clientHeight < 24;
  });
  const toBottom = () => { convo.scrollTop = convo.scrollHeight; atBottom = true; };
  const follow = () => { if (atBottom) toBottom(); };

  function setConvoShown(shown) {
    convo.hidden = !shown || !convo.childElementCount;
    const has = convo.childElementCount > 0;
    clearBtn.hidden = foldBtn.hidden = !has;
    renderSuggestions();
    foldBtn.classList.toggle("folded", convo.hidden);
    const tip = convo.hidden ? "Afficher la conversation" : "Masquer la conversation";
    foldBtn.dataset.tip = tip;
    foldBtn.setAttribute("aria-label", tip);
    sizeConvo();
    if (!convo.hidden) toBottom();
  }

  /** Demande de l'utilisateur (bulle à droite) ; consigne en dessous. */
  function userTurn(text, note = "") {
    const bubble = h("div", { class: "msg msg-user pc-user" },
      h("div", { class: "bubble" }, text, note ? h("span", { class: "pc-note", text: note }) : null));
    bubble.turn = { ask: note ? `${text}. ${note}` : text };     // lecture de toute la conversation
    convo.append(bubble);
    trim();
  }

  /** Réponse de l'assistant ; set(...) remplace son contenu (travail en cours, résultat),
   *  say(texte) donne son texte lu (avec toute la conversation) ; source : sa lecture. */
  function botTurn(name = model()) {
    const body = h("div", { class: "pc-body" });
    const turn = {
      body, source: `${prefix}${++turnCount}`, answer: "",
      set(...children) { body.replaceChildren(...children.flat(Infinity).filter(Boolean)); follow(); },
      say(text) { turn.answer = text || ""; feedAll(); },
    };
    const bubble = h("div", { class: "msg msg-bot pc-bot" },
      h("div", { class: "msg-meta" }, h("span", { class: "avatar" }, ico("sparkles", 12)),
        h("span", { class: "msg-model", text: name || "Assistant" })),
      body);
    bubble.turn = turn;
    convo.append(bubble);
    trim();
    setConvoShown(true);
    return turn;
  }

  // ── Lecture à voix haute de la conversation ──
  /** Texte lu de toute la conversation (bouton Lire de la zone de saisie). */
  const convoSpeech = (mode) => conversationSpeech([...convo.children].map((c) => c.turn).filter(Boolean), mode);

  function readAll(mode) {
    readingMode = mode;
    readingLive = !idle();         // une réponse s'écrit : la lecture attend la suite
    ctx.speak(convoSpeech(mode), READ_ALL, true, readingLive);
  }

  /** Suite de la lecture de toute la conversation (complète quand plus rien ne s'écrit). */
  function feedAll() {
    if (!ctx.feeding(READ_ALL)) return;
    const final = idle();
    ctx.feed(READ_ALL, convoSpeech(readingMode), final);
    if (final) readingLive = false;
  }

  /** Lecture auto d'une réponse arrivée d'un bloc (propositions, brouillon) — sauf si
   *  la lecture de toute la conversation, commencée avant, va la lire. */
  function autoSpeak(turn, text) {
    if (!composer.autoRead() || !text || (readingLive && ctx.speaking(READ_ALL))) return;
    ctx.speak(text, turn.source);
  }

  /** Bouton « Lire » sous une réponse arrivée d'un bloc. */
  function speakButton(turn, text, label) {
    const btn = iconButton("volume-2", label, () => ctx.toggleSpeak(text, turn.source), { size: 15, cls: "sm" });
    btn.dataset.speakSource = turn.source;
    btn.dataset.tipIdle = label;
    return btn;
  }

  function trim() {
    while (convo.childElementCount > CONVO_TURNS) convo.firstElementChild.remove();
  }

  function clearConvo() {
    if (DIGEST_RUNNING.has(S.digest.state)) api.pane_digest_stop(cfg.id);
    if (QA_RUNNING.has(S.qa.state)) api.pane_ask_stop(cfg.id);
    if (cfg.ask) api.pane_ask_reset(cfg.id);       // la question suivante repart de zéro
    // Sa lecture à voix haute s'arrête avec elle (une lecture au fil de l'eau attendrait la suite)
    if (store.tts.speaking && (store.tts.source || "").startsWith(prefix)) api.tts_stop();
    digestTurn = qaTurn = null;
    convo.replaceChildren();
    setConvoShown(false);
  }

  const busyStatus = (text) => h("div", { class: "pane-status" }, h("span", { class: "pane-busy" }), text);
  const errorStatus = (text) => h("div", { class: "pane-status error" }, ico("triangle-alert", 14), text);
  const doneStatus = (text) => h("div", { class: "pane-status" }, ico("check", 14), text);

  /** Consigne du champ, utilisée par un bouton : affichée sous la demande, puis effacée. */
  function takeHint() {
    const text = input.value.trim();
    composer.setValue("");
    return text;
  }

  // ── Hauteur de la conversation : réglage digest_height (px, 0 = automatique) ──
  function sizeConvo() {
    const shown = !convo.hidden;
    const px = shown ? Number(store.settings.digest_height) || 0 : 0;
    el.classList.toggle("resizable", shown);
    convo.classList.toggle("sized", px > 0);
    if (px > 0) convo.style.setProperty("--digest-h", `${px}px`);
  }

  /** Plus grande hauteur possible : le site (et la liste des e-mails) gardent SITE_SHARE de la section. */
  function maxConvoHeight() {
    const section = el.parentElement;
    const host = section?.querySelector(".pane-body, .pane-host");
    const now = convo.getBoundingClientRect().height;
    const keep = (section?.clientHeight || 0) * SITE_SHARE;
    return Math.max(CONVO_MIN_H, now + (host ? host.getBoundingClientRect().height - keep : 0));
  }

  function setConvoHeight(px, save) {
    store.settings.digest_height = px ? Math.round(Math.max(CONVO_MIN_H, Math.min(px, maxConvoHeight()))) : 0;
    sizeConvo();
    if (save) ctx.saveSetting("digest_height", store.settings.digest_height);
  }

  grip.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    // Capture : le glisser continue au-dessus du site (contrôle natif)
    try { grip.setPointerCapture(ev.pointerId); } catch { /* pointeur déjà relâché */ }
    grip.classList.add("dragging");
    document.body.classList.add("pane-resizing");
    const startY = ev.clientY;
    const startH = convo.getBoundingClientRect().height;
    const max = maxConvoHeight();
    const before = store.settings.digest_height;
    let done = false;
    const move = (e) => {
      if (!(e.buttons & 1)) { end(); return; }    // relâché hors de la page
      store.settings.digest_height = Math.round(Math.max(CONVO_MIN_H, Math.min(max, startH + startY - e.clientY)));
      sizeConvo();
    };
    const end = () => {
      if (done) return;
      done = true;
      grip.removeEventListener("pointermove", move);
      grip.classList.remove("dragging");
      document.body.classList.remove("pane-resizing");
      if (store.settings.digest_height !== before) ctx.saveSetting("digest_height", store.settings.digest_height);
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", end, { once: true });
    grip.addEventListener("lostpointercapture", end, { once: true });
  });
  grip.addEventListener("dblclick", () => setConvoHeight(0, true));
  grip.addEventListener("keydown", (ev) => {
    const step = { ArrowUp: 40, ArrowDown: -40 }[ev.key];
    if (!step) return;
    ev.preventDefault();
    setConvoHeight(convo.getBoundingClientRect().height + step, true);
  });

  // ── Propositions de réponse, brouillon amélioré ──
  /** Remet le texte remplacé dans la zone de saisie. */
  function restoreButton(previous, label = "Rétablir l'original") {
    const btn = h("button", { class: "btn sm", type: "button", onClick: async () => {
      const done = await api.pane_insert(cfg.id, previous);
      if (done?.ok) btn.replaceWith(doneStatus("Rétabli."));
      else toast(done?.error || "Restauration impossible.", "error");
    } }, ico("undo-2", 14), label);
    return btn;
  }

  /** Demande (bulle) puis réponse en attente ; le résultat ou l'erreur la remplace. */
  /** note : consigne d'une suggestion, sinon celle du champ. */
  async function run(request, label, call, note = takeHint()) {
    if (!idle() || !S.ready) return null;
    const m = model();
    userTurn(request, note ? `Consigne : ${note}` : "");
    const turn = botTurn(m);
    turn.set(busyStatus(`${label}…`));
    S.busy = true;
    refresh();
    let res = null;
    try {
      res = await call(cfg.id, m, note);
    } finally {
      S.busy = false;
      refresh();
    }
    if (!res?.ok) {
      turn.set(errorStatus(res?.error || "Échec de la génération."));
      turn.say("");     // la lecture de toute la conversation n'attend plus rien
      return null;
    }
    return { res, turn };
  }

  async function suggest(note) {
    const out = await run("Suggérer une réponse", "Rédaction de propositions", api.pane_suggest, note);
    if (!out) return;
    const { res, turn } = out;
    const undo = h("div", { class: "pane-undo" });
    // Lues l'une après l'autre (Lecture auto, bouton Lire)
    const speech = res.replies.map((text, i) => `Proposition ${i + 1} : ${text}`).join("\n\n");
    // E-mails : aperçu compact (sans lignes vides) ; le texte inséré reste complet
    const chips = res.replies.map((text, i) => h("button", { class: "pane-reply", type: "button",
      text: cfg.long ? text.replace(/\n\s*\n/g, "\n") : text,
      dataset: { tip: "Placer cette réponse dans la zone de saisie (rien n'est envoyé)" },
      onContextmenu: (ev) => {
        ev.preventDefault();
        const source = `${turn.source}:${i}`;
        openMenu([
          { label: ctx.speaking(source) ? "Arrêter la lecture" : "Lire", icon: "volume-2",
            onSelect: () => ctx.toggleSpeak(text, source) },
          { label: "Copier", icon: "copy", onSelect: () => ctx.copy(text) },
        ], ev.clientX, ev.clientY);
      },
      onClick: async (ev) => {
        const chip = ev.currentTarget;
        const done = await api.pane_insert(cfg.id, text);
        if (!done?.ok) { toast(done?.error || "Insertion impossible.", "error"); return; }
        chips.forEach((c) => c.classList.toggle("used", c === chip));
        // Un brouillon déjà écrit a été remplacé : on permet de le récupérer
        undo.replaceChildren();
        if (done.previous && done.previous !== text) undo.append(restoreButton(done.previous, "Rétablir mon brouillon"));
      } }));
    turn.set(h("div", { class: "pane-for", text: res.title ? `Pour « ${res.title} » — cliquez sur une proposition pour la placer dans la réponse :`
        : "Cliquez sur une proposition pour la placer dans la zone de saisie :" }),
      h("div", { class: `pc-replies${cfg.long ? " long" : ""}` }, chips), undo,
      h("div", { class: "msg-actions" }, speakButton(turn, speech, "Lire les propositions")));
    turn.say(speech);
    autoSpeak(turn, speech);
  }

  async function improve(note) {
    const out = await run("Améliorer le brouillon", "Amélioration du brouillon", api.pane_improve, note);
    if (!out) return;
    const { res, turn } = out;
    turn.set(doneStatus(`Brouillon amélioré dans ${cfg.title}.`),
      h("div", { class: "pane-undo" }, restoreButton(res.draft)),
      res.text ? h("div", { class: "msg-actions" }, speakButton(turn, res.text, "Lire le brouillon amélioré")) : null);
    turn.say(res.text);
    autoSpeak(turn, res.text);
  }

  // ── Résumé des e-mails de la liste affichée dans Gmail (en arrière-plan,
  //    événements <site>:digest) : l'e-mail ouvert, sélectionnés, non lus ou tous ──
  /** La demande telle qu'elle apparaît dans la conversation. */
  function digestRequest(scope, n) {
    if (scope === "current") return "Résumer cet e-mail";
    if (scope === "selection") return n > 1 ? `Résumer les ${n} e-mails sélectionnés` : "Résumer l'e-mail sélectionné";
    if (scope === "unread") return n === 1 ? "Résumer l'e-mail non lu" : n ? `Résumer les ${n} e-mails non lus` : "Résumer les e-mails non lus";
    if (scope === "today") return n === 1 ? "Résumer l'e-mail d'aujourd'hui" : n ? `Résumer les ${n} e-mails d'aujourd'hui` : "Résumer les e-mails d'aujourd'hui";
    return n === 1 ? "Résumer l'e-mail" : n ? `Résumer les ${n} e-mails` : "Résumer les e-mails";
  }

  /** Menu du tray (section affichée par main.js) : lancé tout de suite, ou dès que
   *  Gmail est chargé ; un résumé en cours n'est pas interrompu. */
  let pendingDigest = null;
  function requestDigest(scope) {
    pendingDigest = null;
    if (!(scope in digestBtns) || scope === "current" || scope === "selection") return;
    if (!S.ready) { pendingDigest = scope; return; }
    if (!idle()) { toast("Un résumé ou une réponse est déjà en cours.", "error"); return; }
    if (!model()) { toast("Aucun modèle Ollama installé.", "error"); return; }
    startDigest(scope, null, scopeCount(scope));
  }

  async function startDigest(scope, ids = null, count = 0) {
    if (!idle() || !S.ready) return;
    const note = takeHint();
    userTurn(digestRequest(scope, ids ? ids.length : count), note ? `Consigne : ${note}` : "");
    const turn = digestTurn = botTurn();
    turn.set(busyStatus("Lecture des e-mails…"));
    const res = await api.pane_digest(cfg.id, model(), note, scope, ids);
    if (!res?.ok) {
      turn.set(errorStatus(res?.error || "Résumé impossible."));
      turn.say("");
      if (digestTurn === turn) digestTurn = null;
      return;
    }
    // Lecture auto : chaque résumé dès qu'il est prêt, la synthèse à la fin (la suite
    // arrive par les événements <site>:digest)
    if (autoOn()) {
      turn.overviewLast = true;
      ctx.speak("", turn.source, false, true);
    }
    renderDigest();
  }

  function digestCount(d) {
    if (!d.total || d.scope === "current") return "";
    if (d.scope === "selection") return plural(d.total, "e-mail sélectionné", "e-mails sélectionnés");
    if (d.scope === "unread") return plural(d.total, "non lu", "non lus");
    if (d.scope === "today") return plural(d.total, "e-mail aujourd'hui", "e-mails aujourd'hui");
    return d.listed > d.total ? `${d.total} plus récents sur ${d.listed.toLocaleString("fr-FR")}`
      : plural(d.total, "e-mail", "e-mails");
  }

  function digestEmpty(d) {
    const where = `dans « ${d.view || "Gmail"} »`;
    if (d.scope === "selection") return "Aucun e-mail sélectionné dans Gmail.";
    if (d.scope === "today") return `Aucun e-mail reçu aujourd'hui ${where}.`;
    return d.scope === "unread" ? `Aucun e-mail non lu ${where}.` : `Aucun e-mail ${where}.`;
  }

  function digestProgress(d) {
    if (d.scope === "current") return `Lecture et résumé de l'e-mail…`;
    if (d.state === "listing") return "Lecture de la boîte de réception…";
    if (d.state === "overview") return "Synthèse…";
    return `Résumé ${Math.min(d.items.length + 1, d.total)} sur ${d.total}…`;
  }

  function renderDigest() {
    const turn = digestTurn;
    if (!turn) return;
    const d = S.digest;
    turn.digest = d;          // ce que lit son bouton, une fois un autre résumé lancé
    const items = d.items || [];
    const running = DIGEST_RUNNING.has(d.state);
    // Plusieurs e-mails, liste affichée à côté : leurs résumés y sont, ici la synthèse seule
    const inList = listShown() && d.scope !== "current" && (d.total || 0) > 1;
    const text = () => digestText(d);
    const count = digestCount(d);
    turn.set(
      d.view ? h("div", { class: "digest-head" },
        h("span", { class: "digest-title" }, ico("scroll-text", 14), d.view),
        count ? h("span", { class: "digest-count", text: count }) : null,
        inList && items.length && !running ? h("span", { class: "digest-count", text: "· résumés dans la liste à gauche" }) : null)
        : null,
      running ? busyStatus(digestProgress(d)) : null,
      d.state === "cancelled" ? h("div", { class: "pane-status", text: "Arrêté." }) : null,
      d.state === "error" ? errorStatus(d.error || "Échec du résumé.") : null,
      d.overview ? h("p", { class: "digest-overview", text: d.overview }) : null,
      d.state === "done" && !items.length ? h("p", { class: "digest-empty", text: digestEmpty(d) }) : null,
      items.length && !inList ? h("div", { class: "digest-list" }, items.map(digestItem)) : null,
      // « Lire » dès le premier résumé : la lecture suit les suivants
      items.length || running ? h("div", { class: "msg-actions" },
        digestSpeakButton(turn),
        running ? h("button", { class: "btn sm", type: "button", onClick: () => api.pane_digest_stop(cfg.id) },
          ico("square", 12), "Arrêter") : [
          iconButton("copy", "Copier le résumé", () => ctx.copy(text()), { size: 15, cls: "sm" }),
          iconButton("notebook-pen", "Ajouter à une note", (ev) => ctx.pickDestination("notes", ev.currentTarget,
            (slot) => ctx.emit("note:insert", { text: text(), slot })), { size: 15, cls: "sm" }),
        ]) : null);
    // Avec toute la conversation, la synthèse est lue à la fin (le texte ne fait que s'allonger)
    turn.say(digestSpeech(d, true));
    ctx.emit("speak-sources-changed");
  }

  // ── Question sur les e-mails (Gmail, en arrière-plan : événements <site>:qa) ──
  /** preset : question d'une suggestion (le champ garde son texte), sinon celle du champ. */
  async function ask(preset) {
    const question = preset || input.value.trim();
    if (!question) { input.focus(); return; }
    if (!idle() || !S.ready) return;
    if (!preset) composer.setValue("");
    userTurn(question);
    const turn = qaTurn = botTurn();
    turn.set(busyStatus("Lecture de Gmail…"));
    const res = await api.pane_ask(cfg.id, model(), question);
    if (!res?.ok) {
      turn.set(errorStatus(res?.error || "Question impossible."));
      turn.say("");
      if (qaTurn === turn) qaTurn = null;
      return;
    }
    renderQA();
  }

  /** Lecture de la réponse en cours, dès son début (la suite arrive par renderQA). */
  function readQA(turn) {
    turn.autoRead = true;
    ctx.speak(S.qa.answer || "", turn.source, true, true);
  }

  function renderQA() {
    const turn = qaTurn;
    if (!turn) return;
    const q = S.qa;
    const running = QA_RUNNING.has(q.state);
    const { source } = turn;
    const text = q.answer || "";
    // Lecture auto : dès que la réponse commence (Gmail lu)
    if (q.state === "thinking" && !turn.autoRead && autoOn()) readQA(turn);
    ctx.feed(source, text, !running);
    const answer = h("div", { class: "md qa-answer" });
    if (text) answer.innerHTML = renderMarkdown(text);
    let speak = null;
    if (text && !running) {
      speak = iconButton("volume-2", "Lire la réponse", () => ctx.toggleSpeak(text, source, true), { size: 15, cls: "sm" });
      speak.dataset.speakSource = source;
      speak.dataset.tipIdle = "Lire la réponse";
    }
    turn.set(
      running && !text ? busyStatus(q.state === "reading" ? "Lecture de Gmail…" : "Réflexion…") : null,
      text ? answer : null,
      q.state === "stopped" ? h("div", { class: "pane-status", text: "Arrêté." }) : null,
      q.state === "error" ? errorStatus(q.error || "Échec de la réponse.") : null,
      h("div", { class: "msg-actions" }, running
        ? h("button", { class: "btn sm", type: "button", onClick: () => api.pane_ask_stop(cfg.id) }, ico("square", 12), "Arrêter")
        : text ? [speak, iconButton("copy", "Copier la réponse", () => ctx.copy(text), { size: 15, cls: "sm" })] : null));
    if (!running) qaTurn = null;
    turn.say(text);
    ctx.emit("speak-sources-changed");
  }

  if (cfg.ask) {
    on(`${cfg.id}:qa`, (q) => {
      S.qa = q;
      refresh();
      renderQA();
    });
    // Interface rechargée pendant la réponse : elle reprend dans la conversation
    if (QA_RUNNING.has(S.qa.state)) {
      userTurn(S.qa.question || "Question");
      qaTurn = botTurn(S.qa.model);
      renderQA();
    }
  }

  // ── Lecture à voix haute du résumé ──
  /** Lecture auto possible : pas si celle de toute la conversation, commencée pendant
   *  la génération, va lire cette réponse. */
  function autoOn() {
    return composer.autoRead() && !(readingLive && ctx.speaking(READ_ALL));
  }

  /** Bouton Lire d'un résumé ; commencée pendant le travail, la lecture suit les
   *  résumés suivants et lit la synthèse, qui arrive en dernier, à la fin. */
  function digestSpeakButton(turn) {
    const btn = iconButton("volume-2", "Lire le résumé", () => {
      if (ctx.speaking(turn.source)) { api.tts_stop(); return; }
      const running = turn === digestTurn && DIGEST_RUNNING.has(S.digest.state);
      turn.overviewLast = running;
      ctx.speak(digestSpeech(turn.digest, running), turn.source, false, running);
    }, { size: 15, cls: "sm" });
    btn.dataset.speakSource = turn.source;
    btn.dataset.tipIdle = "Lire le résumé";
    return btn;
  }

  /** Un fil, tel qu'il est lu : son résumé suffit, sans expéditeur ni objet. */
  const itemSpeech = (i) => [i.summary, actionSentence(i.action)].filter(Boolean).join("\n");
  /** Un fil, tel qu'il est copié : avec expéditeur et objet. */
  const itemCopy = (i) => `${i.from || "Expéditeur inconnu"}${i.subject ? ` : ${i.subject}` : ""}.\n${itemSpeech(i)}`;

  /** Texte lu : la synthèse puis chaque fil ; overviewLast (lecture commencée pendant
   *  le travail) : il ne fait que s'allonger — chaque fil fini, puis la synthèse à la fin. */
  function digestSpeech(d, overviewLast = false) {
    const items = (d.items || []).map((i) => `${itemSpeech(i)}\n\n`).join("");
    if (!d.overview) return items;
    return overviewLast ? `${items}En bref : ${d.overview}` : `${d.overview}\n\n${items}`;
  }

  /** Un fil : clic pour l'ouvrir dans Gmail (sauf sélection de texte en cours). */
  function digestItem(item) {
    const open = async () => {
      const res = await api.pane_open_thread(cfg.id, item.id);
      if (!res?.ok) toast(res?.error || "Ouverture impossible.", "error");
    };
    // E-mail ouvert : déjà affiché dans Gmail, pas de lien
    const link = S.digest.scope !== "current";
    return h("div", { class: `digest-item${item.unread ? " unread" : ""}${item.failed ? " failed" : ""}${link ? "" : " static"}`,
      role: link ? "button" : null, tabindex: link ? "0" : null,
      onClick: () => { if (link && window.getSelection().isCollapsed) open(); },
      onKeydown: (ev) => { if (link && ev.key === "Enter") open(); },
      onContextmenu: (ev) => {
        ev.preventDefault();
        const source = `${prefix}item:${item.id}`;
        openMenu([
          { label: ctx.speaking(source) ? "Arrêter la lecture" : "Lire ce résumé", icon: "volume-2",
            onSelect: () => ctx.toggleSpeak(itemSpeech(item), source) },
          { label: "Copier", icon: "copy", onSelect: () => ctx.copy(itemCopy(item)) },
          link ? { label: "Ouvrir dans Gmail", icon: "mail", onSelect: open } : null,
        ], ev.clientX, ev.clientY);
      } },
      h("div", { class: "digest-meta" },
        item.unread ? h("span", { class: "digest-dot", "aria-label": "Non lu" }) : null,
        importanceIcon(item.failed ? "" : item.importance),
        h("span", { class: "digest-from", text: item.from || "(expéditeur inconnu)" }),
        h("span", { class: "digest-subject", text: item.subject || "(sans objet)" }),
        h("span", { class: "digest-when", text: item.when, dataset: { tip: item.date || null } })),
      h("div", { class: "digest-summary", text: item.summary,
        dataset: { tip: item.failed ? "Résumé impossible : extrait affiché par Gmail" : null } }),
      item.action ? h("div", { class: "digest-action" }, ico("chevron-right", 12), h("span", { text: item.action })) : null);
  }

  /** Texte à copier ou à mettre dans une note. */
  function digestText(d) {
    const day = new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
    const count = digestCount(d);
    const lines = [`${d.view || "Gmail"} — ${day}${count ? ` (${count})` : ""}`, ""];
    if (d.overview) lines.push(d.overview, "");
    for (const i of d.items) {
      lines.push(`• ${i.unread ? "[non lu] " : ""}${i.from} — ${i.subject || "(sans objet)"}${i.when ? ` (${i.when})` : ""}`);
      if (i.summary) lines.push(`  ${i.summary}`);
      if (i.action) lines.push(`  → ${actionSentence(i.action)}`);
    }
    return lines.join("\n");
  }

  if (cfg.digest) {
    on(`${cfg.id}:list`, (mail) => { S.mail = mail; refresh(); });
    on(`${cfg.id}:digest`, (d) => {
      S.digest = d;
      // Lecture du résumé en cours (son bouton, Lecture auto) : la suite
      if (digestTurn) ctx.feed(digestTurn.source, digestSpeech(d, digestTurn.overviewLast), !DIGEST_RUNNING.has(d.state));
      refresh();
      renderDigest();
    });
    // Interface rechargée pendant le résumé : il reprend dans la conversation
    if (DIGEST_RUNNING.has(S.digest.state)) {
      userTurn(digestRequest(S.digest.scope, S.digest.total));
      digestTurn = botTurn(S.digest.model);
      renderDigest();
    }
  }

  on("chat:models", ({ models, default_model }) => {
    S.models = models || [];
    if (default_model) S.fallback = default_model;
    refresh();
  });
  refresh();
  setConvoShown(false);

  return {
    el,
    setReady(ready) {
      S.ready = ready;
      refresh();
      if (ready && pendingDigest) requestDigest(pendingDigest);
    },
    setContext(context) { S.context = { reply: !!context?.reply, improve: !!context?.improve }; refresh(); },
    // Pour la liste des e-mails (maillist.js)
    setListShown(fn) { listShown = fn; },
    listChanged() { renderDigest(); },
    model,
    hint: () => input.value.trim(),
    canRun: () => idle() && S.ready && !!model(),
    blocked: () => !S.ready ? `${cfg.title} n'est pas chargé.` : !model() ? "Aucun modèle Ollama installé."
      : "Un résumé est déjà en cours.",
    runDigest: (scope, ids) => startDigest(scope, ids),
    requestDigest,
  };
}
