// Vues des sites intégrés (WhatsApp, Gmail) : le site tourne dans un contrôle
// WebView2 natif (webpane.py), posé par Python au-dessus de la zone .pane-host
// dont on transmet la position. Sous le site, l'assistant de réponse (Ollama).
import { h, plural } from "../dom.js";
import { ico, iconButton, openMenuBelow } from "../ui.js";

const WHATSAPP = {
  id: "whatsapp", title: "WhatsApp", icon: "message-circle",
  unread: ["discussion non lue", "discussions non lues"],
  firstRun: "Première ouverture : scannez le QR code avec WhatsApp sur votre téléphone (Appareils connectés). La session est ensuite conservée.",
  hint: "Consigne (facultatif) : plus formel, décline poliment…",
};
const GMAIL = {
  id: "gmail", title: "Gmail", icon: "mail",
  unread: ["message non lu", "messages non lus"],
  firstRun: "Première ouverture : connectez-vous à votre compte Google. La session est ensuite conservée.",
  hint: "Consigne (facultatif) : accepte mardi 14 h, plus formel…",
  long: true,      // propositions = e-mails complets, affichés en entier
};

export const createWhatsAppView = (ctx, state) => createPaneView(ctx, state, WHATSAPP);
export const createGmailView = (ctx, state) => createPaneView(ctx, state, GMAIL);

function createPaneView(ctx, state, cfg) {
  const { api, on } = ctx;
  const site = { state: "idle", message: "", unread: 0, ...(state.panes?.[cfg.id] || {}) };
  let active = false;
  let covered = false;       // menu, panneau ou modale ouverts : le contrôle natif les masquerait
  let lastSent = "";

  const sub = h("span", { class: "view-sub" });
  const status = h("div", { class: "empty" });
  const host = h("div", { class: "pane-host" }, status);
  const assist = createAssistBar(ctx, state, cfg);
  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: cfg.title }), sub,
      h("div", { class: "head-spacer" }),
      iconButton("refresh-cw", `Recharger ${cfg.title}`, () => api.pane_reload(cfg.id))),
    host,
    assist.el);

  function render() {
    sub.textContent = site.unread ? plural(site.unread, ...cfg.unread) : "";
    ctx.setBadge(cfg.id, site.unread, "unread");
    assist.setReady(site.state === "ready");
    if (site.state === "error") {
      status.replaceChildren(
        h("div", { class: "glyph" }, ico("triangle-alert", 22)),
        h("h3", { text: `${cfg.title} indisponible` }),
        h("p", { text: site.message || "Le chargement a échoué." }),
        h("div", { class: "empty-actions" },
          h("button", { class: "btn", type: "button", onClick: () => api.pane_reload(cfg.id) },
            ico("refresh-cw", 14), "Réessayer")));
    } else if (site.state === "ready") {
      status.replaceChildren();   // visible seulement sous un menu ouvert
    } else {
      status.replaceChildren(
        h("div", { class: "glyph" }, ico(cfg.icon, 22)),
        h("h3", { text: `Chargement de ${cfg.title}…` }),
        h("p", { text: cfg.firstRun }));
    }
  }

  // Position du contrôle natif, en pixels physiques de la fenêtre.
  // focus : à l'arrivée sur la section seulement (pas à la fermeture d'un menu)
  function sync(focus = false) {
    let next = "hidden";
    if (active && !covered) {
      const r = host.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const k = window.devicePixelRatio || 1;
      const x = Math.floor(r.left * k), y = Math.floor(r.top * k);
      next = [x, y, Math.ceil(r.right * k) - x, Math.ceil(r.bottom * k) - y].join(",");
    }
    if (next === lastSent) return;
    lastSent = next;
    if (next === "hidden") api.pane_hide(cfg.id);
    else api.pane_show(cfg.id, ...next.split(",").map(Number), focus === true);
  }

  // Menus, panneaux et modales qui chevauchent le site (écran partagé : un menu
  // de l'autre volet le laisse affiché)
  function overlapped() {
    const r = host.getBoundingClientRect();
    return [...document.querySelectorAll("body > .menu, body > .popover, body > .modal-backdrop, body > .split-drop")]
      .some((o) => {
        const b = o.getBoundingClientRect();
        return b.left < r.right && b.right > r.left && b.top < r.bottom && b.bottom > r.top;
      });
  }

  new ResizeObserver(() => sync()).observe(host);
  window.addEventListener("resize", () => sync());
  on("layout", () => sync());     // volet déplacé sans changer de taille (inversion…)
  new MutationObserver(() => {
    const now = active && overlapped();
    if (now !== covered) { covered = now; sync(); }
  }).observe(document.body, { childList: true });

  on(`${cfg.id}:state`, (data) => { Object.assign(site, data); render(); });
  on(`${cfg.id}:unread`, ({ count }) => { site.unread = count; render(); });
  render();

  return {
    el,
    native: true,       // garde le clavier dans son propre contrôle (voir layout.focusView)
    onShow() { active = true; sync(); },
    onHide() { active = false; covered = false; sync(); },
    focus() { lastSent = ""; sync(true); },
  };
}

