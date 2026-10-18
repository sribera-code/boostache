// Liste des e-mails à côté de Gmail : la liste affichée dans le site (boîte de
// réception, libellé, recherche — relevée par gmail.py, événement gmail:list),
// avec le résumé de chaque e-mail à la demande : par e-mail, pour tous (le
// résumé de l'assistant, synthèse comprise), ou d'office pour les nouveaux.
import { h } from "../dom.js";
import { ico, iconButton, openMenu, toast } from "../ui.js";

const DIGEST_MAX = 50;           // e-mails résumés au plus (gmail.py)
const WIDTH_DEFAULT = 340;
const WIDTH_MIN = 220;
const SITE_MIN = 320;            // le site garde au moins cette largeur (px)
const RUNNING = new Set(["queued", "running"]);
// Importance donnée par le modèle avec le résumé (gmail.IMPORTANCE) : icône des e-mails à voir
const IMPORTANCE = { urgent: ["flag", "Urgent"], important: ["star", "Important"] };

/** Icône d'un e-mail urgent ou important (rien pour les autres). */
export function importanceIcon(level, size = 13) {
  const [name, label] = IMPORTANCE[level] || [];
  return name ? h("span", { class: `imp imp-${level}`, role: "img", "aria-label": label, dataset: { tip: label } },
    ico(name, size)) : null;
}

