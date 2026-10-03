// Point d'entrée de l'interface : barre latérale, navigation, comportements globaux.
import { api, on, emit, ready, setErrorHandler, onCollect } from "./bridge.js";
import { $, h, isTyping } from "./dom.js";
import { ico, iconButton, toast, closeMenus, openMenu, setSpeakingButton } from "./ui.js";
import { togglePinPanel } from "./pin.js";
import { createLayout } from "./layout.js";
import { createChatView } from "./views/chat.js";
import { createConsolesView } from "./views/consoles.js";
import { createNotesView } from "./views/notes.js";
import { createClipboardView } from "./views/clipboard.js";
import { createCapturesView } from "./views/captures.js";
import { createWhatsAppView, createGmailView } from "./views/webpane.js";
import { createLogsView } from "./views/logs.js";
import { createTasksView } from "./views/tasks.js";
import { createHotkeysView } from "./views/hotkeys.js";
import { createSettingsView } from "./views/settings.js";

const NAV = [
  { id: "chat",      label: "Conversations",  icon: "message-square",  create: createChatView },
  { id: "consoles",  label: "Consoles",       icon: "square-terminal", create: createConsolesView },
  { id: "notes",     label: "Notes",          icon: "notebook-pen",    create: createNotesView },
  { id: "clipboard", label: "Presse-papiers", icon: "clipboard-list",  create: createClipboardView },
  { id: "captures",  label: "Captures",       icon: "palette",         create: createCapturesView },
  { id: "whatsapp",  label: "WhatsApp",       icon: "message-circle",  create: createWhatsAppView },
  { id: "gmail",     label: "Gmail",          icon: "mail",            create: createGmailView },
  "-",
  { id: "logs",      label: "Historique",     icon: "scroll-text",     create: createLogsView },
  { id: "tasks",     label: "Tâches",         icon: "calendar-clock",  create: createTasksView },
  { id: "hotkeys",   label: "Raccourcis",     icon: "keyboard",        create: createHotkeysView },
];
const SETTINGS = { id: "settings", label: "Paramètres", icon: "settings", create: createSettingsView };

const store = { settings: {}, tts: { speaking: false, source: null }, active: null, dataDir: "" };
const views = new Map();          // id → { view, nav, item }
let layout = null;                // une section ou deux côte à côte (layout.js)
let pendingDrop = null;

// ─────────────────────────────────────────────
//  Contexte partagé avec les vues
// ─────────────────────────────────────────────
const ctx = {
  api, on, emit, store, onCollect,

  // (layout n'existe qu'une fois les vues construites)
  navigate: (id) => layout?.navigate(id),
  /** Section affichée (dans l'un des volets). */
  isActive: (id) => !!layout?.isVisible(id),
  /** Section du volet actif : celle qui reçoit le clavier. */
  isFocused: (id) => !!layout?.isFocused(id),

  /** kind "unread" : pastille verte, visible aussi barre latérale repliée. */
  setBadge(id, value, kind = "") {
    const entry = views.get(id);
    if (!entry?.nav) return;
    const slot = entry.nav.querySelector(".nav-extra");
    slot.replaceChildren();
    if (value === "dot") slot.append(h("span", { class: "nav-dot" }));
    else if (typeof value === "number" && value > 0) {
      slot.append(h("span", { class: `nav-badge ${kind}`.trim(), text: value > 999 ? "999+" : String(value) }));
    }
  },

  async saveSetting(key, value) {
    const saved = await api.set_setting(key, value);
    if (saved !== null && saved !== undefined) store.settings[key] = saved;
    return saved;
  },

  async copy(text, message = "Copié dans le presse-papiers") {
    if (!text) return false;
    const ok = await api.copy_text(text);
    if (ok && message) toast(message);
    if (!ok) toast("Copie impossible : le presse-papiers est occupé.", "error");
    return ok;
  },

  speaking: (source) => store.tts.speaking && store.tts.source === source,

  speak(text, source, markdown = false) {
    if (!text || !String(text).trim()) { toast("Rien à lire.", "info"); return; }
    api.speak(text, source, markdown);
  },

  toggleSpeak(text, source, markdown = false) {
    if (ctx.speaking(source)) api.tts_stop();
    else ctx.speak(text, source, markdown);
  },

  /** Menu « mode de lecture » (clic droit sur un bouton 🔊). */
  ttsModeMenu(ev, key, modes) {
    ev.preventDefault();
    const current = store.settings[key];
    openMenu([{ section: "Bouton de lecture" }, ...modes.map(([value, label]) => ({
      label, checked: current === value, onSelect: () => ctx.saveSetting(key, value),
    }))], ev.clientX, ev.clientY);
  },
};

