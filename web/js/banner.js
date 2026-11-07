// Bandeau d'Ollama, au-dessus des sections : arrêté (« Lancer Ollama »), en cours
// de lancement, pas installé, ou installé à moitié par une mise à jour ratée
// (« Installer Ollama » / « Réparer Ollama » : Boostache télécharge l'installateur
// et le lance lui-même, étapes affichées ici). Fermé, il ne revient qu'avec un autre état.

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
  repair: { icon: "loader-circle", text: "" },
};

/** Octets → « 712 Mo », « 1,7 Go ». */
function size(n) {
  if (n < 1e9) return `${Math.max(1, Math.round(n / 1e6))} Mo`;
  return `${(n / 1e9).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Go`;
}

/** Étape de la réparation (ou de l'installation) → texte du bandeau. */
function repairText(r) {
  const what = r.installed ? "Réparation d'Ollama" : "Installation d'Ollama";
  switch (r.phase) {
    case "download": {
      const of = r.total ? ` : ${Math.floor((100 * r.done) / r.total)} % (${size(r.done)} sur ${size(r.total)})` : "";
      return `${what} : téléchargement de l'installateur${r.done ? of : "…"}`;
    }
    case "verify": return `${what} : vérification de l'installateur…`;
    case "stop": return `${what} : fermeture d'Ollama…`;
    case "install": return `${what} : installation en cours (quelques minutes)…`;
    case "start": return `${what} : démarrage d'Ollama…`;
    default: return `${what}…`;
  }
}

export function createOllamaBanner(el) {
  let kind = "";
  let dismissed = "";
  let asked = 0;
  let timer = null;
  let repair = null;

  async function refresh() {
    const ask = ++asked;
    const res = await api.ollama_banner().catch(() => null);
    if (ask !== asked || !res) return;
    repair = res.repair || null;
    set(res.kind || "");
  }

  function set(next) {
    if (next !== dismissed) dismissed = "";
    if (next === "repair" && kind === "repair" && el.dataset.phase === canCancel()) {
      const text = el.querySelector(".ob-text");     // progression : le texte seul (bouton Annuler gardé)
      if (text) text.textContent = repairText(repair || {});
      return;
    }
    const changed = next !== kind || next === "repair";
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
    el.dataset.phase = kind === "repair" ? canCancel() : "";
    if (shown) {
      el.replaceChildren(
        h("span", { class: "ob-icon" }, ico(state.icon, 16)),
        h("span", { class: "ob-text", text: kind === "repair" ? repairText(repair || {}) : state.text }),
        h("div", { class: "ob-actions" }, ...actions()));
    } else {
      el.replaceChildren();
    }
    if (shown !== was) emit("layout");     // sites intégrés replacés sous le bandeau
  }

  /** Réparation : "cancel" tant que le téléchargement peut être annulé. */
  function canCancel() {
    return repair?.phase === "download" || repair?.phase === "prepare" ? "cancel" : "wait";
  }

  function actions() {
    if (kind === "repair") {
      return canCancel() === "cancel"
        ? [h("button", { class: "btn sm", type: "button", onClick: () => api.ollama_repair_cancel() }, "Annuler")]
        : [];
    }
    const close = h("button", { class: "icon-btn sm", type: "button", "aria-label": "Masquer",
      dataset: { tip: "Masquer (revient si l'état d'Ollama change)" },
      onClick: () => { dismissed = kind; render(); } }, ico("x", 14));
    if (kind === "stopped") {
      return [h("button", { class: "btn primary sm", type: "button", onClick: start },
        ico("power", 14), "Lancer Ollama"), close];
    }
    if (kind === "missing") {
      return [h("button", { class: "btn primary sm", type: "button", onClick: () => api.ollama_repair(),
        dataset: { tip: "Boostache télécharge l'installateur d'Ollama et l'installe" } },
        ico("download", 14), "Installer Ollama"), close];
    }
    if (kind === "broken") {
      return [h("button", { class: "btn primary sm", type: "button", onClick: () => api.ollama_repair(),
        dataset: { tip: "Boostache réinstalle Ollama par-dessus : les modèles téléchargés sont conservés" } },
        ico("download", 14), "Réparer Ollama"), close];
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

  on("ollama:repair", (r) => {
    asked++;                               // une réponse d'ollama_banner déjà partie est dépassée
    repair = r;
    if (r) set("repair");
    else refresh();
  });
  for (const event of ["chat:available", "ollama:changed", "ollama:banner", "window:shown"]) on(event, refresh);
  refresh();
  return { refresh };
}
