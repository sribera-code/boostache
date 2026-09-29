// Vue Notes : éditeur de texte multi-onglets, sauvegarde automatique.
import { h, debounce, plural } from "../dom.js";
import { ico, iconButton, openMenu, TabStrip, toast } from "../ui.js";

export function createNotesView(ctx, state) {
  const { api } = ctx;
  const S = { tabs: new Map(), order: [], active: null };

  const strip = new TabStrip({
    onSelect: select,
    onClose: closeTab,
    onNew: () => newTab(),
    onRename: rename,
    onMenu: tabMenu,
    newLabel: "Nouvelle note",
    placeholder: "Note vide",
  });
  const stack = h("div", { class: "note-stack" });
  const stats = h("span", { class: "note-stats" });
  const saveState = h("span", { class: "save-state" });
  const ttsBtn = iconButton("volume-2", "Lire la note", () => speak(activeTab(), ctx.store.settings.tts_mode_note));
  ttsBtn.dataset.tipIdle = "Lire (clic droit : mode)";
  ttsBtn.addEventListener("contextmenu", (ev) => ctx.ttsModeMenu(ev, "tts_mode_note",
    [["all", "Lire toute la note"], ["sel", "Lire la sélection"]]));

  const el = h("section", {},
    h("header", { class: "view-head" }, strip.el),
    stack,
    h("footer", { class: "note-foot" },
      stats, saveState, h("div", { class: "head-spacer" }),
      iconButton("mic", "Dictée vocale", () => dictate(activeTab()), { kbd: "Win+H" }),
      ttsBtn,
      iconButton("copy", "Copier la note", () => copyAll(activeTab()))));

  const activeTab = () => S.tabs.get(S.active);

  function makeTab(data) {
    const editor = h("textarea", { class: "note-editor", spellcheck: "true",
      placeholder: "Commencez à écrire…", "aria-label": "Note" });
    editor.value = data.content || "";
    stack.append(editor);
    const t = { slot: data.slot, data, editor, saved: editor.value };
    t.save = debounce(() => persist(t), 600);
    editor.addEventListener("input", () => {
      t.save();
      if (t.slot === S.active) { updateStats(); setSaveState("editing"); }
    });
    editor.addEventListener("contextmenu", (ev) => editorMenu(t, ev));
    editor.addEventListener("select", () => { if (t.slot === S.active) updateStats(); });
    S.tabs.set(t.slot, t);
    return t;
  }

  async function persist(t) {
    const content = t.editor.value;
    const data = await api.note_save(t.slot, content);
    if (!data) return;
    t.saved = content;
    const titleChanged = data.title !== t.data.title;
    t.data = data;
    if (titleChanged) renderStrip();
    if (t.slot === S.active && !t.save.pending()) setSaveState("saved");
  }

  function setSaveState(kind) {
    if (kind === "saved") saveState.replaceChildren(ico("check", 13), "Enregistré");
    else if (kind === "editing") saveState.replaceChildren("Modification…");
    else saveState.replaceChildren();
  }

  function updateStats() {
    const t = activeTab();
    if (!t) return;
    const text = t.editor.value;
    const words = (text.trim().match(/\S+/g) || []).length;
    const { selectionStart: a, selectionEnd: b } = t.editor;
    const sel = b > a ? ` · ${plural(b - a, "caractère sélectionné", "caractères sélectionnés")}` : "";
    stats.textContent = `${plural(words, "mot", "mots")} · ${plural(text.length, "caractère", "caractères")}${sel}`;
  }

  function renderStrip() {
    strip.render(S.order.map((slot) => {
      const t = S.tabs.get(slot);
      return { id: slot, title: t.data.title, label: t.data.label };
    }), S.active);
  }

  function select(slot) {
    const t = S.tabs.get(slot);
    if (!t) return;
    S.active = slot;
    for (const x of S.tabs.values()) x.editor.classList.toggle("active", x === t);
    ttsBtn.dataset.speakSource = `note:${slot}`;
    ctx.emit("speak-sources-changed");
    renderStrip();
    updateStats();
    setSaveState(t.save.pending() ? "editing" : "");
    if (ctx.isActive("notes")) t.editor.focus();
  }

  async function newTab(afterSlot = null) {
    const data = await api.note_new(afterSlot);
    if (!data) return;
    const t = makeTab(data);
    const idx = afterSlot !== null ? S.order.indexOf(afterSlot) : -1;
    if (idx >= 0) S.order.splice(idx + 1, 0, t.slot);
    else S.order.push(t.slot);
    select(t.slot);
  }

  async function closeTab(slot) {
    const t = S.tabs.get(slot);
    if (!t) return;
    t.save.cancel();
    const res = await api.note_close(slot);
    if (!res) return;
    if (res.reset) {
      t.data = res.reset;
      t.editor.value = "";
      t.saved = "";
      updateStats();
    } else if (res.removed) {
      const idx = S.order.indexOf(slot);
      S.order.splice(idx, 1);
      S.tabs.delete(slot);
      t.editor.remove();
      if (S.active === slot) select(S.order[Math.max(0, idx - 1)]);
    }
    renderStrip();
  }

  async function rename(slot, label) {
    const data = await api.note_rename(slot, label);
    const t = S.tabs.get(slot);
    if (data && t) t.data = data;
    renderStrip();
  }

  function tabMenu(slot, ev) {
    const t = S.tabs.get(slot);
    openMenu([
      { label: "Nouvelle note", icon: "plus", onSelect: () => newTab(slot) },
      { label: "Renommer…", icon: "pencil", hint: "F2", onSelect: () => strip.rename(slot) },
      "-",
      { label: "Copier le contenu", icon: "copy", onSelect: () => copyAll(t) },
      { label: "Tout lire", icon: "volume-2", onSelect: () => speak(t, "all") },
      "-",
      { label: "Fermer la note", icon: "x", onSelect: () => closeTab(slot) },
    ], ev.clientX, ev.clientY);
  }

  // ── Édition ─────────────────────────────────
  const selection = (t) => t.editor.value.slice(t.editor.selectionStart, t.editor.selectionEnd);

  function copyAll(t) {
    if (!t) return;
    if (!t.editor.value.trim()) { toast("La note est vide.", "info"); return; }
    ctx.copy(t.editor.value, "Note copiée");
  }

  function insertText(t, text) {
    t.editor.focus();
    document.execCommand("insertText", false, text);   // conserve l'historique d'annulation
  }

  function editorMenu(t, ev) {
    ev.preventDefault();
    const sel = selection(t);
    const has = !!t.editor.value;
    openMenu([
      { label: "Couper", icon: "scissors", hint: "Ctrl+X", disabled: !sel,
        onSelect: async () => { if (await ctx.copy(sel, null)) { t.editor.focus(); document.execCommand("delete"); } } },
      { label: "Copier", icon: "copy", hint: "Ctrl+C", disabled: !sel, onSelect: () => ctx.copy(sel, null) },
      { label: "Coller", icon: "clipboard-paste", hint: "Ctrl+V",
        onSelect: async () => { const text = await api.read_clipboard(); if (text) insertText(t, text); } },
      { label: "Tout sélectionner", icon: "text-select", hint: "Ctrl+A", disabled: !has,
        onSelect: () => { t.editor.focus(); t.editor.select(); } },
      "-",
      { label: "Lire la sélection", icon: "volume-2", disabled: !sel, onSelect: () => ctx.speak(sel, `note:${t.slot}`) },
      { label: "Tout lire", icon: "volume-2", disabled: !has, onSelect: () => speak(t, "all") },
      { label: "Dictée vocale", icon: "mic", hint: "Win+H", onSelect: () => dictate(t) },
      "-",
      { label: "Tout effacer", icon: "trash-2", danger: true, disabled: !has,
        onSelect: () => { t.editor.focus(); t.editor.select(); document.execCommand("delete"); } },
    ], ev.clientX, ev.clientY);
  }

  function speak(t, mode) {
    if (!t) return;
    const source = `note:${t.slot}`;
    if (ctx.speaking(source)) { api.tts_stop(); return; }
    const text = mode === "sel" ? selection(t) : t.editor.value;
    ctx.speak(text, source);
  }

  function dictate(t) {
    if (!t) return;
    t.editor.focus();
    api.dictate();
  }

  ctx.onCollect((st) => {
    for (const t of S.tabs.values()) {
      if (t.editor.value !== t.saved) st.notes[t.slot] = t.editor.value;
    }
  });

  for (const data of state.notes.tabs) {
    const t = makeTab(data);
    S.order.push(t.slot);
  }
  select(S.order[0]);

  return {
    el,
    onShow() { activeTab()?.editor.focus(); updateStats(); },
    onHide() { for (const t of S.tabs.values()) t.save.flush(); },
    onWindowShown() { activeTab()?.editor.focus(); },
  };
}
