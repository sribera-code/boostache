// Bandeau d'Ollama, au-dessus des sections : arrêté (« Lancer Ollama »), en cours
// de lancement, pas installé, ou installé à moitié par une mise à jour ratée
// (« Télécharger l'installateur »). Fermé, il ne revient qu'avec un autre état.

import { api, emit, on } from "./bridge.js";
import { h } from "./dom.js";
import { ico, toast } from "./ui.js";

const RECHECK_EVERY = 30_000;     // installation absente ou incomplète : réparée entre-temps ? (ms)

const STATES = {
  stopped: {
    icon: "triangle-alert",
    text: "Ollama n'est pas lancé : conversations, assistant, résumés et suggestions sont indisponibles.",
  },
  starting: { icon: "loader-circle", text: "Démarrage d'Ollama…" },
  missing: {
    icon: "triangle-alert",
    text: "Ollama n'est pas installé : les fonctions d'IA de Boostache en ont besoin.",
  },
  broken: {
    icon: "triangle-alert",
    text: "L'installation d'Ollama est incomplète (une mise à jour s'est mal terminée) : aucun modèle ne peut se charger.",
  },
};

export function createOllamaBanner(el) {
  let kind = "";
  let dismissed = "";
  let asked = 0;
  let timer = null;

  async function refresh() {
    const ask = ++asked;
    const res = await api.ollama_banner().catch(() => null);
    if (ask !== asked || !res) return;
    set(res.kind || "");
  }

  function set(next) {
    if (next !== dismissed) dismissed = "";
    const changed = next !== kind;
    kind = next;
    if (changed) render();
    clearInterval(timer);
    timer = kind === "missing" || kind === "broken" ? setInterval(refresh, RECHECK_EVERY) : null;
  }

  function render() {
    const state = STATES[kind];
    const shown = !!state && kind !== dismissed;
    const was = !el.hidden;
    el.hidden = !shown;
    el.dataset.kind = shown ? kind : "";
    if (shown) {
      el.replaceChildren(
        h("span", { class: "ob-icon" }, ico(state.icon, 16)),
        h("span", { class: "ob-text", text: state.text }),
        h("div", { class: "ob-actions" }, ...actions()));
    } else {
      el.replaceChildren();
    }
    if (shown !== was) emit("layout");     // sites intégrés replacés sous le bandeau
  }

  function actions() {
    const close = h("button", { class: "icon-btn sm", type: "button", "aria-label": "Masquer",
      dataset: { tip: "Masquer (revient si l'état d'Ollama change)" },
      onClick: () => { dismissed = kind; render(); } }, ico("x", 14));
    if (kind === "stopped") {
      return [h("button", { class: "btn primary sm", type: "button", onClick: start },
        ico("power", 14), "Lancer Ollama"), close];
    }
    if (kind === "missing") {
      return [h("button", { class: "btn primary sm", type: "button", onClick: () => api.ollama_install_page() },
        ico("external-link", 14), "Installer Ollama"), close];
    }
    if (kind === "broken") {
      return [h("button", { class: "btn primary sm", type: "button", onClick: reinstall,
        dataset: { tip: "Réinstaller par-dessus répare Ollama : les modèles téléchargés sont conservés" } },
        ico("download", 14), "Télécharger l'installateur"), close];
    }
    return [];
  }

  async function start() {
    set("starting");
    const res = await api.ollama_start().catch((err) => ({ ok: false, error: String(err) }));
    if (!res?.ok) {
      toast(res?.error || "Lancement d'Ollama impossible.", "error");
      refresh();
    }
  }

  async function reinstall() {
    if (await api.ollama_installer()) {
      toast("Lance OllamaSetup.exe une fois téléchargé : il répare Ollama sans toucher aux modèles.", "info", 6000);
    }
  }

  for (const event of ["chat:available", "ollama:changed", "ollama:banner", "window:shown"]) on(event, refresh);
  refresh();
  return { refresh };
}
