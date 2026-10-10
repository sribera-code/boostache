// Vue Assistant live : tant qu'il est actif, la fenêtre utilisée est capturée à
// intervalle régulier et un modèle Ollama fait dessus la tâche du mode choisi
// (répondre, résumer, expliquer, corriger, consigne libre) — live.py.
// Le résultat de la fenêtre du moment s'affiche en direct ; les précédents en dessous.
import { h, plural } from "../dom.js";
import { ico, iconButton, openLightbox, openMenu, openMenuBelow, toast } from "../ui.js";
import { renderMarkdown } from "../markdown.js";

const INTERVAL_LABELS = { 15: "15 s", 30: "30 s", 60: "1 min", 120: "2 min", 300: "5 min" };
const MAX_ITEMS = 40;          // comme live.MAX_ITEMS
const WORKING = ["capture", "read", "think"];

// plain : texte à copier tel quel (pas du Markdown)
const MODES = {
  reply:   { label: "Répondre",  icon: "message-circle", plain: true, verb: "Rédaction d'une réponse",
             tip: "Propose une réponse au dernier message reçu (messagerie, e-mail…), prête à copier" },
  summary: { label: "Résumer",   icon: "file-text", verb: "Résumé",
             tip: "Les points clés de ce que vous lisez" },
  explain: { label: "Expliquer", icon: "lightbulb", verb: "Explication",
             tip: "Explique l'erreur ou ce qui peut poser question à l'écran, avec les étapes pour s'en sortir" },
  correct: { label: "Corriger",  icon: "pencil-line", plain: true, verb: "Relecture",
             tip: "Corrige le texte que vous êtes en train d'écrire" },
  custom:  { label: "Libre",     icon: "sparkles", verb: "Consigne",
             tip: "Votre propre consigne, appliquée à la fenêtre utilisée" },
  question: { label: "Question", icon: "message-square", verb: "Réponse" },
};

/** "2026-10-07T14:02:05" → "14:02". */
const hhmm = (iso) => (iso || "").slice(11, 16);

/** Secondes restantes → "12 s", "1 min 05 s". */
function countdown(ms) {
  const n = Math.max(0, Math.ceil((ms - Date.now()) / 1000));
  return n < 60 ? `${n} s` : `${Math.floor(n / 60)} min ${String(n % 60).padStart(2, "0")} s`;
}