// ─────────────────────────────────────────────
//  Navigation
// ─────────────────────────────────────────────
/** Clic : dans le volet actif ; Ctrl+clic : dans l'autre volet (écran partagé). */
function navItem(item) {
  const el = h("div", {
    class: "nav-item", role: "button", tabindex: "0", dataset: { nav: item.id },
    onClick: (ev) => (ev.ctrlKey ? layout.openBeside(item.id) : layout.navigate(item.id)),
    onKeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); layout.navigate(item.id); } },
  }, ico(item.icon, 17), h("span", { class: "nav-label", text: item.label }), h("span", { class: "nav-extra" }));
  layout.bindNav(el, item.id);
  return el;
}

function buildSidebar() {
  const sidebar = $("#sidebar");
  // Écran partagé : en haut (barre dépliée) ou dans le pied (barre repliée, en colonne)
  const splitButton = () => iconButton("columns-2", "Écran partagé", () => layout.toggle(),
    { size: 15, cls: "sm split-btn", kbd: "Ctrl+Maj+S" });
  const splitButtons = [splitButton(), splitButton()];
  layout.onChange = (on) => {
    for (const btn of splitButtons) {
      btn.classList.toggle("on", on);
      btn.dataset.tip = on ? "Revenir à une seule section" : "Écran partagé — Ctrl+clic sur une section pour l'ouvrir à côté";
      btn.setAttribute("aria-label", on ? "Revenir à une seule section" : "Écran partagé");
    }
  };
  sidebar.append(h("div", { class: "brand" },
    h("div", { class: "brand-mark" }, ico("zap", 15, 2)),
    h("span", { class: "brand-name", text: "Boostache" }), splitButtons[0]));

  let shortcut = 1;
  for (const item of NAV) {
    if (item === "-") { sidebar.append(h("div", { class: "nav-sep" })); continue; }
    const entry = views.get(item.id);
    if (!entry) continue;
    entry.nav = navItem(item);
    entry.shortcut = shortcut++;
    sidebar.append(entry.nav);
  }
  sidebar.append(h("div", { class: "sidebar-spacer" }));
  const settingsEntry = views.get("settings");
  settingsEntry.nav = navItem(SETTINGS);
  sidebar.append(settingsEntry.nav);

  const clock = h("span", { class: "clock" });
  const pin = iconButton("pin", "Garder des fenêtres au premier plan", (ev) => togglePinPanel(ctx, ev.currentTarget),
    { size: 15, cls: "sm" });
  const collapse = h("button", { class: "icon-btn sm collapse-btn", type: "button",
    onClick: () => setCollapsed(!$("#app").classList.contains("collapsed"), true) });
  sidebar.append(h("div", { class: "sidebar-foot" },
    h("span", { class: "status-dot", dataset: { tip: "Boostache est actif" } }),
    h("span", { class: "status-text", text: "Actif" }), clock, splitButtons[1], pin, collapse));

  const tick = () => {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    clock.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    clock.dataset.tip = d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  };
  tick();
  setInterval(tick, 1000);

  function setCollapsed(collapsed, persist) {
    $("#app").classList.toggle("collapsed", collapsed);
    collapse.replaceChildren(ico(collapsed ? "panel-left-open" : "panel-left-close", 15));
    collapse.dataset.tip = collapsed ? "Déplier la barre latérale" : "Replier la barre latérale";
    for (const [id, entry] of views) {
      if (collapsed) entry.nav.dataset.tip = entry.item.label;
      else delete entry.nav.dataset.tip;
      if (collapsed && entry.shortcut) entry.nav.dataset.kbd = `Ctrl+${entry.shortcut}`;
      else delete entry.nav.dataset.kbd;
    }
    if (persist) ctx.saveSetting("sidebar_collapsed", collapsed);
    setTimeout(() => emit("layout"), 200);
  }
  setCollapsed(!!store.settings.sidebar_collapsed, false);
}

