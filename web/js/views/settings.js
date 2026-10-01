// Vue Paramètres.
import { h } from "../dom.js";
import { ico, toast, kbdCombo } from "../ui.js";

function card(iconName, title, desc, ...content) {
  return h("div", { class: "card" },
    h("div", { class: "card-head" },
      h("div", { class: "card-icon" }, ico(iconName, 16)),
      h("div", { class: "card-title" }, h("h3", { text: title }), desc ? h("p", { class: "desc", text: desc }) : null)),
    h("div", { class: "card-body" }, ...content));
}

export function createSettingsView(ctx, state) {
  const { api, store } = ctx;
  const settings = store.settings;

  // ── Pré-prompt système ──────────────────────
  const prompt = h("textarea", { class: "field", rows: "7", spellcheck: "true",
    placeholder: "Ex. : Tu es un assistant concis. Réponds en français." });
  prompt.value = settings.system_prompt || "";
  const savedFlag = h("span", { class: "saved-flag", hidden: true }, ico("check", 14), "Enregistré");
  const saveBtn = h("button", { class: "btn primary sm", type: "button", onClick: savePrompt }, "Enregistrer");
  const refreshSaveBtn = () => { saveBtn.disabled = prompt.value === (settings.system_prompt || ""); };
  prompt.addEventListener("input", () => { savedFlag.hidden = true; refreshSaveBtn(); });
  prompt.addEventListener("keydown", (ev) => {
    if (ev.key.toLowerCase() === "s" && ev.ctrlKey) { ev.preventDefault(); savePrompt(); }
  });
  refreshSaveBtn();

  async function savePrompt() {
    await ctx.saveSetting("system_prompt", prompt.value.trim());
    prompt.value = settings.system_prompt || "";
    savedFlag.hidden = false;
    refreshSaveBtn();
    setTimeout(() => { savedFlag.hidden = true; }, 2200);
  }

  // ── Presse-papiers ──────────────────────────
  const maxItems = h("input", { class: "field", type: "number", min: "1", max: "10000",
    value: String(settings.clipboard_max_items ?? 100) });
  const saveMax = async () => {
    const saved = await ctx.saveSetting("clipboard_max_items", maxItems.value);
    maxItems.value = String(saved ?? settings.clipboard_max_items);
  };
  maxItems.addEventListener("change", saveMax);
  maxItems.addEventListener("keydown", (ev) => { if (ev.key === "Enter") maxItems.blur(); });

  // ── Consoles ────────────────────────────────
  const shells = state.consoles.shells || {};
  const shellSeg = h("div", { class: "segmented" });
  const renderShells = () => shellSeg.replaceChildren(...Object.entries(shells).map(([id, label]) =>
    h("button", { type: "button", class: settings.console_shell === id ? "active" : "", text: label,
      onClick: async () => { await ctx.saveSetting("console_shell", id); renderShells(); } })));
  renderShells();

  // ── Fenêtre ─────────────────────────────────
  const topSwitch = h("button", { class: `switch${settings.always_on_top ? " on" : ""}`, type: "button",
    role: "switch", "aria-checked": String(!!settings.always_on_top), "aria-label": "Toujours au premier plan" });
  topSwitch.addEventListener("click", async () => {
    const value = !settings.always_on_top;
    await ctx.saveSetting("always_on_top", value);
    topSwitch.classList.toggle("on", !!settings.always_on_top);
    topSwitch.setAttribute("aria-checked", String(!!settings.always_on_top));
  });

  // ── Captures ────────────────────────────────
  const printSwitch = h("button", { class: `switch${settings.print_screen_capture ? " on" : ""}`, type: "button",
    role: "switch", "aria-checked": String(!!settings.print_screen_capture), "aria-label": "Touche Impr. écran" });
  printSwitch.addEventListener("click", async () => {
    await ctx.saveSetting("print_screen_capture", !settings.print_screen_capture);
    printSwitch.classList.toggle("on", !!settings.print_screen_capture);
    printSwitch.setAttribute("aria-checked", String(!!settings.print_screen_capture));
  });

  // ── Raccourcis de l'interface ───────────────
  const shortcuts = [
    ["ctrl+shift+d", "Afficher Boostache (raccourci global, bindings.py)"],
    ["print screen", "Capturer une zone de l'écran (raccourci global, onglet Captures)"],
    ["ctrl+1", "Aller à une section (Ctrl+1 à Ctrl+9)"],
    ["ctrl+,", "Paramètres"],
    ["f2", "Renommer l'onglet sélectionné (ou double-clic)"],
    ["shift+enter", "Nouvelle ligne dans un message"],
    ["ctrl+t", "Console : ouvrir un terminal externe"],
    ["win+h", "Dictée vocale Windows"],
  ];

  const el = h("section", {},
    h("header", { class: "view-head" }, h("span", { class: "view-title", text: "Paramètres" })),
    h("div", { class: "settings" }, h("div", { class: "settings-inner" },
      card("sparkles", "Pré-prompt système",
        "Envoyé au modèle comme message système au début de chaque conversation.",
        prompt,
        h("div", { class: "card-actions" }, savedFlag, saveBtn)),
      card("clipboard-list", "Presse-papiers",
        "Nombre maximal d'éléments conservés. L'historique reste en mémoire et disparaît à la fermeture.",
        h("div", { class: "row" }, h("span", { class: "muted", text: "Taille maximale de l'historique" }), maxItems)),
      card("square-terminal", "Consoles",
        "Shell utilisé pour les nouvelles consoles. Chaque console peut en changer via son badge.",
        shellSeg),
      card("palette", "Captures", null,
        h("div", { class: "row" },
          h("div", {}, h("div", {}, "Capturer avec la touche ", ...kbdCombo("print screen")),
            h("div", { class: "hint-text",
              text: "Sélection d'une zone de l'écran, ouverte dans l'onglet Captures. Désactivé, la touche retrouve son effet Windows habituel." })),
          printSwitch)),
      card("pin", "Fenêtre", null,
        h("div", { class: "row" },
          h("div", {}, h("div", { text: "Toujours au premier plan" }),
            h("div", { class: "hint-text", text: "La fenêtre reste au-dessus des autres applications." })),
          topSwitch)),
      card("folder", "Données", "Conversations, notes, consoles, captures et réglages sont enregistrés ici.",
        h("div", { class: "path-box" }, h("code", { text: store.dataDir, dataset: { tip: store.dataDir } }),
          h("button", { class: "btn sm", type: "button", onClick: () => api.open_data_dir() },
            ico("folder-open", 14), "Ouvrir"))),
      card("keyboard", "Raccourcis clavier", null,
        h("div", { class: "shortcut-list" }, ...shortcuts.flatMap(([combo, label]) => [
          h("div", {}, ...kbdCombo(combo)), h("div", { text: label })]))),
    )));

  // Réglages modifiables ailleurs (panneau « premier plan » de la barre latérale)
  const syncSwitch = (sw, value) => {
    sw.classList.toggle("on", !!value);
    sw.setAttribute("aria-checked", String(!!value));
  };

  return {
    el,
    onShow() {
      syncSwitch(topSwitch, settings.always_on_top);
      syncSwitch(printSwitch, settings.print_screen_capture);
    },
  };
}
