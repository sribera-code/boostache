// Zone de saisie des sections qui dialoguent avec un modèle (Conversations, Assistant,
// Documents, Fichiers, Calculatrice, WhatsApp, Gmail), et suggestions au-dessus
// (createSuggestions) : le champ s'agrandit avec le texte ; dessous, les outils
// de la section à gauche puis, à droite, la lecture à voix haute — interrupteur
// « Lecture auto » (chaque réponse lue au fur et à mesure qu'elle s'écrit) et bouton
// qui lit toutes les réponses ou toute la conversation (clic droit : choisir) — et le
// bouton d'envoi, qui arrête la réponse en cours.
import { h } from "./dom.js";
import { ico, iconButton } from "./ui.js";

const READ_KEY = "tts_mode_answers";
const READ_MODES = [["answers", "Lire toutes les réponses"], ["all", "Lire toute la conversation"]];

/** Ce que lit le bouton : "answers" (les réponses) ou "all" (les demandes aussi). */
export const readMode = (store) => (store.settings[READ_KEY] === "all" ? "all" : "answers");

/**
 * Texte lu d'une conversation : turns [{ ask, answer }] dans l'ordre (une demande, une
 * réponse ou les deux) ; mode "all" : demandes comprises, annoncées. Tant que la dernière
 * réponse s'écrit, il ne fait que s'allonger (lecture au fil de l'eau).
 */
export function conversationSpeech(turns, mode) {
  const parts = [];
  for (const { ask, answer } of turns) {
    if (mode === "all" && ask?.trim()) parts.push(`Demande : ${ask.trim()}`);
    if (answer?.trim()) parts.push(mode === "all" ? `Réponse : ${answer.trim()}` : answer.trim());
  }
  return parts.join("\n\n");
}

/**
 * Suggestions au-dessus de la zone de saisie d'une conversation, toujours proposées (même
 * quand la conversation n'est pas vide) : set(items, disabled) les remplace.
 * item : { icon, label, tip, main, ask, fill, run } — ask : demande envoyée par onAsk(texte) ;
 * fill : texte mis dans le champ, à compléter (« … » sélectionné, Tab : le « … » suivant) ;
 * run() : action de la section (résumer un fichier…).
 */
export function createSuggestions(composer, onAsk) {
  const { input } = composer;
  const el = h("div", { class: "pane-suggest", role: "group", "aria-label": "Suggestions", hidden: true });

  function use(item) {
    if (item.run) { item.run(); return; }
    if (item.ask) { onAsk(item.ask); return; }
    composer.setValue(item.fill);
    input.focus();
    const i = item.fill.indexOf("…");
    if (i >= 0) input.setSelectionRange(i, i + 1);
    else input.setSelectionRange(item.fill.length, item.fill.length);
  }

  input.addEventListener("keydown", (ev) => {
    if (ev.key !== "Tab" || ev.shiftKey || ev.ctrlKey || !input.value.includes("…")) return;
    ev.preventDefault();
    let i = input.value.indexOf("…", input.selectionEnd);
    if (i < 0) i = input.value.indexOf("…");
    input.setSelectionRange(i, i + 1);
  });

  return {
    el,
    set(items, disabled = false) {
      el.hidden = !items.length;
      el.replaceChildren(...items.map((c) => h("button", { class: `pane-chip${c.main ? " pc-main" : ""}`, type: "button",
        disabled, dataset: { tip: c.tip || (c.fill ? "À compléter dans le champ (Tab : « … » suivant)" : c.ask) },
        onClick: () => use(c) }, ico(c.icon, 13), c.label)));
    },
  };
}

/**
 * o.label, o.placeholder, o.maxlength, o.maxHeight : le champ
 * o.top : au-dessus du champ (pièces jointes) ; o.tools : à gauche sous le champ
 *   (micro, modèle…) ; o.extra : à droite, avant la lecture
 * o.speakKey : réglage de la lecture automatique (la section lance la lecture des
 *   réponses) ; o.onAutoRead(on) : interrupteur actionné
 * o.read : { source, match(source), speak(mode) } — lecture de toute la conversation ;
 *   match : une lecture de cette conversation (le bouton l'arrête)
 * o.onSend() : Entrée ou bouton ; o.onStop() : bouton pendant une réponse
 * o.hasContent(texte) : de quoi envoyer (par défaut : du texte)
 * o.sendTip, o.stopTip : infobulles du bouton d'envoi
 */