// ─────────────────────────────────────────────
//  Assistant de réponse (Ollama, en local). Rien n'est envoyé : le texte
//  choisi est placé dans la zone de saisie du site.
// ─────────────────────────────────────────────
function createAssistBar(ctx, state, cfg) {
  const { api, on, store } = ctx;
  const S = { models: state.chat.models || [], fallback: state.chat.default_model || "", ready: false, busy: false };

  const results = h("div", { class: `pane-results${cfg.long ? " long" : ""}`, hidden: true });
  const suggestBtn = h("button", { class: "btn sm", type: "button", onClick: suggest },
    ico("sparkles", 14), "Suggérer des réponses");
  const improveBtn = h("button", { class: "btn sm", type: "button", onClick: improve },
    ico("pencil-line", 14), "Améliorer le brouillon");
  const hint = h("input", { class: "field pane-hint", type: "text", maxlength: "500", spellcheck: "true",
    placeholder: cfg.hint,
    onKeydown: (ev) => { if (ev.key === "Enter") { ev.preventDefault(); suggest(); } } });
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle de l'assistant",
    onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget, "right") },
    modelName, ico("chevron-down", 14));
  const el = h("div", { class: "pane-assist", dataset: { ollama: "" } }, results,
    h("div", { class: "pane-tools" }, suggestBtn, improveBtn, hint, modelBtn));

  function model() {
    const wanted = store.settings.assist_model;
    if (wanted && S.models.includes(wanted)) return wanted;
    if (S.fallback && S.models.includes(S.fallback)) return S.fallback;
    return S.models[0] || "";
  }

  function refresh() {
    modelName.textContent = model() || "Aucun modèle";
    suggestBtn.disabled = improveBtn.disabled = S.busy || !S.ready || !model();
  }

  function modelItems() {
    const current = model();
    return [
      { section: "Modèle de l'assistant" },
      ...(S.models.length
        ? S.models.map((m) => ({ label: m, checked: m === current,
            onSelect: () => ctx.saveSetting("assist_model", m).then(refresh) }))
        : [{ label: "Aucun modèle installé", disabled: true }]),
      "-",
      { label: "Rafraîchir la liste", icon: "refresh-cw", onSelect: () => api.chat_refresh_models() },
    ];
  }

  function show(...children) {
    results.replaceChildren(...children,
      iconButton("x", "Fermer", () => show(), { size: 14, cls: "sm pane-close" }));
    results.hidden = children.length === 0;
  }

  function showError(message) {
    show(h("span", { class: "pane-status error" }, ico("triangle-alert", 14), message));
  }

  /** Remet le texte remplacé dans la zone de saisie. */
  function restoreButton(previous, label = "Rétablir l'original") {
    return h("button", { class: "btn sm", type: "button", onClick: async () => {
      const done = await api.pane_insert(cfg.id, previous);
      if (done?.ok) show();
      else showError(done?.error || "Restauration impossible.");
    } }, ico("undo-2", 14), label);
  }

  async function run(label, call) {
    if (S.busy || !S.ready) return null;
    const m = model();
    S.busy = true;
    refresh();
    show(h("span", { class: "pane-status" }, h("span", { class: "pane-busy" }), `${label} avec ${m}…`));
    try {
      const res = await call(cfg.id, m, hint.value.trim());
      if (!res?.ok) { showError(res?.error || "Échec de la génération."); return null; }
      return res;
    } finally {
      S.busy = false;
      refresh();
    }
  }

  async function suggest() {
    const res = await run("Rédaction de propositions", api.pane_suggest);
    if (!res) return;
    const head = h("span", { class: "pane-for", text: res.title ? `Pour « ${res.title} » :` : "Propositions :" });
    // E-mails : aperçu compact (sans lignes vides) ; le texte inséré reste complet
    const chips = res.replies.map((text) => h("button", { class: "pane-reply", type: "button",
      text: cfg.long ? text.replace(/\n\s*\n/g, "\n") : text,
      onClick: async (ev) => {
        const chip = ev.currentTarget;
        const done = await api.pane_insert(cfg.id, text);
        if (!done?.ok) { showError(done?.error || "Insertion impossible."); return; }
        chips.forEach((c) => c.classList.toggle("used", c === chip));
        // Un brouillon déjà écrit a été remplacé : on permet de le récupérer
        undo.replaceChildren();
        if (done.previous && done.previous !== text) undo.append(restoreButton(done.previous, "Rétablir mon brouillon"));
      } }));
    const undo = h("span", { class: "pane-undo" });
    show(head, ...chips, undo);
  }

  async function improve() {
    const res = await run("Amélioration du brouillon", api.pane_improve);
    if (!res) return;
    show(h("span", { class: "pane-status" }, ico("check", 14), `Brouillon amélioré dans ${cfg.title}.`),
      restoreButton(res.draft));
  }

  on("chat:models", ({ models, default_model }) => {
    S.models = models || [];
    if (default_model) S.fallback = default_model;
    refresh();
  });
  refresh();

  return {
    el,
    setReady(ready) { S.ready = ready; refresh(); },
  };
}