/** Ctrl+1…9, Ctrl+, , Ctrl+Maj+S (« split ») et F6 (clavier de l'interface ou relayé
 *  depuis un site intégré). */
function shortcut(key) {
  if (key === ",") { layout.navigate("settings"); return true; }
  if (key === "split") { layout.toggle(); return true; }
  if (key === "F6") { layout.focusOther(); return layout.isSplit(); }
  const target = [...views.values()].find((e) => e.shortcut === Number(key));
  if (target) layout.navigate(target.item.id);
  return !!target;
}

// ─────────────────────────────────────────────
//  Lecture vocale : pastille globale + boutons [data-speak-source]
// ─────────────────────────────────────────────
function setupTts() {
  const pill = h("div", { class: "tts-card", hidden: true },
    h("span", { class: "eq" }, h("i"), h("i"), h("i"), h("i")),
    h("span", { class: "tts-text", text: "Lecture en cours" }),
    h("button", { class: "icon-btn sm", type: "button", "aria-label": "Arrêter la lecture",
      dataset: { tip: "Arrêter la lecture" }, onClick: () => api.tts_stop() }, ico("square", 13)));
  $("#sidebar").insertBefore(pill, $(".sidebar-foot"));

  const refresh = () => {
    pill.hidden = !store.tts.speaking;
    for (const btn of document.querySelectorAll("[data-speak-source]")) {
      setSpeakingButton(btn, ctx.speaking(btn.dataset.speakSource));
    }
  };
  on("tts", (data) => { store.tts = data; refresh(); });
  on("speak-sources-changed", refresh);
  refresh();
}