export function createComposer(ctx, o) {
  const { api, store } = ctx;
  const S = { busy: false, ready: false, canStop: true, sendTip: o.sendTip || "Envoyer", stopTip: o.stopTip || "Arrêter" };
  const maxHeight = o.maxHeight || 260;
  const hasContent = () => (o.hasContent ? o.hasContent(input.value) : !!input.value.trim());

  const input = h("textarea", { rows: "1", spellcheck: "true", "aria-label": o.label,
    placeholder: o.placeholder || "", maxlength: o.maxlength ? String(o.maxlength) : null });
  const autoBtn = h("button", { class: "read-auto", type: "button", role: "switch",
    "aria-label": "Lecture automatique des réponses", onClick: toggleAuto },
    h("span", { class: "read-auto-track" }), h("span", { class: "read-auto-label", text: "Lecture auto" }));
  const readBtn = iconButton("volume-2", "", () => (speakingHere() ? api.tts_stop() : o.read.speak(readMode(store))));
  readBtn.dataset.speakSource = o.read.source || "";
  readBtn.speakMatch = o.read.match;        // état du bouton (lecteur, main.js)
  readBtn.addEventListener("contextmenu", (ev) => ctx.ttsModeMenu(ev, READ_KEY, READ_MODES));
  readBtn.addEventListener("pointerenter", readTip);
  readBtn.addEventListener("focus", readTip);
  const sendBtn = h("button", { class: "send-btn", type: "button", disabled: true,
    onClick: () => (S.busy ? o.onStop?.() : o.onSend()) }, ico("arrow-up", 17, 2.2));
  const bar = h("div", { class: "composer-bar" }, o.tools || [], h("div", { class: "head-spacer" }), o.extra || [],
    autoBtn, readBtn, sendBtn);
  const el = h("div", { class: "composer" }, o.top || null, input, bar);

  /** Hauteur du champ d'après son texte (ou son texte d'aide, champ vide). */
  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, maxHeight)}px`;
  }

  // Largeur changée (section affichée, fenêtre ou volet redimensionné) : le texte se
  // replie autrement, le champ est remesuré — au frame suivant, pas pendant la mesure
  let barWidth = 0;
  new ResizeObserver(([entry]) => {
    if (entry.contentRect.width === barWidth) return;
    barWidth = entry.contentRect.width;
    if (barWidth) requestAnimationFrame(autosize);
  }).observe(bar);

  function syncSend() {
    const stop = S.busy && !!o.onStop && S.canStop;
    sendBtn.replaceChildren(ico(S.busy ? "square" : "arrow-up", S.busy ? 14 : 17, 2.2));
    sendBtn.disabled = S.busy ? !stop : !S.ready || !hasContent();
    const tip = !S.busy ? S.sendTip : stop ? S.stopTip : "Réponse en cours…";
    sendBtn.dataset.tip = tip;
    sendBtn.setAttribute("aria-label", tip);
    if (S.busy) delete sendBtn.dataset.kbd; else sendBtn.dataset.kbd = "Entrée";
  }

  input.addEventListener("input", () => { autosize(); syncSend(); });
  input.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || ev.shiftKey || ev.isComposing) return;
    ev.preventDefault();
    if (S.busy || !sendBtn.disabled) o.onSend();
  });

  // ── Lecture ──
  const autoRead = () => !!store.settings[o.speakKey];
  const speakingHere = () => !!store.tts.speaking && !!o.read.match(store.tts.source || "");

  function syncAuto() {
    const on = autoRead();
    autoBtn.classList.toggle("on", on);
    autoBtn.setAttribute("aria-checked", String(on));
    autoBtn.dataset.tip = on
      ? "Lecture automatique : chaque réponse est lue à voix haute pendant qu'elle s'écrit — cliquer pour l'arrêter"
      : "Lire automatiquement chaque réponse à voix haute, au fur et à mesure qu'elle s'écrit";
  }

  function toggleAuto() {
    const on = !autoRead();
    store.settings[o.speakKey] = on;
    syncAuto();
    ctx.saveSetting(o.speakKey, on);
    o.onAutoRead?.(on);
  }

  /** Infobulle du bouton Lire : ce qu'il lit (mode choisi par clic droit, commun aux sections). */
  function readTip() {
    const label = READ_MODES.find(([mode]) => mode === readMode(store))[1];
    readBtn.dataset.tipIdle = `${label} (clic droit : choisir)`;
    if (!readBtn.classList.contains("speaking")) readBtn.dataset.tip = readBtn.dataset.tipIdle;
    readBtn.setAttribute("aria-label", label);
  }

  ctx.on("setting", ({ key }) => { if (key === o.speakKey) syncAuto(); });
  syncAuto();
  readTip();

  return {
    el, input, readBtn,
    autosize,
    autoRead,
    /** Texte du champ remplacé (vidé après un envoi). */
    setValue(text) {
      input.value = text;
      autosize();
      syncSend();
    },
    /** Texte d'aide du champ vide ; le champ s'agrandit s'il tient sur plusieurs lignes. */
    setPlaceholder(text) {
      if (input.placeholder === text) return;
      input.placeholder = text;
      if (!input.value) autosize();
    },
    /** busy : réponse en cours (le bouton l'arrête si canStop) ; ready : envoi possible. */
    update(state) {
      Object.assign(S, state);
      syncSend();
    },
  };
}
