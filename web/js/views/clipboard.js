// Vue Presse-papiers : historique des copies (en mémoire uniquement).
import { h, escapeHtml, formatTimestamp, plural, isTyping } from "../dom.js";
import { ico, iconButton, openMenu, openModal, toast } from "../ui.js";

export function createClipboardView(ctx, state) {
  const { api, on } = ctx;
  let items = state.clipboard || [];
  let selected = null;
  let query = "";

  const count = h("span", { class: "view-sub" });
  const search = h("input", { type: "search", placeholder: "Rechercher…", spellcheck: "false",
    "aria-label": "Rechercher dans l'historique" });
  const clearBtn = h("button", { class: "btn ghost sm danger", type: "button", onClick: clearAll },
    ico("trash-2", 14), "Tout effacer");
  const list = h("div", { class: "clip-list", role: "listbox", tabindex: "0" });

  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Presse-papiers" }), count,
      h("div", { class: "head-spacer" }),
      h("label", { class: "search" }, ico("search", 14), search),
      clearBtn),
    h("div", { class: "clip-info" }, ico("lock", 13),
      "Conservé en mémoire uniquement — les contenus marqués confidentiels (gestionnaires de mots de passe) sont ignorés."),
    list);

  search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); render(); });
  search.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { search.value = ""; query = ""; render(); list.focus(); }
    if (ev.key === "ArrowDown") { ev.preventDefault(); moveSelection(1); list.focus(); }
  });
  list.addEventListener("contextmenu", (ev) => {
    if (!ev.target.closest(".clip-item")) {
      ev.preventDefault();
      openMenu([{ label: "Tout effacer", icon: "trash-2", danger: true, disabled: !items.length, onSelect: clearAll }],
        ev.clientX, ev.clientY);
    }
  });
  list.addEventListener("keydown", onKey);

  function visible() {
    if (!query) return items;
    return items.filter((it) => it.preview.toLowerCase().includes(query));
  }

  function highlight(text) {
    const safe = escapeHtml(text);
    if (!query) return safe;
    const needle = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return safe.replace(new RegExp(needle, "gi"), (m) => `<mark>${m}</mark>`);
  }

  function row(it) {
    const speakBtn = iconButton("volume-2", "Lire", () => speakItem(it.id), { size: 15, cls: "sm" });
    speakBtn.dataset.speakSource = `clipboard:${it.id}`;
    speakBtn.dataset.tipIdle = "Lire";
    const el = h("div", { class: `clip-item${it.id === selected ? " selected" : ""}`, role: "option",
      dataset: { id: String(it.id) } },
      h("div", {},
        h("div", { class: "clip-meta" },
          h("span", { text: formatTimestamp(it.ts) }),
          h("span", { text: plural(it.length, "caractère", "caractères") }),
          it.lines > 1 ? h("span", { text: plural(it.lines, "ligne", "lignes") }) : null),
        h("div", { class: "clip-text", html: highlight(it.preview) })),
      h("div", { class: "clip-actions" },
        iconButton("copy", "Copier", () => copyItem(it.id), { size: 15, cls: "sm" }),
        speakBtn,
        iconButton("trash-2", "Supprimer", () => api.clip_delete(it.id), { size: 15, cls: "sm" })));
    el.addEventListener("click", () => setSelected(it.id));
    el.addEventListener("dblclick", () => openItem(it.id));
    el.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      setSelected(it.id);
      openMenu([
        { label: "Ouvrir…", icon: "file-text", hint: "Entrée", onSelect: () => openItem(it.id) },
        { label: "Copier", icon: "copy", onSelect: () => copyItem(it.id) },
        { label: "Lire", icon: "volume-2", onSelect: () => speakItem(it.id) },
        { label: "Supprimer", icon: "trash-2", hint: "Suppr", onSelect: () => api.clip_delete(it.id) },
        "-",
        { label: "Tout effacer", icon: "trash-2", danger: true, onSelect: clearAll },
      ], ev.clientX, ev.clientY);
    });
    return el;
  }

  function render() {
    const shown = visible();
    count.textContent = items.length ? plural(items.length, "élément", "éléments") : "";
    clearBtn.disabled = !items.length;
    ctx.setBadge("clipboard", items.length);
    if (!shown.length) {
      list.replaceChildren(h("div", { class: "empty" },
        h("div", { class: "glyph" }, ico(query ? "search" : "clipboard-list", 22)),
        h("h3", { text: query ? "Aucun résultat" : "Rien pour l'instant" }),
        h("p", { text: query ? "Aucune copie ne contient ce texte."
          : "Copiez du texte (Ctrl+C, clic droit → Copier, Win+Maj+S…) pour le retrouver ici." })));
      return;
    }
    list.replaceChildren(...shown.map(row));
    ctx.emit("speak-sources-changed");
  }

  function setSelected(id) {
    selected = id;
    for (const n of list.querySelectorAll(".clip-item")) {
      n.classList.toggle("selected", Number(n.dataset.id) === id);
    }
    list.querySelector(".clip-item.selected")?.scrollIntoView({ block: "nearest" });
  }

  function moveSelection(step) {
    const shown = visible();
    if (!shown.length) return;
    const idx = shown.findIndex((it) => it.id === selected);
    const next = shown[Math.min(shown.length - 1, Math.max(0, idx + step))] || shown[0];
    setSelected(next.id);
  }

  function onKey(ev) {
    if (ev.target !== list) return;
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); moveSelection(ev.key === "ArrowDown" ? 1 : -1); }
    else if (ev.key === "Enter" && selected !== null) { ev.preventDefault(); openItem(selected); }
    else if (ev.key === "Delete" && selected !== null) { ev.preventDefault(); api.clip_delete(selected); }
    else if (ev.key.toLowerCase() === "c" && ev.ctrlKey && selected !== null) { ev.preventDefault(); copyItem(selected); }
  }

  async function copyItem(id) {
    if (await api.clip_copy(id)) toast("Copié dans le presse-papiers");
  }

  async function speakItem(id) {
    const source = `clipboard:${id}`;
    if (ctx.speaking(source)) { api.tts_stop(); return; }
    const text = await api.clip_get(id);
    ctx.speak(text, source);
  }

  function clearAll() {
    if (items.length) api.clip_clear();
  }

  async function openItem(id) {
    const text = await api.clip_get(id);
    if (text == null) { toast("Cet élément n'existe plus.", "info"); return; }
    const item = items.find((it) => it.id === id);
    const viewer = h("pre", { class: "viewer", tabindex: "0", text });
    const source = `clipboard-view:${id}`;
    viewer.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      const sel = window.getSelection().toString();
      openMenu([
        { label: "Copier la sélection", icon: "copy", disabled: !sel, onSelect: () => ctx.copy(sel) },
        { label: "Copier tout", icon: "copy", onSelect: () => ctx.copy(text) },
        "-",
        { label: "Lire la sélection", icon: "volume-2", disabled: !sel, onSelect: () => ctx.speak(sel, source) },
        { label: "Tout lire", icon: "volume-2", onSelect: () => ctx.speak(text, source) },
      ], ev.clientX, ev.clientY);
    });
    openModal({
      title: "Élément du presse-papiers",
      subtitle: item ? `${formatTimestamp(item.ts)} · ${plural(item.length, "caractère", "caractères")}` : "",
      body: viewer,
      width: 680,
      onClose: () => { if (ctx.speaking(source)) api.tts_stop(); },
      actions: [
        { label: "Lire", icon: "volume-2", kind: "ghost", left: true,
          onClick: () => { ctx.toggleSpeak(text, source); return false; } },
        { label: "Fermer", kind: "ghost" },
        { label: "Copier", icon: "copy", kind: "primary",
          onClick: async () => { if (await ctx.copy(text)) return true; return false; } },
      ],
    });
  }

  on("clipboard", (data) => {
    items = data.items || [];
    if (selected !== null && !items.some((it) => it.id === selected)) selected = null;
    render();
  });

  render();

  return {
    el,
    onShow() { if (!isTyping()) list.focus(); },
  };
}