export function createLiveView(ctx, state) {
  const { api, on, store } = ctx;
  const init = state.live || { available: false };
  const S = {
    available: !!init.available,
    intervals: init.intervals || [15, 30, 60, 120, 300],
    live: init,                // état du service : running, phase, mode, current, note…
    items: init.items || [],
    models: state.chat?.models || [],
    unread: 0,                 // résultats arrivés hors de la vue
    windowVisible: !!state.visible,
  };
  let ticker = null;
  let shownKey = "";           // résultat affiché en direct (évite de le reconstruire)

  // ── Panneau de contrôle ─────────────────────
  const toggleBtn = h("button", { class: "live-btn", type: "button", onClick: toggle });
  const headline = h("div", { class: "live-headline" });
  const detail = h("div", { class: "live-detail" });
  const noteEl = h("div", { class: "live-note", hidden: true });
  const nowBtn = h("button", { class: "btn sm", type: "button", onClick: () => api.live_now(),
    dataset: { tip: "Analyser tout de suite la fenêtre utilisée : nouveau résultat même si elle n'a pas changé" } },
    ico("scan", 14), "Analyser maintenant");

  const modeSeg = h("div", { class: "segmented live-modes", role: "group", "aria-label": "Mode de l'assistant" });
  const goal = h("input", { class: "field live-goal", type: "text", maxlength: "500", spellcheck: "true",
    placeholder: "Votre consigne : traduis en anglais, liste les dates et rendez-vous, propose un titre…",
    "aria-label": "Consigne du mode Libre" });
  goal.value = store.settings.live_goal || "";
  goal.addEventListener("change", async () => { goal.value = await ctx.saveSetting("live_goal", goal.value) ?? goal.value; });
  goal.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); goal.blur(); } });

  const intervalSeg = h("div", { class: "segmented live-interval", role: "group", "aria-label": "Intervalle entre deux captures" });
  const notifyBtn = settingToggle("bell", "Notifications", "live_notify",
    "Notification Windows pour chaque nouveau résultat quand Boostache est masqué");
  const speakBtn = settingToggle("volume-2", "Voix", "live_speak", "Lire chaque nouveau résultat à voix haute");
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle de l'assistant",
    dataset: { tip: "Modèle de l'assistant (de préférence un modèle qui lit les images)" },
    onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget, "right") },
    ico("sparkles", 14), modelName, ico("chevron-down", 14));

  const deck = h("div", { class: "live-deck" },
    h("div", { class: "live-main" }, toggleBtn,
      h("div", { class: "live-status" }, headline, detail, noteEl),
      nowBtn),
    modeSeg,
    goal,
    h("div", { class: "live-options" },
      h("span", { class: "live-label", text: "Toutes les" }), intervalSeg,
      notifyBtn, speakBtn, h("div", { class: "head-spacer" }), modelBtn));

  // ── En direct, puis les résultats précédents ─
  const nowCard = h("div", { class: "live-now" });
  const count = h("span", { class: "view-sub" });
  const clearBtn = h("button", { class: "btn ghost sm", type: "button", onClick: () => api.live_clear() },
    ico("trash-2", 14), "Tout effacer");
  const list = h("div", { class: "live-list" });
  const listHead = h("div", { class: "live-list-head" }, h("span", { class: "live-list-title", text: "Précédemment" }),
    count, h("div", { class: "head-spacer" }), clearBtn);
  const body = h("div", { class: "live-body" }, deck, nowCard, listHead, list);

  // ── Question sur la fenêtre du moment ───────
  const askInput = h("input", { class: "field live-ask-input", type: "text", maxlength: "1000", spellcheck: "true",
    placeholder: "Une question sur la fenêtre que vous utilisez ?", "aria-label": "Question sur la fenêtre utilisée",
    onKeydown: (ev) => { if (ev.key === "Enter" && !ev.isComposing) { ev.preventDefault(); ask(); } } });
  const askBtn = h("button", { class: "btn primary sm", type: "button", onClick: ask, dataset: { kbd: "Entrée" } },
    ico("arrow-up", 14), "Demander");
  const askBar = h("div", { class: "live-ask" }, h("div", { class: "live-ask-inner" }, askInput, askBtn));

  const sub = h("span", { class: "view-sub" });
  const unavailable = h("div", { class: "empty" },
    h("div", { class: "glyph" }, ico("eye", 22)),
    h("h3", { text: "Assistant live indisponible" }),
    h("p", { text: `Il a besoin d'Ollama : ${init.reason || "erreur inconnue"}.` }));
  const el = h("section", {},
    h("header", { class: "view-head" }, h("span", { class: "view-title", text: "Assistant live" }), sub),
    S.available ? body : unavailable,
    S.available ? askBar : null);

  // ── Rendu : état ────────────────────────────
  const intervalLabel = () => INTERVAL_LABELS[store.settings.live_interval] || "30 s";
  const mode = () => MODES[S.live.mode] || MODES.reply;

  function phaseText() {
    const { phase, window: win, asking, model } = S.live;
    if (phase === "capture") return "Capture de la fenêtre utilisée…";
    if (phase === "read") return win ? `Lecture du texte de « ${win} »…` : "Lecture du texte de la fenêtre…";
    return `${asking ? "Réponse" : mode().verb} avec ${model}…`;
  }

  function renderStatus() {
    const L = S.live;
    const ollama = !!store.ollama;
    const working = WORKING.includes(L.phase) && (L.running || L.pending);
    deck.classList.toggle("on", !!L.running);
    toggleBtn.replaceChildren(ico(L.running ? "square" : "eye", L.running ? 18 : 24));
    toggleBtn.dataset.tip = L.running ? "Arrêter l'assistant" : "Activer l'assistant";
    toggleBtn.setAttribute("aria-label", toggleBtn.dataset.tip);
    toggleBtn.disabled = !ollama && !L.running;
    headline.textContent = !ollama ? "Ollama ne répond pas"
      : L.running ? `Assistant actif · ${mode().label}` : "Assistant arrêté";
    let text = "";
    if (!ollama) text = "L'assistant a besoin d'Ollama, en local : il reprendra dès qu'il répondra.";
    else if (working) text = phaseText();
    else if (!L.running) text = `Une fois activé, la fenêtre que vous utilisez est relue toutes les ${intervalLabel()}.`;
    else if (L.phase === "idle") text = "En pause : aucune activité au clavier ni à la souris.";
    else if (L.phase === "wait" && L.next_at) text = `Prochaine capture dans ${countdown(L.next_at)}`;
    detail.replaceChildren(...(working ? [h("span", { class: "pane-busy" })] : []), h("span", { text }));
    noteEl.hidden = !L.note || !ollama;
    noteEl.textContent = L.note || "";
    noteEl.classList.toggle("error", L.note_kind === "error");
    nowBtn.disabled = !ollama || !!L.pending;
    askBtn.disabled = !ollama || !!L.asking;
    askInput.placeholder = L.asking ? `Question en cours : « ${L.asking} »`
      : "Une question sur la fenêtre que vous utilisez ?";
    modelName.textContent = L.model || "Aucun modèle";
    sub.textContent = L.running ? `actif · ${mode().label.toLowerCase()} · toutes les ${intervalLabel()}` : "";
    for (const btn of modeSeg.children) btn.classList.toggle("active", btn.dataset.mode === L.mode);
    goal.hidden = L.mode !== "custom";
    renderBadge();
    syncTicker();
  }

  function renderModes() {
    modeSeg.replaceChildren(...Object.entries(MODES).filter(([id]) => id !== "question").map(([id, m]) =>
      h("button", { type: "button", dataset: { mode: id, tip: m.tip }, onClick: () => setMode(id) },
        ico(m.icon, 14), m.label)));
  }

  function renderIntervals() {
    const current = store.settings.live_interval;
    intervalSeg.replaceChildren(...S.intervals.map((s) => h("button", {
      type: "button", class: s === current ? "active" : "", text: INTERVAL_LABELS[s] || `${s} s`,
      onClick: async () => { await ctx.saveSetting("live_interval", s); renderIntervals(); renderStatus(); },
    })));
  }

  function settingToggle(iconName, label, key, tip) {
    const btn = h("button", { class: "btn sm live-toggle", type: "button", role: "switch", dataset: { tip } },
      ico(iconName, 14), label);
    const sync = () => {
      btn.classList.toggle("on", !!store.settings[key]);
      btn.setAttribute("aria-checked", String(!!store.settings[key]));
    };
    btn.addEventListener("click", async () => { await ctx.saveSetting(key, !store.settings[key]); sync(); });
    sync();
    return btn;
  }

  function modelItems() {
    const current = S.live.model;
    return [
      { section: "Modèle de l'assistant" },
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === current, onSelect: () => ctx.saveSetting("live_model", m) }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  // ── Rendu : résultats ───────────────────────
  /** Contenu d'un résultat : message auquel il répond, texte (brut ou Markdown), corrections. */
  function resultContent(item) {
    const m = MODES[item.mode] || MODES.reply;
    return [
      item.question ? h("div", { class: "live-question" }, ico("message-square", 13), h("span", { text: item.question })) : null,
      item.quote ? h("div", { class: "live-quote", text: item.quote }) : null,
      m.plain ? h("div", { class: "live-plain", text: item.text })
        : h("div", { class: "md live-md", html: renderMarkdown(item.text) }),
      item.notes?.length ? h("ul", { class: "live-notes" }, ...item.notes.map((n) => h("li", { text: n }))) : null,
    ];
  }

  const copyText = (item) => (item.mode === "summary" ? `${item.title}\n\n${item.text}` : item.text);
  const copyLabel = (item) => ({ reply: "Copier la réponse", correct: "Copier le texte corrigé" }[item.mode] || "Copier");

  function itemActions(item) {
    return [
      { label: copyLabel(item), icon: "copy", onSelect: () => ctx.copy(copyText(item), "Copié") },
      { label: "Lire", icon: "volume-2", onSelect: () => ctx.speak(item.text, `live:${item.id}`, true) },
      store.ollama ? { label: "Approfondir dans Conversations", icon: "message-square", onSelect: () => discuss(item) } : null,
      { label: "Ouvrir la capture dans Captures", icon: "palette", onSelect: () => toCaptures(item) },
      "-",
      { label: "Retirer", icon: "x", onSelect: () => api.live_delete(item.id) },
    ];
  }

  function onContextMenu(ev, item) {
    ev.preventDefault();
    const sel = window.getSelection().toString();
    openMenu([
      sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
      ...itemActions(item),
    ], ev.clientX, ev.clientY);
  }

  function speakButton(item) {
    const source = `live:${item.id}`;
    const btn = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(item.text, source, true), { size: 15, cls: "sm" });
    btn.dataset.speakSource = source;
    btn.dataset.tipIdle = "Lire";
    return btn;
  }

  /** Carte en direct : résultat de la fenêtre du moment (ou ce qui en tient lieu). */
  function renderNow() {
    const cur = S.live.current;
    const key = cur ? `${cur.id || ""}|${cur.at}|${cur.text}` : `none|${S.live.mode}`;
    if (key === shownKey) return;
    shownKey = key;
    nowCard.oncontextmenu = null;
    nowCard.classList.toggle("empty-now", !cur || !!cur.empty);
    if (!cur) {
      const m = mode();
      nowCard.replaceChildren(
        h("div", { class: "live-now-head" }, ico(m.icon, 15), h("span", { class: "live-now-title", text: m.label })),
        h("p", { class: "live-now-hint", text: `${m.tip}. Activez l'assistant, ou cliquez sur Analyser maintenant : `
          + "le résultat pour la fenêtre que vous utilisez s'affichera ici." }),
        h("p", { class: "live-privacy" }, ico("lock", 13),
          h("span", { text: "Les captures ne quittent pas ce PC (Ollama, en local) et sont effacées à la fermeture. "
            + "Le modèle n'est sollicité que lorsque du nouveau texte apparaît, et jamais pendant vos absences." })));
      return;
    }
    const meta = h("span", { class: "live-now-meta", text: `${hhmm(cur.at)} · ${cur.window || cur.app || "Fenêtre sans titre"}` });
    if (cur.empty) {
      nowCard.replaceChildren(
        h("div", { class: "live-now-head" }, ico(mode().icon, 15), h("span", { class: "live-now-title", text: cur.text }),
          h("div", { class: "head-spacer" }), meta),
        cur.situation ? h("p", { class: "live-now-hint", text: cur.situation }) : null);
      return;
    }
    const m = MODES[cur.mode] || MODES.reply;
    const copyBtn = h("button", { class: `btn sm${m.plain ? " primary" : ""}`, type: "button",
      onClick: () => ctx.copy(copyText(cur), "Copié") }, ico("copy", 14), copyLabel(cur));
    const againBtn = h("button", { class: "btn ghost sm", type: "button", onClick: () => api.live_now(),
      dataset: { tip: "Nouvelle analyse de la fenêtre utilisée" } },
      ico("refresh-cw", 14), cur.mode === "reply" ? "Autre réponse" : "Refaire");
    const discussBtn = iconButton("message-square", "Approfondir dans Conversations", () => discuss(cur), { size: 15, cls: "sm" });
    discussBtn.dataset.ollama = "";
    nowCard.replaceChildren(
      h("div", { class: "live-now-head" }, ico(m.icon, 15), h("span", { class: "live-now-title", text: cur.title }),
        h("div", { class: "head-spacer" }), meta),
      h("div", { class: "live-now-body" },
        h("div", { class: "live-now-content" }, ...resultContent(cur)),
        h("img", { class: "live-thumb zoomable", src: cur.thumb, alt: "", dataset: { tip: "Voir la capture" },
          onClick: () => openImage(cur) })),
      h("div", { class: "live-now-actions" }, copyBtn, againBtn, h("div", { class: "head-spacer" }),
        speakButton(cur), discussBtn,
        iconButton("x", "Retirer", () => api.live_delete(cur.id), { size: 15, cls: "sm" })));
    nowCard.oncontextmenu = (ev) => onContextMenu(ev, cur);
    ctx.emit("speak-sources-changed");
  }

  function itemEl(item) {
    const m = MODES[item.mode] || MODES.reply;
    const root = h("article", { class: "live-item", dataset: { id: item.id } },
      h("img", { class: "live-thumb zoomable", src: item.thumb, alt: "", dataset: { tip: "Voir la capture" },
        onClick: () => openImage(item) }),
      h("div", { class: "live-text" },
        h("div", { class: "live-meta" }, h("span", { text: hhmm(item.at) }),
          h("span", { class: "live-meta-window", text: item.window || item.app || "Fenêtre sans titre" })),
        h("div", { class: "live-item-title" }, ico(m.icon, 14), h("span", { text: item.title })),
        ...resultContent(item)),
      h("div", { class: "live-actions" },
        iconButton("copy", copyLabel(item), () => ctx.copy(copyText(item), "Copié"), { size: 15, cls: "sm" }),
        speakButton(item),
        iconButton("x", "Retirer", () => api.live_delete(item.id), { size: 15, cls: "sm" })));
    root.addEventListener("contextmenu", (ev) => onContextMenu(ev, item));
    return root;
  }

  /** Précédemment : tous les résultats sauf celui en direct. */
  function renderList() {
    const currentId = S.live.current?.id;
    const others = S.items.filter((i) => i.id !== currentId);
    list.replaceChildren(...others.map((item) => itemEl(item)));
    listHead.hidden = !others.length;
    count.textContent = others.length ? plural(others.length, "résultat", "résultats") : "";
    clearBtn.hidden = !S.items.length;
    ctx.emit("speak-sources-changed");
  }

  function renderBadge() {
    if (S.unread) ctx.setBadge("live", S.unread, "unread");
    else ctx.setBadge("live", S.live.running ? "dot" : null, "live");
  }

  /** Compte à rebours : seulement quand il est à l'écran. */
  function syncTicker() {
    const need = S.live.running && S.live.phase === "wait" && S.windowVisible && ctx.isActive("live");
    if (need && !ticker) ticker = setInterval(renderStatus, 1000);
    else if (!need && ticker) { clearInterval(ticker); ticker = null; }
  }

  function clearUnread() {
    if (!S.unread) return;
    S.unread = 0;
    renderBadge();
  }

  // ── Actions ─────────────────────────────────
  function toggle() {
    if (S.live.running) api.live_stop();
    else api.live_start();
  }

  async function setMode(id) {
    if (id === S.live.mode) return;
    S.live.mode = id;          // tout de suite à l'écran ; Python renvoie l'état
    renderStatus();
    await ctx.saveSetting("live_mode", id);
    if (id === "custom" && !goal.value.trim()) goal.focus();
  }

  function ask() {
    const question = askInput.value.trim();
    if (!question || S.live.asking || !store.ollama) return;
    askInput.value = "";
    api.live_now(question);
  }

  async function discuss(item) {
    const res = await api.live_discuss(item.id);
    if (!res?.ok) toast(res?.error || "Conversation impossible.", "error");
  }

  async function toCaptures(item) {
    if (!await api.live_to_captures(item.id)) toast("Capture introuvable.", "error");
  }

  function openImage(item) {
    openLightbox({ src: api.live_image_url(item.id), preview: item.thumb, title: item.window || item.app,
      actions: [
        store.ollama ? { label: "Approfondir dans Conversations", icon: "message-square", close: true,
          onSelect: () => discuss(item) } : null,
        { label: "Ouvrir dans Captures", icon: "palette", close: true, onSelect: () => toCaptures(item) },
      ].filter(Boolean) });
  }

  // ── Évènements Python ───────────────────────
  on("live:state", (data) => {
    const before = S.live.current?.id;
    S.live = { ...S.live, ...data };
    renderStatus();
    renderNow();
    if (S.live.current?.id !== before) renderList();
  });

  on("live:item", ({ item, replace }) => {
    const i = S.items.findIndex((x) => x.id === item.id);
    if (replace && i >= 0) S.items[i] = item;
    else {
      S.items.unshift(item);
      S.items.length = Math.min(S.items.length, MAX_ITEMS);
      if (!(ctx.isActive("live") && S.windowVisible)) { S.unread += 1; renderBadge(); }
    }
    renderList();
  });

  on("live:items", ({ items }) => {
    S.items = items || [];
    renderList();
  });

  on("chat:models", ({ models }) => { S.models = models || []; });
  on("chat:available", () => renderStatus());
  on("window:shown", () => {
    S.windowVisible = true;
    if (ctx.isActive("live")) clearUnread();
    syncTicker();
  });
  on("window:hidden", () => { S.windowVisible = false; syncTicker(); });

  // ── Initialisation ──────────────────────────
  if (S.available) {
    renderModes();
    renderIntervals();
    renderStatus();
    renderNow();
    renderList();
    setTimeout(renderBadge, 0);    // la barre latérale est construite après les vues
  }

  return {
    el,
    onShow() { clearUnread(); syncTicker(); },
    onHide() { syncTicker(); },
    onWindowShown() { if (ctx.isActive("live")) clearUnread(); },
    focus() { if (S.available) askInput.focus(); },
  };
}