// ─────────────────────────────────────────────
//  Comportements globaux
// ─────────────────────────────────────────────
function setupGlobalHandlers() {
  // Menu contextuel natif : seulement dans les champs de saisie (correcteur orthographique)
  document.addEventListener("contextmenu", (ev) => {
    if (ev.defaultPrevented) return;
    if (ev.shiftKey && ev.ctrlKey) return;   // outils de développement (mode --debug)
    if (!isTyping(ev.target)) ev.preventDefault();
  });

  // Liens : toujours dans le navigateur, jamais dans la fenêtre
  document.addEventListener("click", (ev) => {
    const copyBtn = ev.target.closest("[data-copy-code]");
    if (copyBtn) {
      const code = (copyBtn.closest(".code-block")?.querySelector("code")?.textContent || "").replace(/\n$/, "");
      ctx.copy(code, null).then((ok) => {
        if (!ok) return;
        const label = copyBtn.querySelector("span");
        label.textContent = "Copié";
        setTimeout(() => { label.textContent = "Copier"; }, 1400);
      });
      return;
    }
    const link = ev.target.closest("a[href]");
    if (link) {
      ev.preventDefault();
      api.open_url(link.getAttribute("href")).then((ok) => {
        if (ok === false) toast("Lien non pris en charge.", "info");
      });
    }
  });
  window.open = (url) => { api.open_url(String(url)); return null; };

  // Glisser-déposer de fichiers : le chemin complet arrive par Python (files:dropped).
  // Écran partagé : les fichiers vont à la section sous le curseur.
  const hasFiles = (ev) => [...(ev.dataTransfer?.types || [])].includes("Files");
  let depth = 0;
  const clearDragging = () => {
    depth = 0;
    document.querySelectorAll(".dragging").forEach((n) => n.classList.remove("dragging"));
    document.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
  };
  const markDragging = (target) => {
    const id = layout.viewAt(target);
    for (const [vid, entry] of views) entry.view.el.classList.toggle("dragging", vid === id);
  };
  document.addEventListener("dragenter", (ev) => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    depth += 1;
    markDragging(ev.target);
  });
  document.addEventListener("dragleave", (ev) => {
    if (!hasFiles(ev)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) clearDragging();
  });
  document.addEventListener("dragover", (ev) => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
    markDragging(ev.target);
    const zone = ev.target.closest?.("[data-drop]");
    document.querySelectorAll(".drop-target").forEach((n) => { if (n !== zone) n.classList.remove("drop-target"); });
    zone?.classList.add("drop-target");
  });
  document.addEventListener("drop", (ev) => {
    if (!hasFiles(ev)) return;      // section glissée depuis la barre latérale (layout.js)
    ev.preventDefault();
    const zone = ev.target.closest?.("[data-drop]")?.dataset.drop || null;
    pendingDrop = { view: layout.viewAt(ev.target), zone, at: Date.now() };
    clearDragging();
  }, true);
  on("files:dropped", ({ paths }) => {
    const drop = pendingDrop;
    pendingDrop = null;
    if (!drop || Date.now() - drop.at > 10000 || !paths?.length) return;
    const view = views.get(drop.view)?.view;
    if (view?.onDrop) view.onDrop(paths, drop.zone);
    else toast("Déposez des fichiers dans Conversations, Consoles ou Captures.", "info");
  });

  // Raccourcis de l'interface
  document.addEventListener("keydown", (ev) => {
    if (ev.ctrlKey && !ev.altKey && !ev.shiftKey && /^[1-9]$/.test(ev.key)) {
      if (shortcut(ev.key)) { ev.preventDefault(); ev.stopPropagation(); }
    } else if (ev.ctrlKey && ev.key === ",") {
      ev.preventDefault();
      shortcut(",");
    } else if (ev.ctrlKey && ev.shiftKey && !ev.altKey && ev.key.toLowerCase() === "s") {
      ev.preventDefault();
      ev.stopPropagation();
      shortcut("split");
    } else if (ev.key === "F6" && !ev.ctrlKey && !ev.altKey && !ev.shiftKey) {
      if (shortcut("F6")) { ev.preventDefault(); ev.stopPropagation(); }
    } else if (ev.key === "F5" && !ev.ctrlKey) {
      ev.preventDefault();   // pas de rechargement accidentel de l'interface
    }
  }, true);

  // Fenêtre masquée : on ne laisse pas l'historique du presse-papiers affiché
  on("window:hidden", () => {
    closeMenus();
    layout.evict("clipboard");
  });
  on("webpane:key", ({ key }) => shortcut(key));
  on("window:shown", () => layout.windowShown());
  on("toast", ({ text, kind }) => toast(text, kind || "info"));
}

// ─────────────────────────────────────────────
//  Démarrage
// ─────────────────────────────────────────────
async function boot() {
  setErrorHandler((msg) => toast(msg, "error"));
  window.__bootStage = "attente de pywebview";
  await ready();
  window.__bootStage = "polices";
  await document.fonts.load('13px "JetBrains Mono"').catch(() => {});
  window.__bootStage = "état initial";
  const state = await window.pywebview.api.ui_ready();
  window.__bootStage = "construction";
  store.settings = state.settings || {};
  store.tts = state.tts || store.tts;
  store.dataDir = state.data_dir;
  store.winBuild = state.win_build;

  const main = $("#main");
  for (const item of [...NAV, SETTINGS]) {
    if (item === "-") continue;
    if (item.id === "chat" && !state.chat?.available) continue;
    const view = item.create(ctx, state);
    view.el.classList.add("view");
    view.el.dataset.view = item.id;
    main.append(view.el);
    views.set(item.id, { view, item, nav: null, shortcut: 0 });
  }
  layout = createLayout(ctx, views, main);
  buildSidebar();
  setupTts();
  setupGlobalHandlers();

  layout.restore(store.settings.layout, state.visible);
  // (pas de requestAnimationFrame : il ne s'exécute pas tant que la fenêtre est masquée)
  $("#app").classList.remove("booting");
  window.__bootStage = "prêt";
}

boot().catch((err) => {
  console.error(err);
  document.body.append(h("pre", { style: { color: "#ee7d7d", padding: "20px", whiteSpace: "pre-wrap" } },
    `Erreur au démarrage de l'interface :\n${err?.stack || err}`));
});