/** assist : barre de l'assistant (modèle, consigne, résumé de plusieurs e-mails). */
export function createMailList(ctx, state, cfg, assist) {
  const { api, on, store } = ctx;
  const snap = state.panes?.[cfg.id] || {};
  const S = { mail: snap.mail || { rows: [], view: "", base: "#inbox", open: "" }, sums: snap.summaries || {} };
  let cache = new Map();         // fil → { sig, el } : lignes inchangées gardées telles quelles
  const expanded = new Set();    // fils dont le résumé est affiché en entier
  const acting = new Map();      // fil → action en cours (lu, non lu, supprimer), jusqu'à la liste suivante

  const title = h("span", { class: "ml-title" });
  const count = h("span", { class: "ml-count" });
  const stopBtn = iconButton("square", "Arrêter les résumés en attente", () => api.pane_list_stop(cfg.id),
    { size: 12, cls: "sm" });
  const autoBtn = iconButton("zap", "", toggleAuto, { size: 15, cls: "sm" });
  // Résumer, sans menu : un bouton par choix (non lus, tous, sélection dans Gmail),
  // sur une rangée sous l'en-tête ; la sélection en dernier, les autres ne bougent pas
  const scopeBtn = (scope, label) => {
    const n = h("span", { class: "ml-scope-n" });
    const btn = h("button", { class: "pane-chip ml-scope", type: "button", onClick: () => summarizeScope(scope) }, label, n);
    btn.count = n;
    return btn;
  };
  const sumBtns = { unread: scopeBtn("unread", "Non lus"), all: scopeBtn("all", "Tous"),
    selection: scopeBtn("selection", "Sélection") };
  const scopesRow = h("div", { class: "ml-scopes", role: "group", "aria-label": "Résumer les e-mails" },
    h("span", { class: "ml-scopes-label" }, ico("sparkles", 13), "Résumer"), ...Object.values(sumBtns));
  const rowsEl = h("div", { class: "ml-rows", role: "list" });
  const el = h("aside", { class: "mail-list", "aria-label": "Liste des e-mails" },
    h("div", { class: "ml-head" }, title, count, h("span", { class: "head-spacer" }), stopBtn, autoBtn),
    scopesRow, rowsEl);
  const splitter = h("div", { class: "ml-splitter", role: "separator", tabindex: "0",
    "aria-orientation": "vertical", "aria-label": "Largeur de la liste des e-mails",
    dataset: { tip: "Glisser pour changer la largeur — double-clic : largeur par défaut" } });

  // ── Rendu ──
  function render() {
    const { rows = [], view } = S.mail;
    title.textContent = view || "Gmail";
    const unread = rows.filter((r) => r.unread).length;
    const marked = rows.filter((r) => IMPORTANCE[S.sums[r.id]?.importance]).length;
    count.textContent = rows.length ? `${rows.length}${unread ? ` · ${unread} non lu${unread > 1 ? "s" : ""}` : ""}`
      + (marked ? ` · ${marked} important${marked > 1 ? "s" : ""}` : "") : "";
    stopBtn.hidden = !Object.values(S.sums).some((e) => e.state === "queued");
    const auto = !!store.settings.gmail_auto_summary;
    autoBtn.classList.toggle("on", auto);
    autoBtn.dataset.tip = auto ? "Nouveaux e-mails résumés automatiquement — cliquer pour arrêter"
      : "Résumer automatiquement les nouveaux e-mails de la boîte de réception";
    autoBtn.setAttribute("aria-label", "Résumé automatique des nouveaux e-mails");
    autoBtn.setAttribute("aria-pressed", String(auto));
    renderScopes();
    if (!rows.length) {
      cache = new Map();
      rowsEl.replaceChildren(h("p", { class: "ml-empty", text: view ? "Aucun e-mail dans cette liste."
        : "La liste apparaîtra dès que Gmail affichera une boîte de réception, un libellé ou une recherche." }));
      return;
    }
    const next = new Map();
    const els = rows.map((r) => {
      const sum = S.sums[r.id];
      const sig = JSON.stringify([r, sum || null, r.id === S.mail.open, expanded.has(r.id), acting.get(r.id) || "",
        S.mail.search]);
      let c = cache.get(r.id);
      if (!c || c.sig !== sig) c = { sig, el: rowEl(r, sum) };
      next.set(r.id, c);
      return c.el;
    });
    cache = next;
    const kids = rowsEl.children;
    if (els.length !== kids.length || els.some((e, i) => kids[i] !== e)) rowsEl.replaceChildren(...els);
    measureSoon();
  }

  // ── Résumés réduits : deux lignes (et l'action sur une) ; le bouton n'apparaît
  //    que si le texte est coupé — vérifié après chaque rendu ou changement de largeur ──
  let measuring = 0;
  function measureSoon() {
    if (!measuring) measuring = requestAnimationFrame(measure);
  }
  function measure() {
    measuring = 0;
    for (const block of rowsEl.querySelectorAll(".ml-summary:not(.open)")) {
      const text = block.querySelector(".ml-summary-text");
      const action = block.querySelector(".ml-action > span");
      const cut = text.scrollHeight > text.clientHeight + 1 || (!!action && action.scrollWidth > action.clientWidth + 1);
      block.classList.toggle("truncated", cut);
      block.querySelector(".ml-more").hidden = !cut;
    }
  }
  new ResizeObserver(measureSoon).observe(rowsEl);

  function toggleSummary(id) {
    if (!expanded.delete(id)) expanded.add(id);
    render();
  }

  function rowEl(r, sum) {
    const st = sum?.state || "idle";
    const busy = RUNNING.has(st);
    // Résumé d'avant un nouveau message du fil : encore affiché, atténué
    const stale = !!(sum?.summary && sum.last && r.last && sum.last !== r.last);
    const btn = h("button", { class: `icon-btn sm ml-sum${busy ? " busy" : ""}`, type: "button", disabled: busy,
      "aria-label": sum?.summary ? "Résumer à nouveau" : "Résumer cet e-mail",
      dataset: { tip: busy ? null : sum?.summary ? "Résumer à nouveau" : "Résumer cet e-mail" },
      onClick: (ev) => { ev.stopPropagation(); summarize([r.id]); } },
      busy ? h("span", { class: "pane-busy" }) : ico("sparkles", 14));
    const act = (name, icon, label, action) => h("button", { class: `icon-btn sm ml-act ml-${name}`, type: "button",
      "aria-label": label, dataset: { tip: label }, onClick: (ev) => { ev.stopPropagation(); rowAction(r, action); } },
      ico(icon, 14));
    return h("div", { class: `ml-row${r.unread ? " unread" : ""}${r.id === S.mail.open ? " open" : ""}${r.selected ? " selected" : ""}`
      + `${acting.has(r.id) ? " acting" : ""}${acting.get(r.id) === "delete" ? " removing" : ""}`,
      role: "listitem", tabindex: "0",
      onClick: () => { if (window.getSelection().isCollapsed) openThread(r); },
      onKeydown: (ev) => {
        if (ev.target !== ev.currentTarget) return;
        if (ev.key === "Enter") { openThread(r); return; }
        if (ev.key === "Delete" && canDelete()) { ev.preventDefault(); rowAction(r, "delete"); return; }
        // → / ← : résumé en entier / réduit
        const want = { ArrowRight: true, ArrowLeft: false }[ev.key];
        if (want !== undefined && want !== expanded.has(r.id)
          && ev.currentTarget.querySelector(".ml-summary.truncated, .ml-summary.open")) toggleSummary(r.id);
      },
      onContextmenu: (ev) => rowMenu(ev, r) },
      h("div", { class: "ml-meta" },
        r.unread ? h("span", { class: "digest-dot", "aria-label": "Non lu" }) : null,
        importanceIcon(sum?.summary ? sum.importance : ""),
        h("span", { class: "ml-from", text: r.from || "(expéditeur inconnu)" }),
        h("span", { class: "ml-when", text: r.when, dataset: { tip: r.date || null } }),
        // Au survol, à la place de la date : lu / non lu, supprimer
        h("span", { class: "ml-acts" },
          r.unread ? act("read", "mail-open", "Marquer comme lu", "read") : act("unread", "mail", "Marquer comme non lu", "unread"),
          canDelete() ? act("delete", "trash-2", "Supprimer", "delete") : null),
        btn),
      h("div", { class: "ml-subject" }, h("span", { text: r.subject || "(sans objet)" }),
        r.snippet ? h("span", { class: "ml-snippet", text: ` — ${r.snippet}` }) : null),
      sum?.summary ? summaryEl(r, sum, stale || busy, stale)
        : busy ? h("div", { class: "ml-pending", text: st === "queued" ? "En attente du résumé…" : "Résumé en cours…" }) : null,
      st === "failed" ? h("div", { class: "ml-error", text: sum.error || "Résumé impossible." }) : null);
  }

  /** Résumé réduit ; un clic ouvre l'e-mail comme le reste de la ligne, le chevron
   *  (ou → / ← sur la ligne) l'affiche en entier. */
  function summaryEl(r, sum, dim, stale) {
    const open = expanded.has(r.id);
    const label = open ? "Réduire le résumé" : "Afficher tout le résumé";
    return h("div", { class: `ml-summary${dim ? " dim" : ""}${open ? " open" : ""}`,
      dataset: { tip: stale ? "Nouveau message depuis ce résumé" : null } },
      h("span", { class: "ml-summary-text" }, ico("sparkles", 11), sum.summary),
      sum.action ? h("span", { class: "ml-action" }, ico("chevron-right", 12), h("span", { text: sum.action })) : null,
      h("button", { class: "ml-more", type: "button", hidden: !open, "aria-expanded": String(open), "aria-label": label,
        dataset: { tip: label }, onClick: (ev) => { ev.stopPropagation(); toggleSummary(r.id); } },
        ico("chevron-down", 14)));
  }

  // ── Actions ──
  // Corbeille, spams : la suppression y serait définitive, laissée à Gmail
  const canDelete = () => S.mail.search !== "trash" && S.mail.search !== "spam";

  /** Lu, non lu, supprimer : par Gmail (bouton de la ligne) ; la ligne reste
   *  atténuée jusqu'à la liste suivante qui montre le changement. */
  async function rowAction(r, action) {
    if (acting.has(r.id)) return;
    acting.set(r.id, action);
    render();
    const res = await api.pane_list_action(cfg.id, r.id, action);
    if (!res?.ok) {
      acting.delete(r.id);
      render();
      toast(res?.error || "Action impossible.", "error");
      return;
    }
    if (action === "delete") toast("E-mail placé dans la corbeille.");
    // Sans changement visible (Gmail lent, liste inchangée) : la ligne redevient normale
    setTimeout(() => { if (acting.get(r.id) === action) { acting.delete(r.id); render(); } }, 6000);
  }

  async function openThread(r) {
    const res = await api.pane_open_thread(cfg.id, r.id, S.mail.base);
    if (!res?.ok) toast(res?.error || "Ouverture impossible.", "error");
  }

  async function summarize(ids) {
    const res = await api.pane_list_summarize(cfg.id, assist.model(), assist.hint(), ids);
    if (!res?.ok) toast(res?.error || "Résumé impossible.", "error");
  }

  // Lu : le résumé suffit ; copié : avec expéditeur et objet
  const speech = (sum) => [sum.summary, sum.action ? `À faire : ${sum.action}` : ""].filter(Boolean).join("\n");
  const copyText = (r, sum) => `${r.from || "Expéditeur inconnu"}${r.subject ? ` : ${r.subject}` : ""}.\n${speech(sum)}`;

  function rowMenu(ev, r) {
    ev.preventDefault();
    const sum = S.sums[r.id];
    const done = !!sum?.summary;
    const source = `mail-list:${r.id}`;
    openMenu([
      { label: "Ouvrir dans Gmail", icon: "mail", onSelect: () => openThread(r) },
      r.unread ? { label: "Marquer comme lu", icon: "mail-open", onSelect: () => rowAction(r, "read") }
        : { label: "Marquer comme non lu", icon: "mail", onSelect: () => rowAction(r, "unread") },
      canDelete() ? { label: "Supprimer", icon: "trash-2", hint: "Suppr", onSelect: () => rowAction(r, "delete") } : null,
      "-",
      { label: done ? "Résumer à nouveau" : "Résumer cet e-mail", icon: "sparkles",
        disabled: RUNNING.has(sum?.state), onSelect: () => summarize([r.id]) },
      done ? "-" : null,
      done ? { label: ctx.speaking(source) ? "Arrêter la lecture" : "Lire le résumé", icon: "volume-2",
        onSelect: () => ctx.toggleSpeak(speech(sum), source) } : null,
      done ? { label: "Copier le résumé", icon: "copy", onSelect: () => ctx.copy(copyText(r, sum)) } : null,
    ].filter(Boolean), ev.clientX, ev.clientY);
  }

  /** Plusieurs e-mails : le résumé de l'assistant (chacun, puis la synthèse), sur
   *  les e-mails sélectionnés dans Gmail, les non lus ou tous ceux de la liste. */
  function scopeIds(scope) {
    const rows = S.mail.rows || [];
    if (scope === "all") return rows.slice(0, DIGEST_MAX).map((r) => r.id);
    return rows.filter((r) => (scope === "unread" ? r.unread : r.selected)).map((r) => r.id);
  }

  /** Un bouton par choix qui a des e-mails, avec leur nombre. */
  function renderScopes() {
    const tips = {
      selection: (k) => k > 1 ? `Résumer les ${k} e-mails sélectionnés dans Gmail` : "Résumer l'e-mail sélectionné dans Gmail",
      unread: (k) => k > 1 ? `Résumer les ${k} e-mails non lus` : "Résumer l'e-mail non lu",
      all: (k) => (S.mail.rows || []).length > k ? `Résumer les ${k} premiers e-mails de la liste`
        : k > 1 ? `Résumer les ${k} e-mails de la liste` : "Résumer l'e-mail de la liste",
    };
    for (const [scope, btn] of Object.entries(sumBtns)) {
      const k = scopeIds(scope).length;
      btn.hidden = !k;
      btn.count.textContent = k ? String(k) : "";
      btn.dataset.tip = k ? `${tips[scope](k)} — chacun, puis une synthèse` : "";
    }
    scopesRow.hidden = Object.values(sumBtns).every((b) => b.hidden);
  }

  function summarizeScope(scope) {
    const ids = scopeIds(scope);
    if (!ids.length) return;
    if (!assist.canRun()) { toast(assist.blocked(), "error"); return; }
    assist.runDigest(scope, ids);
  }

  function toggleAuto() {
    const on = !store.settings.gmail_auto_summary;
    store.settings.gmail_auto_summary = on;
    render();
    ctx.saveSetting("gmail_auto_summary", on);
    toast(on ? "Les nouveaux e-mails de la boîte de réception seront résumés automatiquement."
      : "Résumé automatique arrêté.");
  }

  // ── Largeur (réglage mail_list_width) et affichage (mail_list) ──
  const setWidth = (w) => el.style.setProperty("--ml-w", `${Math.round(w)}px`);
  const maxWidth = () => Math.max(WIDTH_MIN, (el.parentElement?.getBoundingClientRect().width || 0) - SITE_MIN);
  const clampWidth = (w) => Math.max(WIDTH_MIN, Math.min(w, maxWidth()));
  setWidth(Number(store.settings.mail_list_width) || WIDTH_DEFAULT);

  splitter.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    // Capture : le glisser continue au-dessus du site (contrôle natif)
    try { splitter.setPointerCapture(ev.pointerId); } catch { /* pointeur déjà relâché */ }
    splitter.classList.add("dragging");
    document.body.classList.add("ml-resizing");
    const left = el.getBoundingClientRect().left;
    let width = el.getBoundingClientRect().width;
    const move = (e) => { width = clampWidth(e.clientX - left); setWidth(width); };
    const end = () => {
      splitter.classList.remove("dragging");
      document.body.classList.remove("ml-resizing");
      splitter.removeEventListener("pointermove", move);
      splitter.removeEventListener("pointerup", end);
      splitter.removeEventListener("lostpointercapture", end);
      ctx.saveSetting("mail_list_width", Math.round(width));
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", end);
    splitter.addEventListener("lostpointercapture", end);
  });
  splitter.addEventListener("dblclick", () => { setWidth(WIDTH_DEFAULT); ctx.saveSetting("mail_list_width", WIDTH_DEFAULT); });
  splitter.addEventListener("keydown", (ev) => {
    const step = { ArrowLeft: -24, ArrowRight: 24 }[ev.key];
    if (!step) return;
    ev.preventDefault();
    const w = clampWidth(el.getBoundingClientRect().width + step);
    setWidth(w);
    ctx.saveSetting("mail_list_width", Math.round(w));
  });

  function setShown(shown) {
    el.hidden = splitter.hidden = !shown;
  }
  setShown(store.settings.mail_list !== false);

  on("gmail:list", (mail) => {
    // Action faite : ligne supprimée, ou lu / non lu changé
    for (const [id, action] of acting) {
      const row = mail.rows?.find((x) => x.id === id);
      if (!row || (action !== "delete" && row.unread === (action === "unread"))) acting.delete(id);
    }
    S.mail = mail;
    render();
  });
  on("gmail:summaries", (changed) => { Object.assign(S.sums, changed); render(); });
  render();

  return {
    el, splitter,
    shown: () => !el.hidden,
    toggle() {
      const shown = el.hidden;
      setShown(shown);
      ctx.saveSetting("mail_list", shown);
      return shown;
    },
  };
}
