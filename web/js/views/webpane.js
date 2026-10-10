// Vues des sites intégrés (WhatsApp, Gmail) : le site tourne dans un contrôle
// WebView2 natif (webpane.py), posé par Python au-dessus de la zone .pane-host
// dont on transmet la position. Sous le site, l'assistant de réponse (Ollama).
import { h, plural } from "../dom.js";
import { ico, iconButton, openMenuBelow, toast } from "../ui.js";

const WHATSAPP = {
  id: "whatsapp", title: "WhatsApp", icon: "message-circle",
  unread: ["discussion non lue", "discussions non lues"],
  firstRun: "Première ouverture : scannez le QR code avec WhatsApp sur votre téléphone (Appareils connectés). La session est ensuite conservée.",
  hint: "Consigne (facultatif) : plus formel, décline poliment…",
  idle: "Ouvrez une discussion : l'assistant pourra proposer une réponse ou améliorer votre message.",
};
const GMAIL = {
  id: "gmail", title: "Gmail", icon: "mail",
  unread: ["message non lu", "messages non lus"],
  firstRun: "Première ouverture : connectez-vous à votre compte Google. La session est ensuite conservée.",
  hint: "Consigne (facultatif) : accepte mardi 14 h, plus formel…",
  listHint: "Consigne pour le résumé (facultatif) : concentre-toi sur les factures…",   // aucun e-mail ouvert
  long: true,      // propositions = e-mails complets, affichés en entier
  digest: true,    // résumé de la boîte de réception
};

const HOLE_MARGIN = 6;     // px autour d'un menu percé dans le site (son ombre)

export const createWhatsAppView = (ctx, state) => createPaneView(ctx, state, WHATSAPP);
export const createGmailView = (ctx, state) => createPaneView(ctx, state, GMAIL);

function createPaneView(ctx, state, cfg) {
  const { api, on } = ctx;
  const site = { state: "idle", message: "", unread: 0, ...(state.panes?.[cfg.id] || {}) };
  let active = false;
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

  // Position du contrôle natif, en pixels physiques de la fenêtre, et ses trous
  // (menus de l'interface qui le chevauchent : le contrôle natif les cacherait).
  // focus : à l'arrivée sur la section seulement (pas à la fermeture d'un menu)
  function sync(focus = false) {
    let next = "hidden";
    let place = null;
    if (active) {
      const r = host.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const holes = holesIn(r);
      if (holes) {
        const k = window.devicePixelRatio || 1;
        const x = Math.floor(r.left * k), y = Math.floor(r.top * k);
        place = [x, y, Math.ceil(r.right * k) - x, Math.ceil(r.bottom * k) - y, holes];
        next = JSON.stringify(place);
      }
    }
    if (next === lastSent) return;
    lastSent = next;
    if (!place) api.pane_hide(cfg.id);
    else api.pane_show(cfg.id, ...place.slice(0, 4), focus === true, place[4]);
  }

  /** Zones du site recouvertes par un menu, un panneau ou une infobulle de l'interface,
   *  en pixels physiques relatifs au site : le contrôle natif y est percé (le reste
   *  du site reste affiché). null : une modale ou un écran entier le recouvre, il est masqué.
   *  (Écran partagé : un menu de l'autre volet ne le touche pas.) */
  function holesIn(r) {
    const k = window.devicePixelRatio || 1;
    const holes = [];
    for (const o of document.querySelectorAll(`body > .menu, body > .popover, body > .tooltip,
        body > .modal-backdrop, body > .split-drop, body > .lightbox`)) {
      const b = o.getBoundingClientRect();
      if (!(b.left < r.right && b.right > r.left && b.top < r.bottom && b.bottom > r.top)) continue;
      if (!o.matches(".menu, .popover, .tooltip")) return null;
      // Marge : l'ombre portée du menu reste visible
      const x0 = Math.max(b.left - HOLE_MARGIN, r.left), y0 = Math.max(b.top - HOLE_MARGIN, r.top);
      const x1 = Math.min(b.right + HOLE_MARGIN, r.right), y1 = Math.min(b.bottom + HOLE_MARGIN, r.bottom);
      holes.push([Math.floor((x0 - r.left) * k), Math.floor((y0 - r.top) * k),
        Math.ceil((x1 - x0) * k), Math.ceil((y1 - y0) * k)]);
    }
    return holes;
  }

  new ResizeObserver(() => sync()).observe(host);
  window.addEventListener("resize", () => sync());
  on("layout", () => sync());     // volet déplacé sans changer de taille (inversion…)
  // Menu, infobulle ou modale ouverts ou fermés (ajoutés à la page, déjà placés)
  new MutationObserver(() => { if (active) sync(); }).observe(document.body, { childList: true });

  on(`${cfg.id}:state`, (data) => { Object.assign(site, data); render(); });
  on(`${cfg.id}:unread`, ({ count }) => { site.unread = count; render(); });
  on(`${cfg.id}:context`, (context) => assist.setContext(context));
  render();

  return {
    el,
    native: true,       // garde le clavier dans son propre contrôle (voir layout.focusView)
    onShow() { active = true; sync(); },
    onHide() { active = false; sync(); },
    focus() { lastSent = ""; sync(true); },
  };
}

