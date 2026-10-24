// Vue Notes : éditeur de texte multi-onglets, sauvegarde automatique.
// Une note liée à un fichier (Enregistrer dans un fichier, Ouvrir un fichier) le réécrit à chaque
// sauvegarde (enregistrement automatique) ou avec Ctrl+S ; un fichier modifié ailleurs est relu,
// ou signalé si la note a changé aussi.
import { h, debounce, plural } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, openModal, TabStrip, toast } from "../ui.js";
import { fold } from "../palette.js";

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
  const fileBar = h("div", { class: "note-filebar", hidden: true });
  const stack = h("div", { class: "note-stack" });
  const stats = h("span", { class: "note-stats" });
  const saveState = h("span", { class: "save-state" });
  const ttsBtn = iconButton("volume-2", "Lire la note", () => speak(activeTab(), ctx.store.settings.tts_mode_note));
  ttsBtn.dataset.tipIdle = "Lire (clic droit : mode)";
  ttsBtn.addEventListener("contextmenu", (ev) => ctx.ttsModeMenu(ev, "tts_mode_note",
    [["all", "Lire toute la note"], ["sel", "Lire la sélection"]]));
  const fileBtn = h("button", { class: "note-file", type: "button", hidden: true, dataset: { tipWrap: "" },
    onClick: () => { const t = activeTab(); if (t) openMenuBelow(fileItems(t), fileBtn, "right"); } });
  const saveBtn = iconButton("save", "Enregistrer dans un fichier…", () => saveFile(activeTab()), { kbd: "Ctrl+S" });
  const dropOverlay = h("div", { class: "drop-overlay" },
    h("div", { class: "inner" }, ico("file-text", 22), "Déposez pour ouvrir dans une note"));

  const el = h("section", {},
    h("header", { class: "view-head" }, strip.el),
    fileBar,
    stack,
    h("footer", { class: "note-foot" },
      stats, saveState, h("div", { class: "head-spacer" }),
      fileBtn, saveBtn,
      iconButton("folder-open", "Ouvrir un fichier…", openDialog, { kbd: "Ctrl+O" }),
      iconButton("mic", "Dictée vocale", () => dictate(activeTab()), { kbd: "Win+H" }),
      ttsBtn,
      iconButton("copy", "Copier la note", () => copyAll(activeTab()))),
    dropOverlay);

  const activeTab = () => S.tabs.get(S.active);
  /** Enregistrement à la demande, modifications pas encore dans le fichier (sinon le bandeau le dit). */
  const unsaved = (d) => !!d.file && !d.file.auto && d.file.pending && !d.file.state;

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

  /** toFile : le fichier lié aussi, même sans enregistrement automatique (Ctrl+S). */
  async function persist(t, toFile = false) {
    const content = t.editor.value;
    const data = await api.note_save(t.slot, content, toFile);
    if (!data) return;
    t.saved = content;
    applyData(t, data);
    if (t.slot === S.active && !t.save.pending()) setSaveState("saved");
  }

  /** Données de la note reçues de Python : onglet et fichier lié redessinés s'ils ont changé. */
  function applyData(t, data) {
    const key = (d) => `${d.title}|${d.file?.path || ""}|${d.file?.state || ""}|${unsaved(d)}`;
    const changed = key(data) !== key(t.data);
    t.data = data;
    if (changed) renderStrip();
    if (t.slot === S.active) renderFile();
  }

  function setSaveState(kind) {
    const f = activeTab()?.data.file;
    saveState.classList.toggle("warn", kind === "saved" && !!f?.state);
    if (kind === "saved" && f?.state) saveState.replaceChildren(ico("triangle-alert", 13), "Fichier pas à jour");
    else if (kind === "saved" && f?.pending) saveState.replaceChildren("Pas encore dans le fichier (Ctrl+S)");
    else if (kind === "saved") saveState.replaceChildren(ico("check", 13), "Enregistré");
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
      const f = t.data.file;
      // Point à la place de l'icône : modifications pas encore enregistrées dans le fichier
      return { id: slot, title: t.data.title, label: t.data.label, dim: unsaved(t.data),
        icon: f ? "file-text" : undefined, warn: !!f?.state, tip: f?.path };
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
    renderFile();
    updateStats();
    setSaveState(t.save.pending() ? "editing" : "");
    if (ctx.isFocused("notes")) t.editor.focus();
  }

  async function newTab(afterSlot = null) {
    const data = await api.note_new(afterSlot);
    if (!data) return;
    const t = makeTab(data);
    const idx = afterSlot !== null ? S.order.indexOf(afterSlot) : -1;
    if (idx >= 0) S.order.splice(idx + 1, 0, t.slot);
    else S.order.push(t.slot);
    select(t.slot);
    return t;
  }

  // ── Destination d'un envoi (texte d'un enregistrement…) ──
  const isBlank = (t) => !t.editor.value.trim();
  const isFree = (t) => isBlank(t) && !t.data.file;     // une note vide liée à un fichier reste à lui

  ctx.targets.notes = {
    list: () => S.order.map((slot) => S.tabs.get(slot)).filter((t) => !isBlank(t))
      .map((t) => ({ id: t.slot, name: t.data.label || t.data.title || "Note sans titre",
        shown: t.slot === S.active && ctx.isActive("notes") })),
  };

  /** Note choisie, ou pour "new" une note vide (celle affichée de préférence), sinon une nouvelle. */
  async function destination(slot) {
    if (slot !== "new") return S.tabs.get(slot) || null;
    const active = activeTab();
    if (active && isFree(active)) return active;
    return S.order.map((x) => S.tabs.get(x)).find(isFree) || await newTab();
  }

  // Texte envoyé depuis une autre section : ajouté à la fin de la note choisie
  ctx.on("note:insert", async ({ text, slot }) => {
    const t = await destination(slot ?? "new");
    if (!t) { toast("Cette note n'existe plus.", "error"); return; }
    const before = t.editor.value.replace(/\s+$/, "");
    t.editor.value = before ? `${before}\n\n${text}` : text;
    t.save();
    ctx.navigate("notes");
    select(t.slot);
    t.editor.setSelectionRange(t.editor.value.length, t.editor.value.length);
    t.editor.scrollTop = t.editor.scrollHeight;
    toast(`Ajouté à la note « ${t.data.label || t.data.title || "nouvelle note"} »`);
  });

  async function closeTab(slot) {
    const t = S.tabs.get(slot);
    if (!t) return;
    // Note liée : ses dernières modifications d'abord (dans le fichier s'il est en enregistrement automatique)
    if (t.data.file && (t.save.pending() || t.editor.value !== t.saved)) { t.save.cancel(); await persist(t); }
    // Modifications qui ne sont pas dans le fichier : enregistrer ou les perdre, au choix
    if (t.data.file?.pending && !await confirmClose(t)) return;
    t.save.cancel();
    const res = await api.note_close(slot);
    if (!res) return;
    if (res.reset) {
      t.data = res.reset;
      t.editor.value = "";
      t.saved = "";
      updateStats();
      renderFile();
    } else if (res.removed) {
      const idx = S.order.indexOf(slot);
      S.order.splice(idx, 1);
      S.tabs.delete(slot);
      t.editor.remove();
      if (S.active === slot) select(S.order[Math.max(0, idx - 1)]);
    }
    renderStrip();
  }

  /** → true : la note peut être fermée (enregistrée dans son fichier, ou modifications abandonnées). */
  function confirmClose(t) {
    const f = t.data.file;
    const text = f.state === "conflict"
      ? `« ${f.name} » a aussi été modifié en dehors de Boostache. Enregistrer y met la note (l'autre version va à la corbeille) ; sinon les modifications de la note sont perdues.`
      : f.state === "missing"
        ? `« ${f.name} » est introuvable. Enregistrer le recrée avec la note ; sinon ses modifications sont perdues.`
        : `La note a des modifications qui ne sont pas dans « ${f.name} ». Si vous la fermez sans enregistrer, elles seront perdues.`;
    return new Promise((resolve) => {
      let close = false;
      const modal = openModal({
        title: `Enregistrer dans « ${f.name} » ?`,
        body: h("div", { class: "lk-confirm" }, h("p", { text })),
        width: 480,
        onClose: () => resolve(close),
        actions: [
          { label: "Annuler", kind: "ghost" },
          { label: "Fermer sans enregistrer", kind: "ghost danger", onClick: () => { close = true; } },
          { label: "Enregistrer", icon: "save", kind: "primary", onClick: async () => {
            await writeFile(t);
            const after = t.data.file;
            if (after?.state || after?.pending) {
              modal.setError(after.error || "Le fichier n'a pas pu être enregistré.");
              return false;
            }
            close = true;
          } },
        ],
      });
    });
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
      { label: "Ouvrir un fichier…", icon: "folder-open", hint: "Ctrl+O", onSelect: openDialog },
      { label: "Renommer…", icon: "pencil", hint: "F2", onSelect: () => strip.rename(slot) },
      "-",
      ...fileItems(t),
      "-",
      { label: "Copier le contenu", icon: "copy", onSelect: () => copyAll(t) },
      { label: "Tout lire", icon: "volume-2", onSelect: () => speak(t, "all") },
      "-",
      { label: "Fermer la note", icon: "x", onSelect: () => closeTab(slot) },
    ], ev.clientX, ev.clientY);
  }

  // ── Fichier lié ─────────────────────────────
  function fileItems(t) {
    const f = t.data.file;
    if (!f) return [{ label: "Enregistrer dans un fichier…", icon: "save", hint: "Ctrl+S", onSelect: () => saveAs(t) }];
    return [
      { section: f.name },
      { label: "Enregistrement automatique", checked: f.auto, onSelect: () => toggleAuto(t) },
      f.auto ? null : { label: "Enregistrer", icon: "save", hint: "Ctrl+S", onSelect: () => saveFile(t) },
      "-",
      { label: "Afficher dans l'Explorateur", icon: "folder", disabled: f.state === "missing",
        onSelect: () => api.reveal_path(f.path) },
      { label: "Ouvrir avec son application", icon: "external-link", disabled: f.state === "missing",
        onSelect: async () => { if (!await api.files_open(f.path)) toast(`« ${f.name} » est introuvable.`, "error"); } },
      { label: "Copier le chemin", icon: "copy", onSelect: () => ctx.copy(f.path, "Chemin copié") },
      { label: "Enregistrer sous…", icon: "save", onSelect: () => saveAs(t) },
      { label: "Détacher du fichier", icon: "unlink", onSelect: () => unlink(t) },
    ];
  }

  /** Pied de page (fichier lié ou bouton Enregistrer) et bandeau d'un fichier pas à jour. */
  function renderFile() {
    const t = activeTab();
    const f = t?.data.file;
    // Disquette : enregistrer dans un fichier, ou dans le fichier lié sans enregistrement automatique
    saveBtn.hidden = !!f?.auto;
    const saveTip = f ? `Enregistrer dans « ${f.name} »` : "Enregistrer dans un fichier…";
    saveBtn.dataset.tip = saveTip;
    saveBtn.setAttribute("aria-label", saveTip);
    fileBtn.hidden = !f;
    if (f) {
      const pending = unsaved(t.data);
      fileBtn.classList.toggle("warn", !!f.state);
      fileBtn.dataset.tip = f.state ? `Pas à jour : ${f.path}`
        : f.auto ? `Chaque modification de la note est enregistrée dans ${f.path}`
          : pending ? `Modifications pas encore enregistrées dans ${f.path} (Ctrl+S)`
            : `Enregistrée dans ${f.path} — enregistrement à la demande (Ctrl+S)`;
      fileBtn.replaceChildren(ico(f.state ? "triangle-alert" : "file-text", 13), h("span", { text: f.name }),
        ...(pending ? [h("span", { class: "note-file-dot" })] : []));
    }
    renderBar(t);
  }

  function renderBar(t) {
    const f = t?.data.file;
    fileBar.hidden = !f?.state;
    fileBar.dataset.kind = f?.state || "";
    if (!f?.state) { fileBar.replaceChildren(); return; }
    const btn = (icon, label, onClick, { primary = false, tip } = {}) => h("button", {
      class: `btn sm${primary ? " primary" : ""}`, type: "button", dataset: tip ? { tip } : {}, onClick },
    ico(icon, 14), label);
    let text, actions;
    if (f.state === "conflict") {
      text = `« ${f.name} » a été modifié en dehors de Boostache, et la note aussi. Quelle version garder ?`;
      actions = [
        btn("file-text", "Celle du fichier", () => reloadFile(t),
          { tip: "La note reprend le texte du fichier (Ctrl+Z dans la note pour revenir au vôtre)" }),
        btn("notebook-pen", "Celle de la note", () => writeFile(t),
          { tip: "Le fichier est réécrit avec la note ; sa version actuelle va à la corbeille" }),
      ];
    } else if (f.state === "missing") {
      text = `« ${f.name} » est introuvable (déplacé, renommé ou supprimé ?). La note reste dans Boostache.`;
      actions = [
        btn("save", "Recréer le fichier", () => writeFile(t), { primary: true, tip: f.path }),
        btn("save", "Enregistrer sous…", () => saveAs(t)),
        btn("unlink", "Détacher", () => unlink(t), { tip: "La note reste dans Boostache, sans fichier" }),
      ];
    } else {
      text = `« ${f.name} » n'a pas pu être enregistré : ${f.error || "erreur inconnue"}`;
      actions = [
        btn("refresh-cw", "Réessayer", () => writeFile(t), { primary: true }),
        btn("save", "Enregistrer sous…", () => saveAs(t)),
      ];
    }
    fileBar.replaceChildren(
      h("span", { class: "nf-icon" }, ico("triangle-alert", 16)),
      h("span", { class: "nf-text", text }),
      h("div", { class: "nf-actions" }, ...actions));
  }

  /** Ctrl+S : note liée enregistrée tout de suite, sinon fenêtre Enregistrer. */
  async function saveFile(t) {
    if (!t) return;
    if (!t.data.file) { saveAs(t); return; }
    t.save.cancel();
    await persist(t, true);
    const f = t.data.file;
    if (f && !f.state) toast(`Enregistré dans « ${f.name} »`);
  }

  /** Enregistrement automatique dans le fichier, ou à la demande (Ctrl+S). */
  async function toggleAuto(t) {
    const f = t.data.file;
    if (!f) return;
    if (t.save.pending() || t.editor.value !== t.saved) { t.save.cancel(); await persist(t); }
    const data = await api.note_file_auto(t.slot, !f.auto);
    if (!data) return;
    applyData(t, data);
    if (t.slot === S.active) setSaveState("saved");
    toast(data.file.auto
      ? `Enregistrement automatique : chaque modification de la note va dans « ${f.name} ».`
      : `Enregistrement à la demande : Ctrl+S pour enregistrer la note dans « ${f.name} ». Choix gardé pour les prochains fichiers.`,
    "info", 4500);
  }

  async function saveAs(t) {
    if (!t) return;
    t.save.cancel();
    const content = t.editor.value;
    const res = await api.note_save_as(t.slot, content);
    t.saved = content;
    if (!res) return;
    if (res.error) { toast(res.error, "error"); return; }
    applyData(t, res);
    setSaveState("saved");
    toast(`Note enregistrée dans « ${res.file.name} »${followUp(res.file)}`);
  }

  /** Ce qui arrive aux modifications suivantes d'une note qu'on vient de lier. */
  const followUp = (f) => (f.auto ? " : ses modifications y seront enregistrées."
    : " (Ctrl+S pour y enregistrer les modifications suivantes).");

  /** Conflit tranché pour la note, fichier recréé ou nouvel essai. */
  async function writeFile(t) {
    if (t.save.pending() || t.editor.value !== t.saved) { t.save.cancel(); await persist(t); }
    const res = await api.note_file_write(t.slot);
    if (!res) return;
    if (res.error) { toast(res.error, "error"); return; }
    applyData(t, res);
    if (!res.file?.state) { setSaveState("saved"); toast(`Note enregistrée dans « ${res.file.name} »`); }
  }

  /** Conflit tranché pour le fichier : la note reprend son texte (annulable). */
  async function reloadFile(t) {
    t.save.cancel();
    const res = await api.note_file_reload(t.slot);
    if (!res) return;
    if (res.error) { toast(res.error, "error"); return; }
    replaceText(t, res.content, true);
    applyData(t, res);
    updateStats();
    toast("La note a repris le texte du fichier.", "info");
  }

  async function unlink(t) {
    const name = t.data.file?.name;
    const data = await api.note_unlink(t.slot);
    if (!data) return;
    applyData(t, data);
    setSaveState("");
    toast(`La note n'est plus enregistrée dans « ${name} » (le fichier reste où il est).`, "info");
  }

  /** Texte remplacé ; undoable : par la saisie, pour que Ctrl+Z ramène l'ancien. */
  function replaceText(t, text, undoable = false) {
    const { editor } = t;
    if (undoable && t.slot === S.active && ctx.isActive("notes")) {
      editor.focus();
      editor.select();
      document.execCommand(text ? "insertText" : "delete", false, text);
      editor.setSelectionRange(0, 0);
      editor.scrollTop = 0;
    } else {
      const pos = editor.selectionStart;
      const top = editor.scrollTop;
      editor.value = text;
      editor.setSelectionRange(Math.min(pos, text.length), Math.min(pos, text.length));
      editor.scrollTop = top;
    }
    t.saved = text;
  }

  /** Fichiers des notes liées revus : relus s'ils ont changé ailleurs, conflit signalé sinon. */
  let checking = false;
  async function checkFiles() {
    if (checking || ![...S.tabs.values()].some((t) => t.data.file)) return;
    checking = true;
    const dirty = [...S.tabs.values()].filter((t) => t.save.pending() || t.editor.value !== t.saved)
      .map((t) => t.slot);
    let items;
    try { items = await api.note_check(dirty); } finally { checking = false; }
    for (const d of items || []) {
      const t = S.tabs.get(d.slot);
      if (!t) continue;
      if (d.reloaded) {
        if (t.editor.value !== t.saved) continue;     // saisie arrivée entre-temps : rien d'écrasé
        replaceText(t, d.content);
        if (t.slot === S.active) updateStats();
        toast(`« ${d.file.name} » modifié en dehors de Boostache : note mise à jour.`, "info");
      }
      applyData(t, d);
    }
  }
  window.addEventListener("focus", () => { if (ctx.isActive("notes")) checkFiles(); });
  ctx.on("window:shown", () => { if (ctx.isActive("notes")) checkFiles(); });

  // ── Ouvrir des fichiers ─────────────────────
  const reusable = () => { const t = activeTab(); return t && isFree(t) ? t.slot : null; };

  async function openDialog() {
    showOpened(await api.note_open_dialog(reusable(), S.active));
  }

  async function openPaths(paths) {
    showOpened(await api.note_open_paths(paths, reusable(), S.active));
  }

  function showOpened(res) {
    if (!res) return;
    for (const err of res.errors || []) toast(err, "error");
    let last = null;
    for (const d of res.notes || []) {
      let t = S.tabs.get(d.slot);
      if (!t) t = makeTab(d);
      else if (d.how !== "existing") {           // note vide remplie
        t.save.cancel();
        t.editor.value = d.content;
        t.saved = d.content;
      }
      t.data = d;
      last = t;
      if (d.how === "opened") toast(`« ${d.file.name} » ouvert${d.file.auto
        ? " : les modifications de la note y seront enregistrées." : " (Ctrl+S pour y enregistrer la note)."}`);
      else if (d.how === "imported") toast(`Texte de « ${d.label} » copié dans une note (le document n'est pas modifié).`, "info");
      else toast(`« ${d.file.name} » est déjà ouvert dans une note.`, "info");
    }
    S.order = res.order.filter((slot) => S.tabs.has(slot));
    if (!last) return;
    ctx.navigate("notes");
    select(last.slot);
    last.editor.setSelectionRange(0, 0);
    last.editor.scrollTop = 0;
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

  // ── Clavier ─────────────────────────────────
  document.addEventListener("keydown", (ev) => {
    if (!ctx.isFocused("notes") || !ev.ctrlKey || ev.altKey || ev.shiftKey
      || document.querySelector(".modal-backdrop")) return;
    const key = ev.key.toLowerCase();
    if (key === "s") saveFile(activeTab());
    else if (key === "o") openDialog();
    else return;
    ev.preventDefault();
  });

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

  /** Recherche (Ctrl+K) : la note, texte trouvé sélectionné ; actions "new" (nouvelle note), "open" (fichier). */
  function reveal(target, words) {
    if (target?.action === "new") { newTab(S.active); return; }
    if (target?.action === "open") { openDialog(); return; }
    const t = S.tabs.get(target?.slot);
    if (!t) return;
    select(t.slot);
    const text = t.editor.value;
    let pos = Math.min(Math.max(0, Number(target.pos) || 0), text.length);
    // Texte modifié depuis la recherche : le premier mot cherché, là où il est maintenant
    const folded = fold(text);
    const word = words.find((w) => folded.startsWith(w, pos)) || words[0] || "";
    if (word && !folded.startsWith(word, pos)) pos = Math.max(0, folded.indexOf(word));
    t.editor.focus();
    // Le curseur posé d'abord seul : la zone de texte défile jusqu'à lui
    t.editor.setSelectionRange(pos, pos);
    t.editor.blur();
    t.editor.focus();
    t.editor.setSelectionRange(pos, pos + (folded.startsWith(word, pos) ? word.length : 0));
    updateStats();
  }

  return {
    el,
    reveal,
    onShow() { updateStats(); checkFiles(); },
    onHide() { for (const t of S.tabs.values()) t.save.flush(); },
    /** Fichiers glissés sur les notes : ouverts chacun dans une note. */
    onDrop(paths) { openPaths(paths); },
    focus() { activeTab()?.editor.focus(); },
  };
}
