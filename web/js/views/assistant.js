// Vue Assistant : questions sur Gmail et WhatsApp (réponse d'Ollama, au fil de l'eau)
// et annonce à voix haute des nouveaux messages — assistant.py.
// En haut, les annonces et les sources ; puis les messages arrivés ; puis les questions.
import { h, plural } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, toast } from "../ui.js";
import { renderMarkdown } from "../markdown.js";
import { importanceIcon } from "./maillist.js";

const SOURCES = {
  gmail:    { label: "Gmail",    icon: "mail" },
  whatsapp: { label: "WhatsApp", icon: "message-circle" },
};
const SOURCE_STATES = { loading: "chargement…", ok: "surveillé", error: "à vérifier" };
const FEED_SHOWN = 5;          // messages arrivés affichés avant « Voir tout »
const SUGGESTIONS = [
  "Quels e-mails non lus demandent une action de ma part ?",
  "Qui m'a écrit sur WhatsApp aujourd'hui ?",
  "Résume mes nouveaux messages",
  "Ai-je reçu un e-mail de ma banque ?",
];

/** "2026-10-07T14:02:05" → "14:02". */
const hhmm = (iso) => (iso || "").slice(11, 16);

export function createAssistantView(ctx, state) {
  const { api, on, store } = ctx;
  const init = state.assistant || {};
  const S = {
    st: { watching: false, sources: {}, asking: null, model: "", ...init },
    feed: init.feed || [],
    history: init.history || [],
    models: state.chat?.models || [],
    unread: 0,                 // messages arrivés hors de la vue
    feedOpen: false,
    windowVisible: !!state.visible,
  };
  const answers = new Map();   // id → { item, el, body, … } (questions affichées)

  // ── Annonces vocales ────────────────────────
  const toggleBtn = h("button", { class: "asst-btn", type: "button", onClick: toggleWatch });
  const headline = h("div", { class: "asst-headline" });
  const detail = h("div", { class: "asst-detail" });
  const exampleBtn = h("button", { class: "btn ghost sm", type: "button", onClick: () => api.assistant_example(),
    dataset: { tip: "Entendre une annonce d'exemple" } }, ico("volume-2", 14), "Exemple");
  const sourcesRow = h("div", { class: "asst-sources" });
  const summaryBtn = settingToggle("sparkles", "Résumer", "assistant_summary",
    "Ollama résume chaque nouveau message pour l'oral, dit s'il est urgent ou important et ce qu'il attend "
    + "de vous ; c'est ce résumé qui est lu (sinon l'objet ou le texte tel quel)");
  const contentBtn = settingToggle("text-select", "Lire le contenu", "assistant_content",
    "Annonce aussi le résumé, l'objet de l'e-mail ou le texte du message (sinon l'expéditeur seul)");
  const problems = h("div", { class: "asst-problems" });
  const deck = h("div", { class: "asst-deck" },
    h("div", { class: "asst-main" }, toggleBtn, h("div", { class: "asst-status" }, headline, detail), exampleBtn),
    h("div", { class: "asst-options" }, sourcesRow, h("div", { class: "head-spacer" }), summaryBtn, contentBtn),
    problems);

  // ── Messages arrivés ────────────────────────
  const feedCount = h("span", { class: "view-sub" });
  const feedList = h("div", { class: "asst-feed" });
  const feedMore = h("button", { class: "btn ghost sm asst-more", type: "button",
    onClick: () => { S.feedOpen = !S.feedOpen; renderFeed(); } });
  const feedBox = h("div", { class: "asst-block" },
    h("div", { class: "asst-block-head" }, h("span", { class: "asst-block-title", text: "Messages arrivés" }), feedCount,
      h("div", { class: "head-spacer" }),
      h("button", { class: "btn ghost sm", type: "button", onClick: () => api.assistant_clear_feed() },
        ico("trash-2", 14), "Effacer")),
    feedList, feedMore);

  // ── Questions ───────────────────────────────
  const thread = h("div", { class: "asst-thread" });
  const clearBtn = h("button", { class: "btn ghost sm", type: "button", onClick: () => api.assistant_clear() },
    ico("trash-2", 14), "Effacer");
  const threadHead = h("div", { class: "asst-block-head" },
    h("span", { class: "asst-block-title", text: "Questions" }), h("div", { class: "head-spacer" }), clearBtn);
  const intro = h("div", { class: "asst-intro" });
  const body = h("div", { class: "asst-body" }, deck, feedBox, threadHead, intro, thread);

  // ── Champ de question ───────────────────────
  const input = h("textarea", { class: "field asst-input", rows: "1", maxlength: "1000", spellcheck: "true",
    "aria-label": "Question à l'assistant",
    onKeydown: (ev) => { if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); ask(); } },
    onInput: autosize });
  const sendBtn = h("button", { class: "btn primary sm asst-send", type: "button", onClick: () => (S.st.asking ? api.assistant_stop() : ask()) });
  const micBtn = iconButton("mic", "Dictée vocale", () => { input.focus(); api.dictate(); }, { kbd: "Win+H" });
  const speakBtn = settingToggle("volume-2", "Lire les réponses", "assistant_speak", "Lire chaque réponse à voix haute, dès ses premières phrases");
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle de l'assistant",
    dataset: { tip: "Modèle qui répond aux questions" },
    onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget, "right") },
    ico("sparkles", 14), modelName, ico("chevron-down", 14));
  const askBar = h("div", { class: "asst-ask" },
    h("div", { class: "asst-ask-inner" },
      h("div", { class: "asst-ask-row" }, input, micBtn, sendBtn),
      h("div", { class: "asst-ask-options" }, speakBtn, h("div", { class: "head-spacer" }), modelBtn)));

  const sub = h("span", { class: "view-sub" });
  const el = h("section", {},
    h("header", { class: "view-head" }, h("span", { class: "view-title", text: "Assistant" }), sub),
    body, askBar);

  // ── Rendu : annonces et sources ─────────────
  function renderStatus() {
    const { watching } = S.st;
    deck.classList.toggle("on", watching);
    toggleBtn.replaceChildren(ico(watching ? "bell" : "volume-x", 22));
    toggleBtn.dataset.tip = watching ? "Arrêter les annonces" : "Annoncer les nouveaux messages à voix haute";
    toggleBtn.setAttribute("aria-label", toggleBtn.dataset.tip);
    toggleBtn.setAttribute("aria-pressed", String(watching));
    const names = Object.keys(SOURCES).filter(enabled).map((k) => SOURCES[k].label);
    headline.textContent = watching ? "Annonces vocales activées" : "Annonces vocales désactivées";
    const summarized = !!store.settings.assistant_summary;
    detail.textContent = !names.length ? "Activez Gmail ou WhatsApp ci-dessous."
      : watching ? `Chaque nouveau message ${names.join(" ou ")} est ${summarized ? "résumé puis annoncé" : "annoncé"} à voix haute`
        + (summarized && !store.ollama ? " (sans résumé tant qu'Ollama ne répond pas)." : ".")
      : `Activez-les pour entendre l'arrivée des nouveaux messages ${names.join(" et ")}`
        + (summarized ? ", résumés avec ce qu'ils attendent de vous." : ".");
    sub.textContent = watching ? "annonces actives" : "";

    sourcesRow.replaceChildren(...Object.entries(SOURCES).map(([key, src]) => {
      const on = enabled(key);
      const st = S.st.sources?.[key] || {};
      const note = watching && on ? SOURCE_STATES[st.state] : "";
      const btn = h("button", { class: `btn sm asst-source${on ? " on" : ""}${st.state === "error" && on && watching ? " error" : ""}`,
        type: "button", role: "switch", "aria-checked": String(on),
        dataset: { tip: on ? `L'assistant lit ${src.label} : cliquez pour l'en exclure` : `Inclure ${src.label}` },
        onClick: async () => { await ctx.saveSetting(`assistant_${key}`, !on); renderStatus(); } },
        ico(on ? "check" : src.icon, 14), src.label, note ? h("span", { class: "asst-source-state", text: note }) : null);
      return btn;
    }));

    const issues = Object.entries(SOURCES).filter(([key]) => watching && enabled(key) && S.st.sources?.[key]?.state === "error");
    problems.replaceChildren(...issues.map(([key, src]) => h("div", { class: "asst-problem" },
      ico("triangle-alert", 13), h("span", { text: S.st.sources[key].message || `${src.label} illisible.` }),
      h("button", { class: "link-btn", type: "button", onClick: () => ctx.navigate(key), text: `Ouvrir ${src.label}` }))));
    problems.hidden = !issues.length;

    const ollama = !!store.ollama;
    const asking = !!S.st.asking;
    sendBtn.replaceChildren(ico(asking ? "square" : "arrow-up", 14), asking ? "Arrêter" : "Demander");
    sendBtn.disabled = !ollama && !asking;
    input.disabled = !ollama;
    input.placeholder = ollama ? "Une question sur vos e-mails ou vos messages WhatsApp ?"
      : "Les questions ont besoin d'Ollama (en local), qui ne répond pas.";
    modelName.textContent = S.st.model || "Aucun modèle";
    modelBtn.hidden = !ollama;
    renderBadge();
  }

  function enabled(key) { return store.settings[`assistant_${key}`] !== false; }

  function settingToggle(iconName, label, key, tip) {
    const btn = h("button", { class: "btn sm asst-toggle", type: "button", role: "switch", dataset: { tip } },
      ico(iconName, 14), label);
    const sync = () => {
      btn.classList.toggle("on", !!store.settings[key]);
      btn.setAttribute("aria-checked", String(!!store.settings[key]));
    };
    btn.addEventListener("click", async () => { await ctx.saveSetting(key, !store.settings[key]); sync(); renderStatus(); });
    sync();
    return btn;
  }

  function modelItems() {
    const current = S.st.model;
    return [
      { section: "Modèle de l'assistant" },
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === current, onSelect: () => ctx.saveSetting("assistant_model", m) }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  // ── Rendu : messages arrivés ────────────────
  /** Le texte de l'annonce (résumé compris) : lu et copié tel quel. */
  function feedText(item) {
    if (item.spoken) return item.spoken;
    return item.source === "gmail"
      ? `E-mail de ${item.title} : ${item.text}`
      : `Message WhatsApp de ${item.title} : ${item.text}`;
  }

  /** Résumé de l'annonce (Ollama) avec ce que le message attend de vous ; sans
   *  résumé, le début du texte de l'e-mail. */
  function summaryEl(item) {
    if (item.summary_state === "pending") {
      return h("div", { class: "asst-msg-pending" }, h("span", { class: "pane-busy" }), "Résumé en cours…");
    }
    if (!item.summary) return item.snippet ? h("div", { class: "asst-msg-snippet", text: item.snippet }) : null;
    return h("div", { class: "asst-msg-summary", dataset: { tip: item.model ? `Résumé par ${item.model}` : null } },
      h("span", { class: "asst-msg-summary-text" }, ico("sparkles", 11), item.summary),
      item.action ? h("span", { class: "asst-msg-action" }, ico("chevron-right", 12), h("span", { text: item.action }))
        : null);
  }

  function feedEl(item) {
    const src = SOURCES[item.source] || SOURCES.gmail;
    const count = item.count > 1 ? h("span", { class: "asst-count", text: `${item.count} messages` }) : null;
    const source = `assistant:feed:${item.id}`;
    const speak = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(feedText(item), source), { size: 15, cls: "sm" });
    speak.dataset.speakSource = source;
    speak.dataset.tipIdle = "Lire";
    const row = h("article", { class: "asst-msg", tabindex: "0",
      dataset: { tip: `Ouvrir ${src.label}` },
      onClick: (ev) => { if (!ev.target.closest("button")) ctx.navigate(item.source); },
      onKeydown: (ev) => { if (ev.key === "Enter") ctx.navigate(item.source); } },
      h("span", { class: `asst-msg-icon ${item.source}` }, ico(src.icon, 14)),
      h("div", { class: "asst-msg-text" },
        h("div", { class: "asst-msg-head" }, importanceIcon(item.summary ? item.importance : ""),
          h("span", { class: "asst-msg-from", text: item.title }), count,
          h("span", { class: "asst-msg-time", text: hhmm(item.at) })),
        h("div", { class: "asst-msg-body", text: item.text || "…" }),
        summaryEl(item)),
      h("div", { class: "asst-msg-actions" }, speak));
    row.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      openMenu([
        { label: `Ouvrir ${src.label}`, icon: src.icon, onSelect: () => ctx.navigate(item.source) },
        { label: "Lire", icon: "volume-2", onSelect: () => ctx.speak(feedText(item), source) },
        { label: "Copier", icon: "copy", onSelect: () => ctx.copy(feedText(item), "Copié") },
      ], ev.clientX, ev.clientY);
    });
    return row;
  }

  function renderFeed() {
    const shown = S.feedOpen ? S.feed : S.feed.slice(0, FEED_SHOWN);
    feedList.replaceChildren(...shown.map(feedEl));
    feedBox.hidden = !S.feed.length;
    feedCount.textContent = S.feed.length ? plural(S.feed.length, "message", "messages") : "";
    feedMore.hidden = S.feed.length <= FEED_SHOWN;
    feedMore.replaceChildren(ico(S.feedOpen ? "chevron-down" : "chevron-right", 14),
      S.feedOpen ? "Réduire" : `Voir tout (${S.feed.length})`);
    ctx.emit("speak-sources-changed");
  }

  // ── Rendu : questions ───────────────────────
  function renderIntro() {
    intro.hidden = S.history.length > 0;
    threadHead.hidden = !S.history.length;
    if (!intro.hidden) {
      intro.replaceChildren(
        h("p", { class: "asst-intro-text", text: "Posez une question sur vos e-mails et vos messages WhatsApp : "
          + "l'assistant relit Gmail et WhatsApp (ouverts en arrière-plan si besoin) et répond avec Ollama, en local." }),
        h("div", { class: "asst-suggestions" }, ...SUGGESTIONS.map((q) =>
          h("button", { class: "chip small asst-suggestion", type: "button", onClick: () => ask(q) }, q))),
        h("p", { class: "asst-privacy" }, ico("lock", 13),
          h("span", { text: "Rien ne quitte ce PC, rien n'est envoyé ni marqué comme lu. Pour WhatsApp, l'assistant voit "
            + "le dernier message de chaque discussion, la discussion ouverte et les messages arrivés pendant les annonces." })));
    }
  }

  function sourcesLine(item) {
    if (!item.sources?.length) return null;
    return h("div", { class: "asst-read" }, ...item.sources.map((s) => {
      const src = SOURCES[s.source] || SOURCES.gmail;
      return h("span", { class: `asst-read-item${s.ok ? "" : " error"}`, dataset: { tip: s.note || "" } },
        ico(s.ok ? src.icon : "triangle-alert", 12), h("span", { text: s.ok ? `${src.label} · ${s.note}` : `${src.label} : ${s.note}` }));
    }));
  }

  function qaEl(item) {
    const source = `assistant:${item.id}`;
    const answer = h("div", { class: "md asst-answer" });
    const phase = h("div", { class: "asst-phase" });
    const meta = h("div", { class: "asst-meta" });
    const speak = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(entry.item.answer, source, true), { size: 15, cls: "sm" });
    speak.dataset.speakSource = source;
    speak.dataset.tipIdle = "Lire";
    const actions = h("div", { class: "asst-actions" },
      iconButton("copy", "Copier la réponse", () => ctx.copy(entry.item.answer, "Copié"), { size: 15, cls: "sm" }),
      speak,
      iconButton("rotate-ccw", "Reposer la question", () => ask(entry.item.question), { size: 15, cls: "sm" }),
      iconButton("x", "Retirer", () => api.assistant_delete(item.id), { size: 15, cls: "sm" }));
    const reply = h("div", { class: "asst-reply" }, phase, answer, meta, actions);
    const root = h("div", { class: "asst-qa", dataset: { id: item.id } },
      h("div", { class: "asst-question" }, h("span", { text: item.question })), reply);
    reply.addEventListener("contextmenu", (ev) => {
      const sel = window.getSelection().toString();
      ev.preventDefault();
      openMenu([
        sel ? { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => ctx.copy(sel) } : null,
        { label: "Copier la réponse", icon: "copy", onSelect: () => ctx.copy(entry.item.answer, "Copié") },
        { label: "Lire", icon: "volume-2", onSelect: () => ctx.speak(entry.item.answer, source, true) },
        { label: "Reposer la question", icon: "rotate-ccw", onSelect: () => ask(entry.item.question) },
        "-",
        { label: "Retirer", icon: "x", onSelect: () => api.assistant_delete(item.id) },
      ], ev.clientX, ev.clientY);
    });
    const entry = { item, root, answer, phase, meta, actions, reply, shown: null };
    answers.set(item.id, entry);
    updateQa(entry);
    return root;
  }

  function updateQa(entry) {
    const { item } = entry;
    const busy = item.state === "reading" || item.state === "thinking";
    entry.reply.classList.toggle("busy", busy);
    entry.phase.hidden = !busy || (item.state === "thinking" && !!item.answer);
    entry.phase.replaceChildren(h("span", { class: "pane-busy" }), h("span", { text: item.phase || "…" }));
    if (entry.shown !== item.answer) {
      entry.shown = item.answer;
      entry.answer.innerHTML = item.answer ? renderMarkdown(item.answer) : "";
    }
    entry.answer.hidden = !item.answer;
    const notes = [];
    if (item.state === "stopped") notes.push(h("span", { class: "asst-note", text: "Arrêtée." }));
    if (item.state === "error") notes.push(h("span", { class: "asst-note error", text: item.error || "Échec." }));
    entry.meta.replaceChildren(sourcesLine(item) || "", ...notes,
      busy ? "" : h("span", { class: "asst-model", text: `${item.model} · ${hhmm(item.at)}` }));
    entry.actions.hidden = busy || !item.answer;
  }

  function renderThread() {
    answers.clear();
    thread.replaceChildren(...S.history.map(qaEl));
    renderIntro();
    ctx.emit("speak-sources-changed");
  }

  function scrollDown() {
    requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
  }

  function nearBottom() {
    return body.scrollHeight - body.scrollTop - body.clientHeight < 80;
  }

  function renderBadge() {
    if (S.unread) ctx.setBadge("assistant", S.unread, "unread");
    else ctx.setBadge("assistant", S.st.watching ? "dot" : null, "live");
  }

  function clearUnread() {
    if (!S.unread) return;
    S.unread = 0;
    renderBadge();
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  }

  // ── Actions ─────────────────────────────────
  function toggleWatch() {
    ctx.saveSetting("assistant_announce", !S.st.watching);
  }

  async function ask(text) {
    const question = (text ?? input.value).trim();
    if (!question || S.st.asking || !store.ollama) return;
    const res = await api.assistant_ask(question);
    if (!res?.ok) { toast(res?.error || "Question impossible.", "error"); return; }
    if (text === undefined) { input.value = ""; autosize(); }
  }

  // ── Évènements Python ───────────────────────
  on("assistant:state", (data) => { S.st = { ...S.st, ...data }; renderStatus(); });

  on("assistant:feed", ({ items, added }) => {
    S.feed = items || [];
    if (added && !(ctx.isActive("assistant") && S.windowVisible)) S.unread += added;
    if (!S.feed.length) S.unread = 0;
    renderFeed();
    renderBadge();
  });

  on("assistant:qa", ({ item }) => {
    const entry = answers.get(item.id);
    if (entry) {
      entry.item = item;
      updateQa(entry);
      const i = S.history.findIndex((x) => x.id === item.id);
      if (i >= 0) S.history[i] = item;
      return;
    }
    S.history.push(item);
    thread.append(qaEl(item));
    renderIntro();
    scrollDown();
    ctx.emit("speak-sources-changed");
  });

  on("assistant:delta", ({ id, text }) => {
    const entry = answers.get(id);
    if (!entry) return;
    const follow = nearBottom();
    entry.item = { ...entry.item, answer: (entry.item.answer || "") + text, state: "thinking" };
    updateQa(entry);
    if (follow) scrollDown();
  });

  on("assistant:history", ({ items }) => {
    S.history = items || [];
    renderThread();
  });

  on("chat:models", ({ models }) => { S.models = models || []; });
  on("chat:available", () => renderStatus());
  on("window:shown", () => {
    S.windowVisible = true;
    if (ctx.isActive("assistant")) clearUnread();
  });
  on("window:hidden", () => { S.windowVisible = false; });

  // ── Initialisation ──────────────────────────
  renderStatus();
  renderFeed();
  renderThread();
  setTimeout(renderBadge, 0);    // la barre latérale est construite après les vues

  return {
    el,
    onShow() { clearUnread(); },
    onWindowShown() { if (ctx.isActive("assistant")) clearUnread(); },
    focus() { if (!input.disabled) input.focus(); },
  };
}