// ─────────────────────────────────────────────
//  Assistant de réponse (Ollama, en local). Rien n'est envoyé : le texte
//  choisi est placé dans la zone de saisie du site.
// ─────────────────────────────────────────────
const DIGEST_RUNNING = new Set(["listing", "reading", "overview"]);
const DIGEST_MAX = 50;          // e-mails résumés au plus (gmail.py)
const DIGEST_MIN_H = 80;        // hauteur minimale du résumé redimensionné (px)
const SITE_MIN_H = 60;          // le site reste visible au-dessus (px, voir .pane-host)

function createAssistBar(ctx, state, cfg) {
  const { api, on, store } = ctx;
  const S = { models: state.chat.models || [], fallback: state.chat.default_model || "", ready: false, busy: false,
    digest: state.panes?.[cfg.id]?.digest || { state: "idle" },
    // Ce qui s'applique à la page du site (webpane.WATCH_JS) : e-mail ou discussion
    // ouvert (reply), brouillon écrit (improve) — les autres boutons sont masqués
    context: state.panes?.[cfg.id]?.context || { reply: false, improve: false } };
  // Le panneau des résultats montre le résumé de la boîte (suivi pendant le travail)
  let digestShown = DIGEST_RUNNING.has(S.digest.state);

  const results = h("div", { class: `pane-results${cfg.long ? " long" : ""}`, hidden: true });
  const suggestBtn = h("button", { class: "btn sm", type: "button", onClick: suggest },
    ico("sparkles", 14), "Suggérer une réponse");
  const improveBtn = h("button", { class: "btn sm", type: "button", onClick: improve },
    ico("pencil-line", 14), "Améliorer le brouillon");
  // Un e-mail ouvert : lui seul ; sinon la liste affichée (menu sélection / non lus / tous)
  const digestLabel = h("span");
  const digestBtn = cfg.digest ? h("button", { class: "btn sm", type: "button", onClick: digest },
    ico("scroll-text", 14), digestLabel) : null;
  const hint = h("input", { class: "field pane-hint", type: "text", maxlength: "500", spellcheck: "true",
    placeholder: cfg.hint,
    onKeydown: (ev) => { if (ev.key === "Enter" && S.context.reply) { ev.preventDefault(); suggest(); } } });
  const idleNote = cfg.idle ? h("span", { class: "pane-idle", text: cfg.idle }) : null;
  const modelName = h("span", { class: "name" });
  const modelBtn = h("button", { class: "model-pill", type: "button", "aria-label": "Modèle de l'assistant",
    onClick: (ev) => openMenuBelow(modelItems(), ev.currentTarget, "right") },
    modelName, ico("chevron-down", 14));
  // Poignée du résumé (bord haut) : sa hauteur se règle à la souris
  const grip = cfg.digest ? h("div", { class: "pane-grip", role: "separator", tabindex: "0",
    "aria-orientation": "horizontal", "aria-label": "Hauteur du résumé",
    dataset: { tip: "Glisser pour changer la hauteur du résumé — double-clic : hauteur automatique" } }) : null;
  const el = h("div", { class: "pane-assist", dataset: { ollama: "" } }, grip, results,
    h("div", { class: "pane-tools" }, suggestBtn, improveBtn, digestBtn, idleNote, hint, modelBtn));

  function model() {
    const wanted = store.settings.assist_model;
    if (wanted && S.models.includes(wanted)) return wanted;
    if (S.fallback && S.models.includes(S.fallback)) return S.fallback;
    return S.models[0] || "";
  }

  const idle = () => !S.busy && !DIGEST_RUNNING.has(S.digest.state);

  function refresh() {
    modelName.textContent = model() || "Aucun modèle";
    const off = !idle() || !S.ready || !model();
    for (const btn of [suggestBtn, improveBtn, digestBtn]) if (btn) btn.disabled = off;
    // Seulement ce qui s'applique : « Suggérer » avec un e-mail (une discussion)
    // ouvert, « Améliorer » avec un brouillon écrit
    const { reply, improve: draft } = S.context;
    suggestBtn.hidden = !reply;
    improveBtn.hidden = !draft;
    const any = reply || draft || !!digestBtn;
    hint.hidden = !any;
    if (idleNote) idleNote.hidden = any;
    hint.placeholder = reply || draft || !cfg.listHint ? cfg.hint : cfg.listHint;
    if (digestBtn) {
      digestLabel.textContent = reply ? "Résumer cet e-mail" : "Résumer les e-mails";
      digestBtn.dataset.tip = reply ? "Résume l'e-mail ouvert (tout le fil) et ce qu'il attend de vous"
        : "Lit les e-mails de la liste affichée dans Gmail — sélectionnés, non lus ou tous — "
          + "sans les marquer comme lus, et fait le point";
    }
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
      { label: "Gérer les modèles…", icon: "settings", onSelect: () => ctx.navigate("ollama") },
    ];
  }

  function show(...children) {
    digestShown = false;
    results.classList.remove("digest");
    sizeDigest();
    fill(children);
  }

  // ── Hauteur du résumé : réglage digest_height (px, 0 = automatique) ──
  /** Applique la hauteur choisie au résumé affiché (les autres résultats gardent la leur). */
  function sizeDigest() {
    const px = digestShown ? Number(store.settings.digest_height) || 0 : 0;
    el.classList.toggle("resizable", digestShown);
    results.classList.toggle("sized", px > 0);
    if (px > 0) results.style.setProperty("--digest-h", `${px}px`);
  }

  /** Plus grande hauteur possible : le site garde SITE_MIN_H au-dessus. */
  function maxDigestHeight() {
    const host = el.parentElement?.querySelector(".pane-host");
    const now = results.getBoundingClientRect().height;
    return Math.max(DIGEST_MIN_H, now + (host ? host.getBoundingClientRect().height - SITE_MIN_H : 0));
  }

  function setDigestHeight(px, save) {
    store.settings.digest_height = px ? Math.round(Math.max(DIGEST_MIN_H, Math.min(px, maxDigestHeight()))) : 0;
    sizeDigest();
    if (save) ctx.saveSetting("digest_height", store.settings.digest_height);
  }

  if (grip) {
    grip.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      // Capture : le glisser continue au-dessus du site (contrôle natif)
      try { grip.setPointerCapture(ev.pointerId); } catch { /* pointeur déjà relâché */ }
      grip.classList.add("dragging");
      document.body.classList.add("pane-resizing");
      const startY = ev.clientY;
      const startH = results.getBoundingClientRect().height;
      const max = maxDigestHeight();
      const before = store.settings.digest_height;
      let done = false;
      const move = (e) => {
        if (!(e.buttons & 1)) { end(); return; }    // relâché hors de la page
        store.settings.digest_height = Math.round(Math.max(DIGEST_MIN_H, Math.min(max, startH + startY - e.clientY)));
        sizeDigest();
      };
      const end = () => {
        if (done) return;
        done = true;
        grip.removeEventListener("pointermove", move);
        grip.classList.remove("dragging");
        document.body.classList.remove("pane-resizing");
        if (store.settings.digest_height !== before) ctx.saveSetting("digest_height", store.settings.digest_height);
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", end, { once: true });
      grip.addEventListener("lostpointercapture", end, { once: true });
    });
    grip.addEventListener("dblclick", () => setDigestHeight(0, true));
    grip.addEventListener("keydown", (ev) => {
      const step = { ArrowUp: 40, ArrowDown: -40 }[ev.key];
      if (!step) return;
      ev.preventDefault();
      setDigestHeight(results.getBoundingClientRect().height + step, true);
    });
  }

  function fill(children) {
    results.replaceChildren(...children, iconButton("x", "Fermer", close, { size: 14, cls: "sm pane-close" }));
    results.hidden = children.length === 0;
  }

  // Fermer le résumé en cours l'arrête
  function close() {
    if (digestShown && DIGEST_RUNNING.has(S.digest.state)) api.pane_digest_stop(cfg.id);
    show();
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
    if (!idle() || !S.ready) return null;
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

  // ── Résumé des e-mails de la liste affichée dans Gmail (en arrière-plan,
  //    événements <site>:digest) : sélectionnés, non lus ou tous ──
  async function digest(ev) {
    const anchor = ev.currentTarget;
    if (!idle() || !S.ready) return;
    if (S.context.reply) { startDigest("current"); return; }    // e-mail ouvert : lui seul
    const scopes = await api.pane_digest_scopes(cfg.id);
    if (!scopes?.ok) { showError(scopes?.error || `${cfg.title} ne répond pas.`); return; }
    const items = scopeItems(scopes);
    // Rien à choisir (ni sélection, ni non-lus) : tous, directement
    if (items.length === 1) { items[0].onSelect(); return; }
    const menu = openMenuBelow([{ section: `Résumer — ${scopes.view}` }, ...items], anchor);
    menu.focusEntry(menu.entries[0]);      // le plus précis en premier (Entrée)
  }

  /** Choix proposés : la sélection s'il y en a une, les non-lus s'il y en a, et tout.
   *  Sans liste affichée (fil ouvert) : non lus ou tous de la boîte de réception. */
  function scopeItems(s) {
    const item = (scope, label, icon, count) => ({ label, icon, hint: count ? String(count) : "",
      onSelect: () => startDigest(scope) });
    return [
      s.list && s.selection ? item("selection", s.selection > 1 ? "Les e-mails sélectionnés" : "L'e-mail sélectionné",
        "text-select", s.selection) : null,
      !s.list || s.unread ? item("unread", s.unread === 1 ? "L'e-mail non lu" : "Les e-mails non lus", "mail", s.unread) : null,
      item("all", !s.list ? "Toute la boîte de réception" : s.all > DIGEST_MAX ? `Les ${DIGEST_MAX} premiers e-mails`
        : "Tous les e-mails", "scroll-text", s.list ? Math.min(s.all, DIGEST_MAX) : 0),
    ].filter(Boolean);
  }

  async function startDigest(scope) {
    if (!idle() || !S.ready) return;
    const res = await api.pane_digest(cfg.id, model(), hint.value.trim(), scope);
    if (!res?.ok) { showError(res?.error || "Résumé impossible."); return; }
    digestShown = true;
    renderDigest();
  }

  function digestCount(d) {
    if (!d.total || d.scope === "current") return "";
    if (d.scope === "selection") return plural(d.total, "e-mail sélectionné", "e-mails sélectionnés");
    if (d.scope === "unread") return plural(d.total, "non lu", "non lus");
    return d.listed > d.total ? `${d.total} plus récents sur ${d.listed.toLocaleString("fr-FR")}`
      : plural(d.total, "e-mail", "e-mails");
  }

  function digestEmpty(d) {
    const where = `dans « ${d.view || "Gmail"} »`;
    if (d.scope === "selection") return "Aucun e-mail sélectionné dans Gmail.";
    return d.scope === "unread" ? `Aucun e-mail non lu ${where}.` : `Aucun e-mail ${where}.`;
  }

  function digestProgress(d) {
    if (d.scope === "current") return `Lecture et résumé de l'e-mail avec ${d.model}…`;
    if (d.state === "listing") return "Lecture de la boîte de réception…";
    if (d.state === "overview") return "Synthèse…";
    return `Résumé ${Math.min(d.items.length + 1, d.total)} sur ${d.total} avec ${d.model}…`;
  }

  function renderDigest() {
    const d = S.digest;
    const items = d.items || [];
    const running = DIGEST_RUNNING.has(d.state);
    const text = () => digestText(d);
    const head = h("div", { class: "digest-head" },
      h("span", { class: "digest-title" }, ico("scroll-text", 14), d.view || "E-mails"),
      h("span", { class: "digest-count", text: digestCount(d) }),
      running ? h("span", { class: "pane-status" }, h("span", { class: "pane-busy" }), digestProgress(d)) : null,
      d.state === "cancelled" ? h("span", { class: "pane-status", text: "Arrêté." }) : null,
      d.state === "error" ? h("span", { class: "pane-status error" }, ico("triangle-alert", 14),
        d.error || "Échec du résumé.") : null,
      h("span", { class: "digest-actions" }, running
        ? h("button", { class: "btn sm", type: "button", onClick: () => api.pane_digest_stop(cfg.id) },
          ico("square", 12), "Arrêter")
        : items.length ? [
          iconButton("copy", "Copier le résumé", () => ctx.copy(text()), { size: 14, cls: "sm" }),
          iconButton("notebook-pen", "Ajouter à une note", (ev) => ctx.pickDestination("notes", ev.currentTarget,
            (slot) => ctx.emit("note:insert", { text: text(), slot })), { size: 14, cls: "sm" }),
        ] : null));
    results.classList.add("digest");
    sizeDigest();
    fill([h("div", { class: "pane-digest" }, head,
      d.overview ? h("p", { class: "digest-overview", text: d.overview }) : null,
      d.state === "done" && !items.length
        ? h("p", { class: "digest-empty", text: digestEmpty(d) }) : null,
      items.length ? h("div", { class: "digest-list" }, items.map(digestItem)) : null)]);
  }

  /** Un fil : clic pour l'ouvrir dans Gmail (sauf sélection de texte en cours). */
  function digestItem(item) {
    const open = async () => {
      const res = await api.pane_open_thread(cfg.id, item.id);
      if (!res?.ok) toast(res?.error || "Ouverture impossible.", "error");
    };
    // E-mail ouvert : déjà affiché dans Gmail, pas de lien
    const link = S.digest.scope !== "current";
    return h("div", { class: `digest-item${item.unread ? " unread" : ""}${item.failed ? " failed" : ""}${link ? "" : " static"}`,
      role: link ? "button" : null, tabindex: link ? "0" : null,
      onClick: () => { if (link && window.getSelection().isCollapsed) open(); },
      onKeydown: (ev) => { if (link && ev.key === "Enter") open(); } },
      h("div", { class: "digest-meta" },
        item.unread ? h("span", { class: "digest-dot", "aria-label": "Non lu" }) : null,
        h("span", { class: "digest-from", text: item.from || "(expéditeur inconnu)" }),
        h("span", { class: "digest-subject", text: item.subject || "(sans objet)" }),
        h("span", { class: "digest-when", text: item.when, dataset: { tip: item.date || null } })),
      h("div", { class: "digest-summary", text: item.summary,
        dataset: { tip: item.failed ? "Résumé impossible : extrait affiché par Gmail" : null } }),
      item.action ? h("div", { class: "digest-action" }, ico("chevron-right", 12), h("span", { text: item.action })) : null);
  }

  /** Texte à copier ou à mettre dans une note. */
  function digestText(d) {
    const day = new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
    const count = digestCount(d);
    const lines = [`${d.view || "Gmail"} — ${day}${count ? ` (${count})` : ""}`, ""];
    if (d.overview) lines.push(d.overview, "");
    for (const i of d.items) {
      lines.push(`• ${i.unread ? "[non lu] " : ""}${i.from} — ${i.subject || "(sans objet)"}${i.when ? ` (${i.when})` : ""}`);
      if (i.summary) lines.push(`  ${i.summary}`);
      if (i.action) lines.push(`  → À faire : ${i.action}`);
    }
    return lines.join("\n");
  }

  if (cfg.digest) {
    on(`${cfg.id}:digest`, (d) => {
      S.digest = d;
      refresh();
      if (digestShown) renderDigest();
    });
    if (digestShown) renderDigest();     // interface rechargée pendant le résumé
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
    setContext(context) { S.context = { reply: !!context?.reply, improve: !!context?.improve }; refresh(); },
  };
}
