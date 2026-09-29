// Vue Conversations : chat avec les modèles Ollama.
import { h, throttle, plural } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, TabStrip, toast, kbdCombo } from "../ui.js";
import { renderMarkdown, renderMarkdownForCopy, splitThinking } from "../markdown.js";

const wordCount = (s) => (s.trim().match(/\S+/g) || []).length;

export function createChatView(ctx, state) {
  const { api, on } = ctx;
  const S = {
    tabs: new Map(),                 // id → état de l'onglet
    order: [],
    active: null,
    models: state.chat.models || [],
    modelsError: state.chat.models_error || "",
    defaultModel: state.chat.default_model || "",
  };

  // ── Structure ───────────────────────────────
  const strip = new TabStrip({
    onSelect: select,
    onClose: closeTab,
    onNew: () => newTab(),
    onRename: rename,
    onMenu: tabMenu,
    newLabel: "Nouvelle conversation",
    placeholder: "Nouvelle conversation",
  });
  const body = h("div", { class: "chat-body" });
  const toBottom = h("button", { class: "to-bottom", type: "button", hidden: true, "aria-label": "Aller en bas",
    onClick: () => { const t = activeTab(); if (t) scrollToBottom(t, true); } }, ico("arrow-down", 16));
  const dropOverlay = h("div", { class: "drop-overlay" },
    h("div", { class: "inner" }, ico("paperclip", 22), "Déposez les fichiers pour les joindre"));
  body.append(toBottom, dropOverlay);

  const input = h("textarea", { rows: "1", placeholder: "Écrivez un message…", spellcheck: "true",
    "aria-label": "Message" });
  const attRow = h("div", { class: "attachments", hidden: true });
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", dataset: { tip: "Choisir le modèle" },
    onClick: (ev) => openMenuBelow(modelItems(activeTab()), ev.currentTarget) },
    ico("sparkles", 14), modelName, ico("chevron-down", 14));
  const ttsBtn = iconButton("volume-2", "Lire la réponse", () => speakMode(activeTab(), ctx.store.settings.tts_mode_chat));
  ttsBtn.dataset.tipIdle = "Lire (clic droit : mode)";
  ttsBtn.addEventListener("contextmenu", (ev) => ctx.ttsModeMenu(ev, "tts_mode_chat",
    [["last", "Lire la dernière réponse"], ["all", "Lire toute la conversation"]]));
  const sendBtn = h("button", { class: "send-btn", type: "button", onClick: send });
  const composer = h("div", { class: "composer" },
    attRow,
    input,
    h("div", { class: "composer-bar" },
      iconButton("paperclip", "Joindre un fichier", attachDialog),
      iconButton("scan", "Capture d'écran", () => { const t = activeTab(); if (t) api.chat_screenshot(t.id); }),
      iconButton("mic", "Dictée vocale", dictate, { kbd: "Win+H" }),
      modelBtn,
      h("div", { class: "head-spacer" }),
      ttsBtn,
      sendBtn));

  const el = h("section", {},
    h("header", { class: "view-head" }, strip.el),
    body,
    h("div", { class: "composer-wrap" }, composer));

  // ── Onglets ─────────────────────────────────
  const activeTab = () => S.tabs.get(S.active);

  function makeTab(data) {
    const pane = h("div", { class: "chat-pane" });
    const t = { id: data.id, data, pane, stick: true, draft: "", stream: null, partial: null };
    t.renderPartial = throttle(() => {
      if (t.stream && t.partial) t.stream.update(t.partial.content, t.partial.thinking, true);
      autoScroll(t);
    }, 60);
    pane.addEventListener("scroll", () => onScroll(t), { passive: true });
    pane.addEventListener("contextmenu", (ev) => paneMenu(t, ev));
    body.insertBefore(pane, toBottom);
    S.tabs.set(t.id, t);
    renderPane(t);
    return t;
  }

  function renderStrip() {
    strip.render(S.order.map((id) => {
      const t = S.tabs.get(id);
      return { id, title: t.data.title, label: t.data.label, busy: t.data.streaming };
    }), S.active);
    ctx.setBadge("chat", [...S.tabs.values()].some((t) => t.data.streaming) ? "dot" : null);
  }

  function select(id) {
    const t = S.tabs.get(id);
    if (!t) return;
    const prev = activeTab();
    if (prev && prev !== t) prev.draft = input.value;
    S.active = id;
    for (const x of S.tabs.values()) x.pane.classList.toggle("active", x === t);
    input.value = t.draft;
    autosize();
    renderStrip();
    renderAttachments();
    updateComposer();
    toBottom.hidden = t.stick;
    if (ctx.isActive("chat")) input.focus();
  }

  async function newTab(afterId = null) {
    const data = await api.chat_new(afterId);
    if (!data) return;
    const t = makeTab(data);
    const idx = afterId ? S.order.indexOf(afterId) : -1;
    if (idx >= 0) S.order.splice(idx + 1, 0, t.id);
    else S.order.push(t.id);
    select(t.id);
  }

  async function closeTab(id) {
    const res = await api.chat_close(id);
    const t = S.tabs.get(id);
    if (!res || !t) return;
    if (res.reset) {
      t.data = res.reset;
      t.draft = "";
      if (S.active === id) input.value = "";
      renderPane(t);
      toast("Conversation effacée.", "info");
    } else if (res.removed) {
      const idx = S.order.indexOf(id);
      S.order.splice(idx, 1);
      S.tabs.delete(id);
      t.pane.remove();
      if (S.active === id) select(S.order[Math.max(0, idx - 1)]);
    }
    renderStrip();
    updateComposer();
  }

  async function rename(id, label) {
    const data = await api.chat_rename(id, label);
    const t = S.tabs.get(id);
    if (data && t) Object.assign(t.data, { label: data.label, title: data.title });
    renderStrip();
  }

  function tabMenu(id, ev) {
    openMenu([
      { label: "Nouvelle conversation", icon: "plus", onSelect: () => newTab(id) },
      { label: "Renommer…", icon: "pencil", hint: "F2", onSelect: () => strip.rename(id) },
      "-",
      { label: "Fermer la conversation", icon: "x", onSelect: () => closeTab(id) },
    ], ev.clientX, ev.clientY);
  }

  // ── Rendu des messages ──────────────────────
  function emptyState() {
    const model = effectiveModel(activeTab() || { data: {} });
    return h("div", { class: "empty" },
      h("div", { class: "glyph" }, ico("sparkles", 22)),
      h("h3", { text: "Nouvelle conversation" }),
      h("p", { text: model ? `Vous discutez avec ${model}, en local via Ollama.`
        : (S.modelsError || "Chargement des modèles…") }),
      h("div", { class: "hints" },
        h("span", {}, ico("paperclip", 13), "Glissez-déposez des fichiers"),
        h("span", {}, ico("scan", 13), "Capture d'écran"),
        h("span", {}, ...kbdCombo("shift+enter"), " nouvelle ligne")));
  }

  /** index : position du message dans la conversation (sert à le retrouver côté Python). */
  function userMessage(t, m, index) {
    let images = 0;
    const files = (m.attachments || []).map((a) => {
      const el = a.type === "image" && a.thumb
        ? h("img", { src: a.thumb, alt: a.name, dataset: { tip: a.name } })
        : h("span", { class: "chip small" },
          h("span", { class: "chip-icon" }, ico(a.type === "image" ? "image" : "file-text", 13)),
          h("span", { class: "name", text: a.name }));
      if (a.type === "image") {
        const k = images++;   // rang parmi les images du message
        el.addEventListener("contextmenu", (ev) => imageMenu(t, index, k, a.name, ev));
      }
      return el;
    });
    const copy = () => copyUserMessage(t, index);
    const root = h("div", { class: "msg msg-user" },
      files.length ? h("div", { class: "msg-files" }, files) : null,
      m.text ? h("div", { class: "bubble", text: m.text }) : null,
      h("div", { class: "msg-actions" },
        iconButton("copy", images ? "Copier le message et ses images" : "Copier", copy, { size: 15, cls: "sm" })));
    root.copyMessage = copy;
    return root;
  }

  async function copyUserMessage(t, index) {
    if (await api.chat_copy_message(t.id, index)) toast("Message copié");
    else toast("Copie impossible : le presse-papiers est occupé.", "error");
  }

  function imageMenu(t, index, k, name, ev) {
    ev.preventDefault();
    ev.stopPropagation();
    openMenu([
      { label: "Copier l'image", icon: "copy", onSelect: async () => {
        if (await api.chat_copy_image(t.id, index, k)) toast("Image copiée");
        else toast("Image introuvable ou presse-papiers occupé.", "error");
      } },
      { label: "Ouvrir dans Captures", icon: "palette", onSelect: async () => {
        if (!await api.chat_image_to_captures(t.id, index, k, name)) toast("Image introuvable.", "error");
      } },
    ], ev.clientX, ev.clientY);
  }

  function botMessage(t, m, live = false) {
    const root = h("div", { class: "msg msg-bot" });
    const thinkLabel = h("span", { class: "label" });
    const thinkBody = h("div", { class: "thinking-body" });
    const summary = h("summary", {}, h("span", { class: "chev" }, ico("chevron-right", 13)), ico("brain", 14), thinkLabel);
    const think = h("details", { class: "thinking", hidden: true }, summary, thinkBody);
    const content = h("div", { class: "md" });
    const source = `chat-msg:${t.id}:${Math.random().toString(36).slice(2, 8)}`;
    const speakBtn = iconButton("volume-2", "Lire", () => ctx.toggleSpeak(state_.text, source, true), { size: 15, cls: "sm" });
    speakBtn.dataset.speakSource = source;
    speakBtn.dataset.tipIdle = "Lire";
    // Texte Markdown (éditeurs, champs de saisie) + HTML mis en forme (Word, Outlook, mails…)
    const copy = async () => {
      if (!state_.text) return;
      if (await api.copy_rich(state_.text, renderMarkdownForCopy(state_.text))) toast("Réponse copiée");
      else toast("Copie impossible : le presse-papiers est occupé.", "error");
    };
    const actions = h("div", { class: "msg-actions" },
      iconButton("copy", "Copier la réponse", copy, { size: 15, cls: "sm" }),
      speakBtn);
    root.copyMessage = copy;
    root.append(
      h("div", { class: "msg-meta" }, h("span", { class: "avatar" }, ico("sparkles", 13)),
        h("span", { class: "msg-model", text: m.model || t.data.model || "Assistant" })),
      think, content, actions);
    summary.addEventListener("click", () => { think.dataset.touched = "1"; });

    const state_ = { text: "" };
    function update(text, thinking, streaming) {
      let answer = text || "";
      let reasoning = thinking || "";
      let reasoningOpen = false;
      if (!reasoning) {
        const split = splitThinking(answer);
        if (split.thinking || split.open) {
          reasoning = split.thinking;
          answer = split.content;
          reasoningOpen = split.open;
        }
      }
      state_.text = answer;
      if (reasoning) {
        const thinkingNow = streaming && (!answer || reasoningOpen);
        think.hidden = false;
        think.classList.toggle("live", thinkingNow);
        thinkLabel.textContent = thinkingNow ? "Réflexion…" : `Réflexion · ${plural(wordCount(reasoning), "mot", "mots")}`;
        const atEnd = thinkBody.scrollHeight - thinkBody.scrollTop - thinkBody.clientHeight < 30;
        thinkBody.textContent = reasoning;
        if (!think.dataset.touched) think.open = thinkingNow;
        if (thinkingNow && atEnd) thinkBody.scrollTop = thinkBody.scrollHeight;
      } else {
        think.hidden = true;
      }
      if (streaming && !answer) {
        content.innerHTML = reasoning ? "" : '<div class="waiting"><i></i><i></i><i></i></div>';
      } else {
        content.innerHTML = renderMarkdown(answer, { highlight: !streaming, caret: streaming });
      }
      actions.hidden = streaming;
    }
    update(m.text, m.thinking, live);
    return { root, update };
  }

  function notice(text, kind = "") {
    return h("div", { class: `notice ${kind}` }, ico(kind === "error" ? "triangle-alert" : "info", 14), text);
  }

  function renderPane(t) {
    const d = t.data;
    t.stream = null;
    if (!d.messages.length && !d.streaming) {
      t.pane.replaceChildren(emptyState());
      return;
    }
    const thread = h("div", { class: "thread" });
    d.messages.forEach((m, i) => {
      thread.append(m.role === "user" ? userMessage(t, m, i) : botMessage(t, m).root);
    });
    if (d.streaming) {
      t.partial = { content: d.partial?.content || "", thinking: d.partial?.thinking || "" };
      t.stream = botMessage(t, { text: t.partial.content, thinking: t.partial.thinking, model: d.partial?.model || d.model }, true);
      thread.append(t.stream.root);
    }
    t.pane.replaceChildren(thread);
    scrollToBottom(t, true);
    ctx.emit("speak-sources-changed");
  }

  function appendNotice(t, text, kind) {
    const thread = t.pane.querySelector(".thread");
    if (!thread) return;
    thread.append(notice(text, kind));
    autoScroll(t);
  }

  // ── Défilement ──────────────────────────────
  function onScroll(t) {
    t.stick = t.pane.scrollHeight - t.pane.scrollTop - t.pane.clientHeight < 60;
    if (t.id === S.active) toBottom.hidden = t.stick;
  }
  function autoScroll(t) {
    if (t.stick) t.pane.scrollTop = t.pane.scrollHeight;
  }
  function scrollToBottom(t, force) {
    if (force) t.stick = true;
    requestAnimationFrame(() => {
      t.pane.scrollTop = t.pane.scrollHeight;
      if (t.id === S.active) toBottom.hidden = true;
    });
  }

  // ── Composition ─────────────────────────────
  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 260)}px`;
  }

  function effectiveModel(t) {
    const wanted = t?.data?.model;
    if (wanted && S.models.includes(wanted)) return wanted;
    if (S.defaultModel && S.models.includes(S.defaultModel)) return S.defaultModel;
    return S.models[0] || "";
  }

  function updateComposer() {
    const t = activeTab();
    const streaming = !!t?.data.streaming;
    const hasContent = input.value.trim() || t?.data.attachments?.length;
    sendBtn.replaceChildren(ico(streaming ? "square" : "arrow-up", streaming ? 14 : 17, 2.2));
    sendBtn.disabled = !streaming && !hasContent;
    sendBtn.dataset.tip = streaming ? "Arrêter la génération" : "Envoyer";
    sendBtn.setAttribute("aria-label", sendBtn.dataset.tip);
    if (streaming) delete sendBtn.dataset.kbd; else sendBtn.dataset.kbd = "Entrée";
    const model = effectiveModel(t);
    modelName.textContent = model || (S.modelsError ? "Aucun modèle" : "Chargement…");
    const source = t ? `chat:${t.id}` : "";
    if (ttsBtn.dataset.speakSource !== source) {
      ttsBtn.dataset.speakSource = source;
      ctx.emit("speak-sources-changed");
    }
  }

  function renderAttachments() {
    const t = activeTab();
    const list = t?.data.attachments || [];
    attRow.hidden = !list.length;
    attRow.replaceChildren(...list.map((a) => {
      const chip = h("span", { class: "chip", dataset: { tip: a.path || a.name } },
        a.type === "image" && a.thumb ? h("img", { class: "thumb", src: a.thumb, alt: "" })
          : h("span", { class: "chip-icon" }, ico(a.type === "image" ? "image" : "file-text", 13)),
        h("span", { class: "name", text: a.name }),
        iconButton("x", "Retirer", () => api.chat_detach(t.id, a.id), { size: 13, cls: "sm" }));
      chip.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        openMenu([
          { label: "Afficher dans l'explorateur", icon: "folder-open", disabled: !a.path,
            onSelect: () => api.reveal_path(a.path) },
          "-",
          { label: "Retirer", icon: "x", onSelect: () => api.chat_detach(t.id, a.id) },
        ], ev.clientX, ev.clientY);
      });
      return chip;
    }));
    updateComposer();
  }

  async function send() {
    const t = activeTab();
    if (!t) return;
    if (t.data.streaming) { api.chat_stop(t.id); return; }
    const text = input.value;
    if (!text.trim() && !t.data.attachments.length) return;
    const model = effectiveModel(t);
    if (!model) { toast(S.modelsError || "Aucun modèle disponible.", "error"); return; }
    input.value = "";
    t.draft = "";
    autosize();
    updateComposer();
    const res = await api.chat_send(t.id, text, model);
    if (!res?.ok) {
      if (res?.error) toast(res.error, "error");
      if (!input.value) { input.value = text; t.draft = text; autosize(); updateComposer(); }
    }
  }

  async function attachDialog() {
    const t = activeTab();
    if (!t) return;
    const res = await api.chat_attach_dialog(t.id);
    for (const err of res?.errors || []) toast(err, "error");
  }

  function dictate() {
    input.focus();
    api.dictate();
  }

  function modelItems(t) {
    const current = effectiveModel(t);
    const items = S.models.length
      ? S.models.map((m) => ({ label: m, checked: m === current, onSelect: () => setModel(t, m) }))
      : [{ label: S.modelsError || "Aucun modèle", disabled: true }];
    return [{ section: "Modèle" }, ...items, "-",
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() }];
  }

  function setModel(t, model) {
    if (!t) return;
    t.data.model = model;
    S.defaultModel = model;
    api.chat_set_model(t.id, model);
    updateComposer();
  }

  // ── Lecture & copie ─────────────────────────
  function conversationText(t, onlyLast = false) {
    const msgs = t.data.messages;
    if (onlyLast) {
      const last = [...msgs].reverse().find((m) => m.role === "assistant");
      return last ? splitThinking(last.text).content : "";
    }
    return msgs.map((m) => (m.role === "user"
      ? `Vous :\n${m.text}`
      : `${m.model || "Assistant"} :\n${splitThinking(m.text).content}`)).join("\n\n");
  }

  function speakMode(t, mode) {
    if (!t) return;
    const source = `chat:${t.id}`;
    if (ctx.speaking(source)) { api.tts_stop(); return; }
    const text = mode === "all"
      ? t.data.messages.map((m) => splitThinking(m.text).content).join("\n\n")
      : conversationText(t, true);
    ctx.speak(text, source, true);
  }

  function paneMenu(t, ev) {
    ev.preventDefault();
    const sel = window.getSelection().toString();
    const has = t.data.messages.length > 0;
    const msg = ev.target.closest(".msg");
    openMenu([
      { label: `Modèle : ${effectiveModel(t) || "—"}`, icon: "sparkles", submenu: modelItems(t) },
      "-",
      { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", disabled: !sel, onSelect: () => ctx.copy(sel) },
      msg?.copyMessage ? { label: msg.classList.contains("msg-user") ? "Copier ce message" : "Copier cette réponse",
        icon: "copy", onSelect: msg.copyMessage } : null,
      { label: "Copier toute la conversation", icon: "copy", disabled: !has,
        onSelect: () => ctx.copy(conversationText(t), "Conversation copiée") },
      "-",
      { label: "Lire la sélection", icon: "volume-2", disabled: !sel, onSelect: () => ctx.speak(sel, `chat:${t.id}`) },
      { label: "Lire la dernière réponse", icon: "volume-2", disabled: !has, onSelect: () => speakMode(t, "last") },
      { label: "Tout lire", icon: "volume-2", disabled: !has, onSelect: () => speakMode(t, "all") },
    ], ev.clientX, ev.clientY);
  }

  // ── Évènements du composer ──────────────────
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      send();
    }
  });
  input.addEventListener("input", () => {
    autosize();
    const t = activeTab();
    if (t) t.draft = input.value;
    updateComposer();
  });
  input.addEventListener("paste", async (ev) => {
    const types = [...(ev.clipboardData?.types || [])];
    if (!types.includes("Files")) return;
    const t = activeTab();
    if (!t) return;
    ev.preventDefault();
    const text = ev.clipboardData.getData("text/plain");
    const handled = await api.chat_paste(t.id);
    if (!handled && text) document.execCommand("insertText", false, text);
  });

  // ── Évènements Python ───────────────────────
  on("chat:tab", (data) => {
    const t = S.tabs.get(data.id);
    if (!t) return;
    const previous = t.data.messages.length;
    const thread = t.pane.querySelector(".thread");
    t.data = data;
    if (thread && data.streaming && data.messages.length === previous + 1) {
      // Envoi d'un message : on ajoute la question et la bulle de réponse,
      // sans reconstruire toute la conversation
      thread.append(userMessage(t, data.messages[previous], previous));
      t.partial = { content: "", thinking: "" };
      t.stream = botMessage(t, { text: "", thinking: "", model: data.partial?.model || data.model }, true);
      thread.append(t.stream.root);
      scrollToBottom(t, true);
    } else {
      renderPane(t);
    }
    renderStrip();
    if (t.id === S.active) { renderAttachments(); updateComposer(); }
  });

  on("chat:delta", ({ tab, content, thinking }) => {
    const t = S.tabs.get(tab);
    if (!t || !t.data.streaming || !t.partial) return;
    t.partial.content += content;
    t.partial.thinking += thinking;
    t.renderPartial();
  });

  on("chat:done", ({ tab, message, error, stopped, title }) => {
    const t = S.tabs.get(tab);
    if (!t) return;
    t.data.streaming = false;
    t.data.partial = null;
    t.partial = null;
    const stream = t.stream;
    t.stream = null;
    if (message) {
      t.data.messages.push(message);
      if (stream) stream.update(message.text, message.thinking, false);
      else renderPane(t);   // pas de bulle en cours (interface rechargée entre-temps)
    } else {
      stream?.root.remove();
    }
    if (error) appendNotice(t, error, "error");
    else if (stopped) appendNotice(t, message ? "Réponse interrompue." : "Génération interrompue.");
    if (title !== undefined) t.data.title = title;
    renderStrip();
    if (t.id === S.active) updateComposer();
    autoScroll(t);
  });

  on("chat:attachments", ({ tab, attachments }) => {
    const t = S.tabs.get(tab);
    if (!t) return;
    t.data.attachments = attachments;
    if (t.id === S.active) renderAttachments();
  });

  on("chat:models", ({ models, error, default_model }) => {
    S.models = models || [];
    S.modelsError = error || "";
    if (default_model) S.defaultModel = default_model;
    updateComposer();
    for (const t of S.tabs.values()) {
      if (!t.data.messages.length && !t.data.streaming) renderPane(t);
    }
  });

  on("layout", () => { const t = activeTab(); if (t) autoScroll(t); });

  // Image envoyée depuis l'onglet Captures
  on("chat:attach", ({ paths }) => {
    const t = activeTab();
    if (!t) return;
    api.chat_attach_paths(t.id, paths).then((res) => {
      for (const err of res?.errors || []) toast(err, "error");
    });
  });

  // ── Initialisation ──────────────────────────
  for (const data of state.chat.tabs) {
    const t = makeTab(data);
    S.order.push(t.id);
  }
  select(S.order[0]);

  return {
    el,
    onShow() {
      const t = activeTab();
      if (t) { autoScroll(t); input.focus(); }
    },
    onWindowShown() { input.focus(); },
    onDrop(paths) {
      const t = activeTab();
      if (!t) return;
      api.chat_attach_paths(t.id, paths).then((res) => {
        for (const err of res?.errors || []) toast(err, "error");
      });
    },
  };
}
