// Vue Paramètres.
import { h } from "../dom.js";
import { card, ico, toast, kbdCombo, openMenuBelow } from "../ui.js";

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

  // ── Lecture à voix haute ────────────────────
  const ENGINE_HINTS = {
    edge: "Voix neuronales de Microsoft, les plus naturelles (celles de « Lire à voix haute » d'Edge). "
      + "Le texte lu est envoyé à Microsoft : il faut une connexion. Sans réponse, la voix hors ligne prend le relais.",
    piper: "Voix neuronale Piper, calculée sur ce PC : rien ne sort. Un peu plus plate.",
    sapi: "Voix installées dans Windows, plus mécaniques.",
  };
  const PREVIEW = "tts-preview";
  const tts = { engines: [], voices: {}, speeds: [], loaded: false };
  const engineSeg = h("div", { class: "segmented" });
  const engineHint = h("div", { class: "hint-text" });
  const voiceBtn = h("button", { class: "btn sm voice-btn", type: "button", onClick: (ev) => voiceMenu(ev.currentTarget) });
  const previewBtn = h("button", { class: "btn sm", type: "button", onClick: preview });
  const speedSeg = h("div", { class: "segmented" });
  const speedText = (v) => `${String(Number(v)).replace(".", ",")}×`;
  const engineOf = () => settings.tts_engine || "edge";
  const voiceKey = () => `tts_voice_${engineOf()}`;

  function currentVoice() {
    const list = tts.voices[engineOf()] || [];
    return list.find((v) => v.id === settings[voiceKey()]) || list[0] || null;
  }

  function renderTts() {
    const engine = engineOf();
    engineSeg.replaceChildren(...tts.engines.map((e) => h("button", {
      type: "button", class: e.id === engine ? "active" : "", text: e.label, disabled: !e.available,
      dataset: { tip: e.available ? null : "Non installé" },
      onClick: async () => { await ctx.saveSetting("tts_engine", e.id); renderTts(); },
    })));
    engineHint.textContent = ENGINE_HINTS[engine] || "";
    const voice = currentVoice();
    voiceBtn.replaceChildren(h("span", { text: voice ? voice.label : tts.loaded ? "Aucune voix" : "…" }),
      voice?.hint ? h("span", { class: "muted", text: voice.hint }) : null, ico("chevron-down", 13));
    voiceBtn.disabled = !(tts.voices[engine] || []).length;
    const current = Number(settings.tts_speed ?? 1);
    speedSeg.replaceChildren(...tts.speeds.map((v) => h("button", {
      type: "button", class: Math.abs(current - v) < 0.01 ? "active" : "", text: speedText(v),
      onClick: async () => { await ctx.saveSetting("tts_speed", v); renderTts(); },
    })));
    renderPreview(store.tts);
  }

  function renderPreview(t) {
    const playing = t?.speaking && t.source === PREVIEW;
    previewBtn.replaceChildren(ico(playing ? "square" : "play", 13), playing ? "Arrêter" : "Écouter");
    previewBtn.disabled = !currentVoice();
  }

  function voiceMenu(anchor) {
    const engine = engineOf();
    const chosen = currentVoice()?.id;
    openMenuBelow((tts.voices[engine] || []).map((v) => ({
      label: v.label, hint: v.hint, checked: v.id === chosen,
      onSelect: async () => { await ctx.saveSetting(voiceKey(), v.id); renderTts(); },
    })), anchor);
  }

  function preview() {
    if (store.tts.speaking && store.tts.source === PREVIEW) { api.tts_stop(); return; }
    const voice = currentVoice();
    if (voice) api.tts_preview(engineOf(), voice.id);
  }

  async function loadVoices() {
    const res = await api.tts_voices();
    if (!res) return;
    Object.assign(tts, res, { loaded: true });
    renderTts();
  }
  ctx.on("tts", renderPreview);

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
  const settingSwitch = (key, label) => {
    const sw = h("button", { class: `switch${settings[key] ? " on" : ""}`, type: "button",
      role: "switch", "aria-checked": String(!!settings[key]), "aria-label": label });
    sw.addEventListener("click", async () => {
      await ctx.saveSetting(key, !settings[key]);
      syncSwitch(sw, settings[key]);
    });
    return sw;
  };
  const printSwitch = settingSwitch("print_screen_capture", "Touche Impr. écran");
  const helpSwitch = settingSwitch("help_capture", "Ctrl+Impr. écran");

  // ── Raccourcis de l'interface ───────────────
  // (3e valeur : a besoin d'Ollama, masqué quand il ne répond pas)
  const shortcuts = [
    ["ctrl+shift+d", "Afficher Boostache (raccourci global, bindings.py)"],
    ["print screen", "Capturer une zone de l'écran (raccourci global, onglet Captures)"],
    ["ctrl+print screen", "Aide contextuelle sur la fenêtre active (raccourci global, Conversations)", true],
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
      card("cpu", "Ollama", "Modèles installés, téléchargement de nouveaux modèles, lancement du serveur, contexte et mémoire.",
        h("button", { class: "btn sm", type: "button", onClick: () => ctx.navigate("ollama") },
          ico("chevron-right", 14), "Ouvrir la section Ollama")),
      needsOllama(card("sparkles", "Pré-prompt système",
        "Envoyé au modèle comme message système au début de chaque conversation.",
        prompt,
        h("div", { class: "card-actions" }, savedFlag, saveBtn))),
      card("clipboard-list", "Presse-papiers",
        "Nombre maximal d'éléments conservés. L'historique reste en mémoire et disparaît à la fermeture.",
        h("div", { class: "row" }, h("span", { class: "muted", text: "Taille maximale de l'historique" }), maxItems)),
      card("square-terminal", "Consoles",
        "Shell utilisé pour les nouvelles consoles. Chaque console peut en changer via son badge.",
        shellSeg),
      card("volume-2", "Lecture à voix haute",
        "Voix des boutons Lire : conversations, notes, consoles, presse-papiers, transcriptions, assistant live.",
        engineSeg, engineHint,
        h("div", { class: "row", style: { marginTop: "16px" } },
          h("span", { class: "muted", text: "Voix" }),
          h("div", { class: "tts-voice" }, voiceBtn, previewBtn)),
        h("div", { class: "row", style: { marginTop: "12px" } },
          h("span", { class: "muted", text: "Vitesse" }), speedSeg)),
      card("palette", "Captures", null,
        h("div", { class: "row" },
          h("div", {}, h("div", {}, "Capturer avec la touche ", ...kbdCombo("print screen")),
            h("div", { class: "hint-text",
              text: "Sélection d'une zone de l'écran, ouverte dans l'onglet Captures. Désactivé, la touche retrouve son effet Windows habituel." })),
          printSwitch),
        h("div", { class: "row", style: { marginTop: "16px" }, dataset: { ollama: "" } },
          h("div", {}, h("div", {}, "Aide contextuelle avec ", ...kbdCombo("ctrl+print screen")),
            h("div", { class: "hint-text",
              text: "Capture la fenêtre active et lit son texte ; un modèle Ollama propose des questions d'aide, puis répond à celle choisie ou écrite, dans Conversations." })),
          helpSwitch)),
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
        h("div", { class: "shortcut-list" }, ...shortcuts.flatMap(([combo, label, ollama]) => [
          h("div", { dataset: { ollama: ollama ? "" : null } }, ...kbdCombo(combo)),
          h("div", { text: label, dataset: { ollama: ollama ? "" : null } })]))),
    )));

  return {
    el,
    // Réglages modifiables ailleurs (panneau « premier plan » de la barre latérale)
    onShow() {
      if (!tts.loaded) loadVoices();
      else renderTts();     // vitesse modifiable depuis le lecteur de la barre latérale
      syncSwitch(topSwitch, settings.always_on_top);
      syncSwitch(printSwitch, settings.print_screen_capture);
      syncSwitch(helpSwitch, settings.help_capture);
    },
  };
}

/** Masqué tant qu'Ollama ne répond pas (règle [data-ollama] de app.css). */
function needsOllama(el) {
  el.dataset.ollama = "";
  return el;
}

function syncSwitch(sw, value) {
  sw.classList.toggle("on", !!value);
  sw.setAttribute("aria-checked", String(!!value));
}
