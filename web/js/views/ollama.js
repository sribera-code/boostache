// Vue Ollama : le serveur (état, lancement), les modèles installés (détails,
// mémoire, mises à jour, suppression), le téléchargement de nouveaux modèles
// depuis la bibliothèque d'ollama.com et les options des appels de Boostache.
import { h, debounce, formatHour } from "../dom.js";
import { card, ico, iconButton, openMenu, openMenuBelow, openModal, toast } from "../ui.js";

const SITE = "https://ollama.com";
const POLL_MS = 5000;          // modèles en mémoire relus toutes les 5 s (section affichée)
const PAGE = 12;               // modèles de la bibliothèque affichés, puis par « Afficher plus »
const NAME_RE = /^[\w.-]+(?:\/[\w.-]+)*(?::[\w.-]+)?$/;
const SIZE_LABEL = /^(?:(\d+)x)?(e)?(\d+(?:\.\d+)?)([mb])$/i;   // « 4b », « e2b », « 270m », « 8x7b »
const FIT_KEY = "boostache.ollama.fitOnly";     // filtre « Adaptés à ce PC » (localStorage)

// Capacités (Ollama, ollama.com) → libellé, infobulle ; les autres ne sont pas affichées
const CAPS = {
  vision:    ["Images", "Lit les images : captures, aide contextuelle, assistant live"],
  thinking:  ["Réflexion", "Réfléchit avant de répondre : plus lent, souvent plus juste"],
  tools:     ["Outils", "Sait appeler des fonctions"],
  audio:     ["Audio", "Comprend le son"],
  embedding: ["Embeddings", "Sert à rapprocher des textes : ne converse pas"],
};

// Fonctions de Boostache → modèle utilisé (réglage côté Python : ollama_admin.USES)
const USES = [
  ["chat", "Conversations", "last_model", "Modèle des nouvelles conversations"],
  ["help", "Aide contextuelle", "help_model", "Ctrl+Impr. écran ; de préférence un modèle qui lit les images"],
  ["assist", "WhatsApp et Gmail", "assist_model", "Assistant de réponse sous les sites intégrés"],
  ["live", "Assistant live", "live_model", "De préférence un modèle qui lit les images"],
];

// Proposés en tête de la bibliothèque, quand rien n'est cherché
const SUGGESTED = {
  "gemma4":      "Google. Lit les images et s'exprime bien en français ; e2b et e4b sont rapides.",
  "qwen3.5":     "Alibaba. Lit les images et sait réfléchir, en de nombreuses tailles.",
  "ministral-3": "Mistral AI. Lit les images ; tailles 3b, 8b et 14b.",
  "qwen3-vl":    "Spécialisé dans les images : lit bien le texte des captures d'écran.",
  "gemma3":      "Génération précédente de Gemma, éprouvée ; lit les images.",
};

const CTX_SIZES = [[4096, "4K"], [8192, "8K"], [16384, "16K"], [32768, "32K"], [65536, "64K"]];
const KEEP = [["0", "Aussitôt"], ["5m", "5 min"], ["30m", "30 min"], ["2h", "2 h"], ["-1", "Jamais"]];
const CTX_HINT = "Ce que le modèle garde en tête : la conversation, les fichiers joints, la capture et sa réflexion. "
  + "Plus grand : conversations plus longues, mais plus de mémoire et des réponses plus lentes. "
  + "8K suffit d'ordinaire ; 16K ou plus pour de longs documents.";
const KEEP_HINT = "Après un appel, le modèle reste chargé ce temps-là : la réponse suivante part sans attendre. "
  + "Aussitôt : la mémoire est rendue tout de suite, mais chaque réponse recharge le modèle (quelques secondes).";

// ─────────────────────────────────────────────
//  Mise en forme
// ─────────────────────────────────────────────
/** Octets → « 4,6 Go » (unités décimales, comme ollama.com). */
function bytes(n) {
  if (!n) return "";
  if (n < 1e9) return `${Math.max(1, Math.round(n / 1e6))} Mo`;
  const gb = n / 1e9;
  return `${gb.toLocaleString("fr-FR", { maximumFractionDigits: gb < 100 ? 1 : 0 })} Go`;
}

/** Mémoire (RAM, GPU) → « 16 Go », « 4,6 Go » avec decimals = 1. */
const memory = (n, decimals = 0) =>
  `${(n / 1073741824).toLocaleString("fr-FR", { maximumFractionDigits: decimals })} Go`;

/** 131072 → « 128K ». */
const tokens = (n) => (n >= 1024 ? `${Math.round(n / 1024)}K` : String(n || ""));

/** « 120.2M » (ollama.com) → « 120,2 M ». */
const pulls = (s) => s.replace(".", ",").replace(/([KMB])$/, (_, u) => ({ K: " k", M: " M", B: " Md" }[u]));

function ago(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = (Date.now() - d) / 1000;
  const rtf = new Intl.RelativeTimeFormat("fr", { numeric: "auto" });
  for (const [unit, secs] of [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400],
    ["hour", 3600], ["minute", 60]]) {
    if (s >= secs) return rtf.format(-Math.floor(s / secs), unit);
  }
  return "à l'instant";
}

/** Durée restante → « 4 min », « 1 h 05 », « 40 s ». */
function eta(seconds) {
  const s = Math.max(1, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;
}

/** « gemma4:e2b » → « gemma4 » ; « e2b ». */
const baseName = (name) => name.replace(/:[^:/]*$/, "");
const tagOf = (name) => (/:([^:/]*)$/.exec(name) || [, "latest"])[1];

function modelUrl(name) {
  const base = baseName(name);
  if (base.startsWith("hf.co/")) return `https://huggingface.co/${base.slice(6)}`;
  return base.includes("/") ? `${SITE}/${base}` : `${SITE}/library/${base}`;
}

function capBadges(caps) {
  return (caps || []).filter((c) => CAPS[c]).map((c) =>
    h("span", { class: `om-cap cap-${c}`, text: CAPS[c][0], dataset: { tip: CAPS[c][1] } }));
}

export function createOllamaView(ctx, state) {
  const { api, on, store } = ctx;
  const init = state.ollama || {};
  const S = {
    available: init.available !== false,
    info: { installed: init.installed, online: !!init.online, starting: !!init.starting, gpus: [], ram: 0 },
    loaded: false,
    models: [],
    running: [],
    uses: {},
    pulls: new Map((init.pulls || []).map((p) => [p.name, p])),
    checking: false,          // vérification des mises à jour
    library: null,            // modèles d'ollama.com (ordre de popularité)
    libraryError: "",
    libraryLoading: false,
    query: "",
    limit: PAGE,
    open: new Set(),          // modèles de la bibliothèque dépliés (variantes)
    variants: new Map(),      // nom → { loading, list, error, all }
    sizes: new Map(),         // nom → variantes principales et leur taille (null : inconnues)
    sizing: new Set(),        // tailles demandées, pas encore reçues
    fitOnly: readFitOnly(),   // bibliothèque : seulement les modèles adaptés à ce PC
  };
  let active = false;
  let visible = true;
  let pollTimer = null;

  // ── En-tête ─────────────────────────────────
  const sub = h("span", { class: "view-sub" });
  const refreshBtn = iconButton("refresh-cw", "Actualiser", () => refresh(), { size: 15, cls: "sm" });

  // ── Serveur ─────────────────────────────────
  const status = h("div", { class: "om-status" });
  const statusActions = h("div", { class: "om-status-actions" });
  const facts = h("div", { class: "om-facts" });
  const dirCode = h("code");
  const diskText = h("div", { class: "hint-text" });
  const autoSwitch = h("button", { class: "switch", type: "button", role: "switch",
    "aria-label": "Lancer Ollama avec Boostache", onClick: toggleAutostart });
  const serverCard = card("cpu", "Serveur", null,
    h("div", { class: "row" }, status, statusActions),
    facts,
    h("div", { class: "om-block" },
      h("div", { class: "label", text: "Dossier des modèles" }),
      h("div", { class: "path-box" }, dirCode,
        h("button", { class: "btn sm", type: "button", onClick: openModelsDir }, ico("folder-open", 14), "Ouvrir")),
      diskText),
    h("div", { class: "row om-block" },
      h("div", {}, h("div", { text: "Lancer Ollama avec Boostache" }),
        h("div", { class: "hint-text",
          text: "S'il ne répond pas quand Boostache démarre (arrêté, ou fermé par une mise à jour qui a échoué)." })),
      autoSwitch));

  // ── Modèles installés ───────────────────────
  const summary = h("span", { class: "muted" });
  const updatesBtn = h("button", { class: "btn ghost sm", type: "button", onClick: checkUpdates });
  const modelList = h("div", { class: "om-list" });
  const modelsCard = card("box", "Modèles installés", null,
    h("div", { class: "om-toolbar" }, summary, h("div", { class: "head-spacer" }), updatesBtn),
    modelList);

  // ── Téléchargement ──────────────────────────
  const pullList = h("div", { class: "om-pulls" });
  const search = h("input", { class: "field", type: "text", spellcheck: "false", autocomplete: "off",
    placeholder: "Rechercher un modèle, ou son nom exact (ex. gemma4:e4b)", "aria-label": "Rechercher un modèle" });
  const fitSeg = h("div", { class: "segmented om-fit-seg" });
  const machine = h("div", { class: "om-machine" });
  const libList = h("div", { class: "om-library" });
  const libFoot = h("div", { class: "om-lib-foot" });
  const downloadCard = card("download", "Télécharger un modèle",
    "Bibliothèque d'ollama.com. Plus un modèle est gros, meilleures sont ses réponses, "
    + "mais plus il est lent et gourmand en mémoire.",
    pullList,
    h("div", { class: "om-search" }, ico("search", 15), search),
    h("div", { class: "om-lib-tools" }, fitSeg, machine),
    libList, libFoot);

  // ── Options ─────────────────────────────────
  const ctxSeg = h("div", { class: "segmented" });
  const keepSeg = h("div", { class: "segmented" });
  const usesGrid = h("div", { class: "om-uses" });
  const optionsCard = card("gauge", "Options", "Pour tous les appels de Boostache à Ollama.",
    h("div", { class: "row" }, h("span", { text: "Contexte" }), ctxSeg),
    h("div", { class: "hint-text", text: CTX_HINT }),
    h("div", { class: "row om-block" }, h("span", { text: "Libérer la mémoire" }), keepSeg),
    h("div", { class: "hint-text", text: KEEP_HINT }),
    h("div", { class: "label om-block", text: "Modèle de chaque fonction" }),
    usesGrid);

  const unavailable = h("div", { class: "empty" },
    h("div", { class: "glyph" }, ico("cpu", 22)),
    h("h3", { text: "Ollama indisponible" }),
    h("p", { text: `Boostache ne peut pas piloter Ollama : ${init.reason || "erreur inconnue"}.` }));

  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Ollama" }), sub,
      h("div", { class: "head-spacer" }),
      h("button", { class: "btn ghost sm", type: "button", onClick: () => api.open_url(`${SITE}/library`) },
        ico("external-link", 14), "ollama.com"),
      refreshBtn),
    S.available
      ? h("div", { class: "settings" }, h("div", { class: "settings-inner" },
        serverCard, modelsCard, downloadCard, optionsCard))
      : unavailable);

  // ─────────────────────────────────────────────
  //  État
  // ─────────────────────────────────────────────
  let refreshing = null;
  let again = false;

  /** Relit tout (serveur, modèles, mémoire) ; un appel pendant une lecture en relance une après. */
  async function refresh() {
    if (!S.available) return;
    if (refreshing) { again = true; return; }
    refreshBtn.classList.add("busy");
    refreshing = (async () => {
      do {
        again = false;
        const res = await api.ollama_state();
        if (res) apply(res);
      } while (again);
    })();
    try { await refreshing; } finally { refreshing = null; refreshBtn.classList.remove("busy"); }
    S.loaded = true;
    renderAll();
  }
  const refreshSoon = debounce(refresh, 250);

  function apply(res) {
    S.info = res;
    S.models = res.models || [];
    S.running = res.running || [];
    S.uses = res.uses || {};
    for (const p of res.pulls || []) S.pulls.set(p.name, p);
  }

  function renderAll() {
    renderHead();
    renderServer();
    renderModels();
    renderPulls();
    renderLibrary();
    renderOptions();
    syncPoll();
  }

  function renderHead() {
    const i = S.info;
    sub.textContent = i.starting ? "Démarrage…" : i.online ? `En marche${i.version ? ` · ${i.version}` : ""}`
      : i.installed === false ? "Non installé" : "Arrêté";
  }

  // ─────────────────────────────────────────────
  //  Serveur
  // ─────────────────────────────────────────────
  function renderServer() {
    const i = S.info;
    const kind = i.starting ? "busy" : i.online ? "on" : "off";
    const text = i.starting ? "Démarrage d'Ollama…"
      : i.online ? `Ollama est en marche${i.version ? ` — version ${i.version}` : ""}`
      : i.installed === false ? "Ollama n'est pas installé sur ce PC"
      : "Ollama ne répond pas";
    status.className = `om-status ${kind}`;
    status.replaceChildren(i.starting ? ico("loader-circle", 14) : h("span", { class: "om-dot" }),
      h("span", { text }));
    statusActions.replaceChildren(...[
      !i.online && !i.starting && i.installed !== false
        ? h("button", { class: "btn primary sm", type: "button", onClick: start }, ico("power", 14), "Lancer Ollama") : null,
      i.installed === false
        ? h("button", { class: "btn primary sm", type: "button", onClick: () => api.ollama_install_page() },
          ico("external-link", 14), "Installer Ollama") : null,
    ].filter(Boolean));

    const gpus = (i.gpus || []).map((g) => `${g.name} (${memory(g.vram)})`);
    facts.replaceChildren(...[
      i.host ? fact("Adresse", i.host) : null,
      fact(gpus.length > 1 ? "Cartes graphiques" : "Carte graphique",
        gpus.length ? gpus.join(", ") : S.loaded && i.online ? "aucune utilisée : le processeur calcule" : "—"),
      i.ram ? fact("Mémoire du PC", memory(i.ram)) : null,
    ].filter(Boolean));
    dirCode.textContent = i.models_dir || "—";
    dirCode.dataset.tip = i.models_dir || "";
    diskText.textContent = i.models_dir
      ? [i.used ? `${bytes(i.used)} occupés par les modèles` : "", i.free != null ? `${bytes(i.free)} libres sur le disque` : ""]
        .filter(Boolean).join(" · ") : "";
    syncSwitch(autoSwitch, store.settings.ollama_autostart);
  }

  function fact(label, value) {
    return h("div", { class: "om-fact" }, h("span", { class: "muted", text: label }), h("span", { text: value }));
  }

  async function start() {
    S.info.starting = true;
    renderHead();
    renderServer();
    const res = await api.ollama_start();
    if (!res?.ok) {
      S.info.starting = false;
      if (res?.missing) S.info.installed = false;
      toast(res?.error || "Lancement d'Ollama impossible.", "error");
      renderHead();
      renderServer();
    }
  }

  async function openModelsDir() {
    if (!await api.ollama_open_models_dir()) toast("Le dossier des modèles n'existe pas encore.", "info");
  }

  async function toggleAutostart() {
    await ctx.saveSetting("ollama_autostart", !store.settings.ollama_autostart);
    syncSwitch(autoSwitch, store.settings.ollama_autostart);
  }

  // ─────────────────────────────────────────────
  //  Modèles installés
  // ─────────────────────────────────────────────
  function renderModels() {
    const i = S.info;
    updatesBtn.replaceChildren(ico(S.checking ? "loader-circle" : "refresh-cw", 14),
      S.checking ? "Vérification…" : "Vérifier les mises à jour");
    updatesBtn.classList.toggle("busy", S.checking);
    updatesBtn.disabled = S.checking || !i.online || !S.models.length;
    if (!i.online) {
      summary.textContent = "";
      modelList.replaceChildren(h("div", { class: "om-empty", text: S.loaded
        ? "Ollama ne répond pas : lance-le pour voir et gérer les modèles installés."
        : "Lecture des modèles…" }));
      return;
    }
    const total = S.models.reduce((n, m) => n + (m.size || 0), 0);
    const updates = S.models.filter((m) => m.update).length;
    summary.textContent = S.models.length
      ? `${S.models.length} modèle${S.models.length > 1 ? "s" : ""} · ${bytes(total)}`
        + (updates ? ` · ${updates} mise${updates > 1 ? "s" : ""} à jour disponible${updates > 1 ? "s" : ""}` : "")
      : "";
    modelList.replaceChildren(...(S.models.length ? S.models.map(modelRow)
      : [h("div", { class: "om-empty", text: "Aucun modèle installé : choisis-en un dans la bibliothèque ci-dessous." })]));
  }

  function modelRow(m) {
    const run = S.running.find((r) => r.name === m.name);
    const job = S.pulls.get(m.name);      // (pas « pull » : c'est la fonction qui télécharge)
    const pulling = job && !job.done;
    const used = USES.filter(([id]) => S.uses[id] === m.name);
    const meta = [m.params, m.quant, bytes(m.size), m.context ? `contexte ${tokens(m.context)}` : "",
      m.modified ? `modifié ${ago(m.modified)}` : ""].filter(Boolean);
    const variants = m.variants > 1 ? h("span", { class: "om-variants-note", text: `${m.variants} variantes`,
      dataset: { tip: "Ollama garde une variante par moteur de calcul : la taille les additionne" } }) : null;
    const menu = () => modelMenu(m, run);
    return h("div", {
      class: `om-model${run ? " loaded" : ""}`,
      onContextmenu: (ev) => { ev.preventDefault(); openMenu(menu(), ev.clientX, ev.clientY); },
    },
      h("div", { class: "om-glyph", dataset: { tip: run ? "Chargé en mémoire" : null } }, ico("box", 17)),
      h("div", { class: "om-model-text" },
        h("div", { class: "om-model-title" },
          h("span", { class: "om-name", text: m.name }),
          ...capBadges(m.capabilities),
          m.remote ? h("span", { class: "om-tag", text: "En ligne", dataset: { tip: "Tourne sur les serveurs d'Ollama, pas sur ce PC" } }) : null,
          m.update && pulling ? h("span", { class: "om-tag warn", text: "mise à jour" }) : null),
        pulling ? pullLine(job) : h("div", { class: "om-meta" }, h("span", { text: meta.join(" · ") }), variants),
        used.length ? h("div", { class: "om-used" }, ico("sparkles", 12),
          h("span", { text: `Utilisé par : ${used.map(([, label]) => label).join(", ")}` })) : null,
        run ? h("div", { class: "om-mem" }, h("span", { class: "om-dot" }), h("span", { text: memText(run) }),
          h("button", { class: "om-link", type: "button", text: "Libérer", onClick: () => unload(m.name) })) : null),
      h("div", { class: "om-actions" },
        m.update && !pulling
          ? h("button", { class: "btn sm", type: "button", onClick: () => pull(m.name) }, ico("download", 14), "Mettre à jour")
          : null,
        iconButton("ellipsis", "Plus d'actions", (ev) => openMenuBelow(menu(), ev.currentTarget, "right"),
          { size: 15, cls: "sm" })));
  }

  function memText(r) {
    const gpu = r.size ? Math.round((r.vram / r.size) * 100) : 0;
    const where = gpu >= 100 ? "sur le GPU" : gpu <= 0 ? "sur le processeur" : `dont ${gpu} % sur le GPU`;
    const parts = [`En mémoire : ${bytes(r.size)} ${where}`];
    if (r.context) parts.push(`contexte ${tokens(r.context)}`);
    const until = new Date(r.expires);
    const left = (until - Date.now()) / 1000;
    if (!Number.isNaN(left)) {
      parts.push(left > 86400 * 365 ? "gardé en mémoire" : left < 60 ? "libéré dans moins d'une minute"
        : left < 3600 ? `libéré dans ${Math.round(left / 60)} min` : `libéré à ${formatHour(until)}`);
    }
    return parts.join(" · ");
  }

  function modelMenu(m, run) {
    const embedOnly = m.capabilities?.includes("embedding") && !m.capabilities.includes("completion");
    return [
      { label: "Utiliser pour", icon: "sparkles", disabled: embedOnly,
        submenu: USES.map(([id, label]) => ({ label, checked: S.uses[id] === m.name, onSelect: () => use(id, m.name) })) },
      "-",
      m.registry && !m.remote ? { label: "Mettre à jour", icon: "download", hint: m.update ? "disponible" : "",
        onSelect: () => pull(m.name) } : null,
      run ? { label: "Libérer la mémoire", icon: "memory-stick", onSelect: () => unload(m.name) } : null,
      { label: "Copier le nom", icon: "copy", onSelect: () => ctx.copy(m.name, "Nom du modèle copié") },
      m.registry ? { label: "Voir sur ollama.com", icon: "external-link", onSelect: () => api.open_url(modelUrl(m.name)) } : null,
      "-",
      { label: "Supprimer…", icon: "trash-2", danger: true, onSelect: () => confirmDelete(m) },
    ];
  }

  async function use(id, model) {
    const res = await api.ollama_use(id, model);
    if (!res) return;
    const key = USES.find(([u]) => u === id)[2];
    store.settings[key] = model;
    S.uses = res;
    renderModels();
    renderOptions();
    toast(`${USES.find(([u]) => u === id)[1]} : ${model}`);
  }

  async function unload(name) {
    const res = await api.ollama_unload(name);
    if (!res?.ok) { toast(res?.error || "Mémoire non libérée.", "error"); return; }
    S.running = S.running.filter((r) => r.name !== name);
    renderModels();
    toast(`Mémoire de ${name} libérée`);
  }

  function confirmDelete(m) {
    const used = USES.filter(([id]) => S.uses[id] === m.name).map(([, label]) => label);
    const others = S.models.length > 1;
    openModal({
      title: `Supprimer ${m.name} ?`,
      width: 460,
      body: h("div", { class: "om-confirm" },
        h("p", { text: `Le modèle sera effacé du disque (${bytes(m.size)}, moins ce qu'il partage avec d'autres modèles). `
          + "Il pourra être téléchargé à nouveau." }),
        used.length ? h("p", { class: "muted", text: `Utilisé par : ${used.join(", ")}. `
          + (others ? "Un autre modèle installé prendra le relais." : "Ces fonctions n'auront plus de modèle.") }) : null),
      actions: [
        { label: "Annuler", kind: "ghost" },
        // Pas « primary » : la modale donnerait le focus à ce bouton, et Entrée supprimerait
        { label: "Supprimer", icon: "trash-2", kind: "danger", onClick: async () => {
          const res = await api.ollama_delete(m.name);
          if (!res?.ok) { toast(res?.error || "Suppression impossible.", "error"); return false; }
          toast(`${m.name} supprimé`);
          refresh();
          return true;
        } },
      ],
    });
  }

  async function checkUpdates() {
    S.checking = true;
    renderModels();
    const res = await api.ollama_check_updates();
    S.checking = false;
    if (!res?.ok) {
      toast(res?.error || "Vérification impossible.", "error");
    } else {
      for (const m of S.models) if (m.name in res.updates) m.update = res.updates[m.name];
      const names = Object.entries(res.updates).filter(([, u]) => u).map(([n]) => n);
      toast(names.length ? `Mise à jour disponible : ${names.join(", ")}` : "Tous les modèles sont à jour.",
        names.length ? "info" : "success");
    }
    renderModels();
    renderLibrary();
  }

  // ─────────────────────────────────────────────
  //  Téléchargements
  // ─────────────────────────────────────────────
  async function pull(name) {
    if (!S.info.online) { toast("Ollama ne répond pas : lance-le d'abord.", "error"); return; }
    const res = await api.ollama_pull(name);
    if (!res?.ok) { toast(res?.error || "Téléchargement impossible.", "error"); return; }
    S.pulls.set(res.name, res.pull);
    renderPulls();
    renderModels();
    renderLibrary();
  }

  function cancel(name) {
    api.ollama_cancel(name);
  }

  function dismiss(name) {
    api.ollama_dismiss(name);
    S.pulls.delete(name);
    renderPulls();
  }

  function pullText(p) {
    if (p.done) return p.error || (p.cancelled ? "Annulé" : p.update ? "Mis à jour" : "Installé");
    if (!p.total || !["Téléchargement", "Préparation…"].includes(p.status)) return p.status;
    const parts = [`${bytes(p.completed) || "0 Mo"} sur ${bytes(p.total)}`];
    if (p.speed) parts.push(`${bytes(p.speed)}/s`);
    if (p.speed && p.total > p.completed) parts.push(`encore ${eta((p.total - p.completed) / p.speed)}`);
    return parts.join(" · ");
  }

  /** Barre et texte de progression (aussi dans la ligne d'un modèle mis à jour). */
  function pullLine(p) {
    const ratio = p.total ? Math.min(1, p.completed / p.total) : 0;
    return h("div", { class: "om-progress", dataset: { pull: p.name } },
      h("div", { class: "om-bar" }, h("span", { style: { transform: `scaleX(${ratio})` } })),
      h("span", { class: "om-progress-text", text: progressText(p) }));
  }

  const ratioOf = (p) => (p.total ? Math.min(1, p.completed / p.total) : 0);
  const progressText = (p) => `${p.total ? `${Math.floor(ratioOf(p) * 100)} % · ` : ""}${pullText(p)}`;

  /** Pourcentage seul (bouton de la variante conseillée) : prefix avant le nombre. */
  function pullPercent(p, prefix = "") {
    return h("span", { class: "om-rec", dataset: { pull: p.name, prefix },
      text: `${prefix}${Math.floor(ratioOf(p) * 100)} %` });
  }

  /** Nouvelle progression d'un téléchargement : barres et textes mis à jour sur place,
   *  sans redessiner les listes (un clic en cours sur « Annuler » n'est pas perdu). */
  function updateProgress(p) {
    for (const node of el.querySelectorAll(`[data-pull="${CSS.escape(p.name)}"]`)) {
      if (node.classList.contains("om-progress")) {
        node.querySelector(".om-bar span").style.transform = `scaleX(${ratioOf(p)})`;
        node.querySelector(".om-progress-text").textContent = progressText(p);
      } else {
        node.textContent = `${node.dataset.prefix || ""}${Math.floor(ratioOf(p) * 100)} %`;
      }
    }
  }

  function renderPulls() {
    const list = [...S.pulls.values()];
    pullList.hidden = !list.length;
    pullList.replaceChildren(...list.map((p) => {
      const kind = !p.done ? "run" : p.error ? "error" : p.cancelled ? "cancelled" : "ok";
      const glyph = { run: "download", error: "triangle-alert", cancelled: "x", ok: "check" }[kind];
      return h("div", { class: `om-pull ${kind}` },
        h("span", { class: "om-pull-icon" }, ico(glyph, 14)),
        h("div", { class: "om-pull-main" },
          h("div", { class: "om-pull-head" },
            h("span", { class: "om-name", text: p.name }),
            p.update && !p.done ? h("span", { class: "om-tag", text: "mise à jour" }) : null),
          kind === "run" ? pullLine(p) : h("div", { class: "om-pull-text", text: pullText(p) })),
        kind === "error" ? h("button", { class: "btn ghost sm", type: "button", onClick: () => pull(p.name) },
          ico("rotate-ccw", 14), "Réessayer") : null,
        iconButton("x", p.done ? "Retirer" : "Annuler le téléchargement",
          () => (p.done ? dismiss(p.name) : cancel(p.name)), { size: 14, cls: "sm" }));
    }));
    const running = list.filter((p) => !p.done).length;
    ctx.setBadge("ollama", running ? "dot" : 0);
  }

  on("ollama:pull", (p) => {
    const before = S.pulls.get(p.name);
    S.pulls.set(p.name, p);
    if (p.done && !before?.done) {
      if (p.error) toast(`${p.name} : ${p.error}`, "error");
      else if (!p.cancelled) toast(`${p.name} ${p.update ? "est à jour" : "est installé"}`);
      // Terminé sans problème : retiré de la liste après quelques secondes (sauf s'il a été relancé)
      if (!p.error) setTimeout(() => { if (S.pulls.get(p.name)?.done) dismiss(p.name); }, p.cancelled ? 2500 : 5000);
    }
    if (!before || before.done !== p.done) {
      // Début ou fin : les listes changent (ligne de progression, bouton, « Installé »)
      renderPulls();
      renderModels();
      renderLibrary();
    } else {
      updateProgress(p);
    }
  });

  // ─────────────────────────────────────────────
  //  Bibliothèque d'ollama.com
  // ─────────────────────────────────────────────
  async function loadLibrary(force = false) {
    if (S.libraryLoading) return;
    S.libraryLoading = true;
    renderLibrary();
    const res = await api.ollama_library(force);
    S.libraryLoading = false;
    if (res?.ok) { S.library = res.models; S.libraryError = ""; }
    else S.libraryError = res?.error || "Bibliothèque indisponible.";
    renderLibrary();
  }

  function haystack(m) {
    const caps = m.capabilities.map((c) => `${c} ${CAPS[c]?.[0] || ""}`).join(" ");
    return `${m.name} ${m.description} ${caps} ${m.sizes.join(" ")}`.toLowerCase();
  }

  /** Modèles de la bibliothèque qui correspondent à la recherche : nom exact,
   *  puis début du nom, puis nom, puis description ; à égalité, les plus téléchargés. */
  function matches(query) {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean).map((w) => w.replace(/:.*$/, "")).filter(Boolean);
    const first = words[0] || "";
    const scored = [];
    (S.library || []).forEach((m, i) => {
      const text = haystack(m);
      if (!words.every((w) => text.includes(w))) return;
      const name = m.name.toLowerCase();
      scored.push([name === first ? 0 : name.startsWith(first) ? 1 : name.includes(first) ? 2 : 3, i, m]);
    });
    return scored.sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
  }

  function renderLibrary() {
    const q = S.query.trim();
    const rows = [];
    const shown = [];
    const direct = NAME_RE.test(q) && (q.includes(":") || q.includes("/")) ? q : "";
    if (direct) rows.push(directRow(direct));
    let more = false;
    if (!S.library) {
      rows.push(S.libraryError
        ? h("div", { class: "om-empty error" }, h("span", { text: S.libraryError }),
          h("button", { class: "btn ghost sm", type: "button", onClick: () => loadLibrary(true) }, ico("rotate-ccw", 14), "Réessayer"),
          h("button", { class: "btn ghost sm", type: "button", onClick: () => api.open_url(`${SITE}/library`) },
            ico("external-link", 14), "Ouvrir ollama.com"))
        : h("div", { class: "om-empty" }, ico("loader-circle", 14), h("span", { text: "Lecture de la bibliothèque d'ollama.com…" })));
    } else if (!q) {
      const suggested = Object.keys(SUGGESTED).map((n) => S.library.find((m) => m.name === n)).filter(Boolean);
      const rest = S.library.filter((m) => !SUGGESTED[m.name] && (!S.fitOnly || adapted(m)));
      shown.push(...suggested, ...rest.slice(0, S.limit));
      rows.push(h("div", { class: "om-section", text: "Suggestions pour Boostache" }), ...suggested.map(libRow),
        h("div", { class: "om-section", text: S.fitOnly ? "Les plus téléchargés parmi ceux adaptés à ce PC" : "Les plus téléchargés" }),
        ...rest.slice(0, S.limit).map(libRow));
      more = rest.length > S.limit;
    } else {
      const all = matches(q);
      const found = S.fitOnly ? all.filter(adapted) : all;
      shown.push(...found.slice(0, S.limit));
      rows.push(...found.slice(0, S.limit).map(libRow));
      more = found.length > S.limit;
      if (!all.length && !direct) {
        rows.push(h("div", { class: "om-empty", text: `Aucun modèle « ${q} » dans la bibliothèque d'ollama.com.` }));
      } else if (all.length > found.length) {
        const hidden = all.length - found.length;
        rows.push(h("div", { class: "om-hidden" },
          h("span", { text: `${hidden} autre${hidden > 1 ? "s" : ""} modèle${hidden > 1 ? "s" : ""} trop lourd${hidden > 1 ? "s" : ""} `
            + "pour ce PC, ou qui ne converse" + (hidden > 1 ? "nt" : "") + " pas" }),
          h("button", { class: "btn ghost sm", type: "button", onClick: () => setFitOnly(false) }, "Les afficher")));
      }
    }
    renderLibTools();
    wantSizes(shown.map((m) => m.name));
    libList.replaceChildren(...rows);
    libFoot.replaceChildren(...(more
      ? [h("button", { class: "btn ghost sm", type: "button", onClick: () => { S.limit += 20; renderLibrary(); } },
        ico("chevron-down", 14), "Afficher plus")]
      : []));
  }

  function directRow(name) {
    const local = S.models.find((m) => m.name === name || m.name === `${name}:latest`);
    const busy = S.pulls.get(name)?.done === false;
    return h("div", { class: "om-direct" },
      ico("download", 15),
      h("span", {}, local ? "Déjà installé : " : "Télécharger ", h("span", { class: "om-name", text: name })),
      h("div", { class: "head-spacer" }),
      h("button", { class: "btn primary sm", type: "button", disabled: busy || !S.info.online,
        onClick: () => pull(name) }, local ? "Mettre à jour" : "Télécharger"));
  }

  function libRow(m) {
    const open = S.open.has(m.name);
    const installed = S.models.filter((x) => baseName(x.name) === m.name).map((x) => tagOf(x.name));
    const note = SUGGESTED[m.name];
    const f = fitOf(m);
    const byTag = new Map(f.list.map((v) => [tagOf(v.name).toLowerCase(), v]));
    const misfit = f.kind === "cpu" || f.kind === "big";
    const head = h("div", {
      class: "om-lib-head", role: "button", tabindex: "0", "aria-expanded": String(open),
      onClick: () => toggleLib(m.name),
      onKeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggleLib(m.name); } },
    },
      h("div", { class: "om-lib-main" },
        h("div", { class: "om-model-title" },
          h("span", { class: "om-name", text: m.name }),
          ...capBadges(m.capabilities),
          misfit ? h("span", { class: `om-fit ${f.kind}`, text: FIT_LABEL[f.kind],
            dataset: { tip: (f.estimated ? "Estimation : " : "") + fitTip(f.kind) } }) : null,
          installed.length ? h("span", { class: "om-tag ok", text: `installé : ${installed.join(", ")}` }) : null),
        h("div", { class: "om-lib-desc", text: note || m.description,
          dataset: { tip: note ? m.description : null, tipWrap: note ? "" : null } }),
        h("div", { class: "om-meta" },
          ...m.sizes.map((s) => {
            const v = byTag.get(s.toLowerCase());
            return h("span", { class: `om-size${v ? ` fit-${v.fit.kind}` : ""}`, text: s,
              dataset: { tip: v ? `${v.name} · ${v.estimated ? "≈ " : ""}${bytes(v.size)} — ${FIT_LABEL[v.fit.kind]}` : null } });
          }),
          h("span", { text: [m.pulls ? `${pulls(m.pulls)} téléchargements` : "", m.updated ? `mis à jour ${m.updated}` : ""]
            .filter(Boolean).join(" · ") }))),
      recommend(f),
      ico(open ? "chevron-down" : "chevron-right", 15));
    return h("div", { class: `om-lib${open ? " open" : ""}${misfit ? " misfit" : ""}` }, head, open ? variantsPanel(m) : null);
  }

  /** Variante conseillée pour ce PC : la plus grande qui tient dans la carte
   *  graphique (tailles exactes seulement, pas sur une estimation). */
  function recommend(f) {
    const b = f.best;
    if (!b || f.estimated) return null;
    const tag = tagOf(b.name);
    const tip = b.fit.kind !== "gpu"
      ? `Conseillé pour ce PC : la plus grande variante qui tient en mémoire (${bytes(b.size)})`
      : b.fit.partial
        ? `Conseillé pour ce PC : la plus grande variante rapide ici (${bytes(b.size)}, `
          + "dont une partie seulement va sur la carte graphique)"
        : `Conseillé pour ce PC : la plus grande variante qui tient dans la carte graphique (${bytes(b.size)})`;
    const job = S.pulls.get(b.name);
    if (job && !job.done) return pullPercent(job, `${tag} · `);
    if (S.models.some((x) => x.name === b.name)) {
      return h("span", { class: "om-rec ok", dataset: { tip } }, ico("check", 13), `${tag} installé`);
    }
    return h("button", { class: "btn sm om-rec-btn", type: "button", disabled: !S.info.online,
      dataset: { tip: S.info.online ? tip : "Ollama ne répond pas" },
      onClick: (ev) => { ev.stopPropagation(); pull(b.name); },
      onKeydown: (ev) => ev.stopPropagation() },
    ico("download", 14), `${tag} · ${bytes(b.size)}`);
  }

  // ── Adapté à ce PC ? ───────────────────────
  const FIT_LABEL = { gpu: "Rapide ici", ok: "Tient ici", cpu: "Plus lent ici", big: "Trop gros ici" };

  function fitTip(kind) {
    const gpu = gpuBudget();
    const ram = S.info.ram || 0;
    return {
      gpu: `Tient dans la carte graphique (${memory(gpu, 1)} disponibles pour Ollama) : réponses rapides`,
      ok: `Tient dans la mémoire du PC (${memory(ram)}) ; calculé par le processeur`,
      cpu: `Plus grand que ce que la carte graphique peut prendre (${memory(gpu, 1)}) : `
        + "une partie tourne sur le processeur, réponses plus lentes",
      big: `Plus grand que la mémoire de ce PC (${memory(ram)}) : très lent, voire impossible à charger`,
    }[kind];
  }

  const gpuBudget = () => Math.max(0, ...(S.info.gpus || []).map((g) => g.available || g.vram * 0.8));
  const currentCtx = () => Number(store.settings.ollama_num_ctx) || S.info.default_ctx || 8192;

  /** Où tourne une variante sur ce PC, d'après sa taille (octets), le contexte choisi,
   *  la mémoire de la carte graphique disponible pour Ollama et celle du PC. */
  function classify(name, size) {
    if (!size) return null;
    const gpu = gpuBudget();
    const ram = S.info.ram || 0;
    const ctxScale = currentCtx() / 8192;
    const need = size * (1 + 0.2 * ctxScale) + 0.3e9;       // poids + contexte (KV) + calcul
    // Modèles « e » de Gemma : une grande part des poids reste en mémoire vive
    const e = /^e(\d+(?:\.\d+)?)b$/i.exec(tagOf(name).split("-")[0]);
    const gpuNeed = e ? Number(e[1]) * 0.7e9 + 0.4e9 : need;
    const kind = ram && need > ram * 0.7 + gpu ? "big" : !gpu ? (ram ? "ok" : null) : gpuNeed <= gpu ? "gpu" : "cpu";
    return kind && { kind, tip: fitTip(kind), partial: !!e };
  }

  /** Tailles estimées d'après les étiquettes (« 4b », « e2b ») en attendant les vraies. */
  function estimate(m) {
    const vision = m.capabilities.includes("vision");
    return m.sizes.map((label) => {
      const x = SIZE_LABEL.exec(label);
      if (!x) return null;
      const n = Number(x[3]) * (x[4].toLowerCase() === "m" ? 1e-3 : 1) * Number(x[1] || 1);
      const gb = x[2] ? 2.4 + 1.1 * n : n * 0.62 + (vision ? 0.7 : 0) + 0.05;
      return { name: `${m.name}:${label}`, size: gb * 1e9, estimated: true };
    }).filter(Boolean);
  }

  /** Variantes d'un modèle de la bibliothèque, avec où elles tournent ici, et la
   *  meilleure : la plus grande qui tient dans la carte graphique. */
  function fitOf(m) {
    const real = S.sizes.get(m.name);
    let list = estimate(m);
    if (real?.length) {
      const labels = new Set(m.sizes.map((s) => s.toLowerCase()));
      const sized = real.filter((v) => labels.has(tagOf(v.name).toLowerCase()));
      list = sized.length ? sized : real.filter((v) => tagOf(v.name) !== "latest");
      if (!list.length) list = real;
    }
    list = list.map((v) => ({ ...v, fit: classify(v.name, v.size) })).filter((v) => v.fit);
    const good = list.filter((v) => v.fit.kind === "gpu" || v.fit.kind === "ok").sort((a, b) => b.size - a.size);
    const kind = good.length ? good[0].fit.kind : list.some((v) => v.fit.kind === "cpu") ? "cpu" : list.length ? "big" : null;
    return { list, best: good[0] || null, kind, estimated: !real?.length };
  }

  /** Modèle mis en avant par le filtre : il converse et tourne vite ici. */
  const adapted = (m) => !m.capabilities.includes("embedding") && ["gpu", "ok"].includes(fitOf(m).kind);

  function renderLibTools() {
    fitSeg.replaceChildren(...[[true, "Adaptés à ce PC"], [false, "Tous les modèles"]].map(([value, label]) =>
      h("button", { type: "button", class: S.fitOnly === value ? "active" : "", text: label,
        dataset: { tip: value ? "Modèles qui conversent et dont une taille tient dans la carte graphique" : null },
        onClick: () => setFitOnly(value) })));
    const g = (S.info.gpus || [])[0];
    machine.replaceChildren(
      h("span", { text: g ? `${g.name.replace(/^NVIDIA (GeForce )?/, "")} : ${memory(gpuBudget(), 1)} pour Ollama`
        : "Aucune carte graphique connue" }),
      S.info.ram ? h("span", { text: `${memory(S.info.ram)} de mémoire` }) : null,
      h("span", { text: `contexte ${tokens(currentCtx())}` }),
      h("span", { class: "om-legend" }, ...["gpu", "cpu", "big"].map((k) =>
        h("span", { class: `om-size fit-${k}`, text: FIT_LABEL[k].replace(" ici", ""), dataset: { tip: fitTip(k) } }))));
  }

  function readFitOnly() {
    try { return localStorage.getItem(FIT_KEY) !== "0"; } catch { return true; }
  }

  function setFitOnly(value) {
    S.fitOnly = value;
    S.limit = PAGE;
    try { localStorage.setItem(FIT_KEY, value ? "1" : "0"); } catch { /* stockage indisponible */ }
    renderLibrary();
  }

  // Tailles exactes (page « tags » d'ollama.com) des modèles affichés, demandées par lots
  const sizeQueue = new Set();
  const fetchSizes = debounce(async () => {
    const names = [...sizeQueue];
    sizeQueue.clear();
    names.forEach((n) => S.sizing.add(n));
    const res = await api.ollama_sizes(names);
    for (const n of names) {
      S.sizing.delete(n);
      S.sizes.set(n, res?.[n] ?? null);      // null : inconnues, on garde l'estimation
    }
    renderLibrary();
  }, 120);

  function wantSizes(names) {
    for (const n of names) if (!S.sizes.has(n) && !S.sizing.has(n)) sizeQueue.add(n);
    if (sizeQueue.size) fetchSizes();
  }

  async function toggleLib(name) {
    if (S.open.has(name)) S.open.delete(name);
    else S.open.add(name);
    renderLibrary();
    if (S.open.has(name) && !S.variants.get(name)?.list && !S.variants.get(name)?.loading) {
      S.variants.set(name, { loading: true });
      renderLibrary();
      const res = await api.ollama_variants(name);
      S.variants.set(name, res?.ok ? { list: res.variants, all: false } : { error: res?.error || "Variantes illisibles." });
      if (res?.ok) S.sizes.set(name, res.variants.filter((x) => !tagOf(x.name).includes("-")));
      if (S.open.has(name)) renderLibrary();
    }
  }

  function variantsPanel(m) {
    const v = S.variants.get(m.name) || { loading: true };
    if (v.loading) {
      return h("div", { class: "om-variants" }, h("div", { class: "om-empty" }, ico("loader-circle", 14),
        h("span", { text: "Lecture des variantes…" })));
    }
    if (v.error) {
      return h("div", { class: "om-variants" }, h("div", { class: "om-empty error" }, h("span", { text: v.error }),
        h("button", { class: "btn ghost sm", type: "button", onClick: () => api.open_url(modelUrl(m.name)) },
          ico("external-link", 14), "Voir sur ollama.com")));
    }
    const list = v.list;
    const plain = (x) => !tagOf(x.name).includes("-");
    const latest = list.find((x) => tagOf(x.name) === "latest");
    const twin = latest && list.find((x) => x !== latest && plain(x) && x.id && x.id === latest.id);
    const main = list.filter((x) => plain(x) && !(x === latest && twin));
    const shown = v.all || !main.length ? list : main;
    return h("div", { class: "om-variants" },
      ...shown.flatMap((x) => {
        const p = S.pulls.get(x.name);
        return [variantRow(x, twin === x),
          p && !p.done ? h("div", { class: "om-variant-progress" }, pullLine(p)) : null];
      }),
      h("div", { class: "om-variants-foot" },
        list.length > main.length && main.length
          ? h("button", { class: "btn ghost sm", type: "button", onClick: () => { v.all = !v.all; renderLibrary(); } },
            ico(v.all ? "chevron-down" : "chevron-right", 14),
            v.all ? "Variantes principales" : `Toutes les variantes (${list.length}) : quantifications…`)
          : null,
        h("div", { class: "head-spacer" }),
        h("button", { class: "btn ghost sm", type: "button", onClick: () => api.open_url(modelUrl(m.name)) },
          ico("external-link", 14), "Fiche sur ollama.com")));
  }

  function variantRow(x, isDefault) {
    const local = S.models.find((m) => m.name === x.name);
    const p = S.pulls.get(x.name);
    const upToDate = local && local.update !== true;
    let action;
    if (p && !p.done) {
      action = h("button", { class: "btn ghost sm", type: "button", onClick: () => cancel(x.name) },
        ico("x", 14), "Annuler");
    } else if (local && upToDate) {
      action = h("span", { class: "om-v-state ok" }, ico("check", 13), "Installé");
    } else {
      action = h("button", { class: `btn sm${local ? "" : " primary"}`, type: "button", disabled: !S.info.online,
        dataset: { tip: S.info.online ? null : "Ollama ne répond pas" }, onClick: () => pull(x.name) },
      ico("download", 14), local ? "Mettre à jour" : "Télécharger");
    }
    const fitting = classify(x.name, x.size);
    return h("div", { class: "om-variant" },
      h("span", { class: "om-v-name" }, h("span", { class: "om-name", text: x.name }),
        isDefault ? h("span", { class: "om-tag", text: "par défaut", dataset: { tip: `Même modèle que ${baseName(x.name)}:latest` } }) : null),
      h("span", { class: "om-v-size", text: bytes(x.size) }),
      h("span", { class: "om-v-ctx", text: x.context ? `contexte ${x.context}` : "",
        dataset: { tip: x.input.length ? `Entrées : ${x.input.map((k) => ({ text: "texte", image: "images", audio: "son" }[k] || k)).join(", ")}` : null } }),
      fitting ? h("span", { class: `om-fit ${fitting.kind}`, text: FIT_LABEL[fitting.kind], dataset: { tip: fitting.tip } }) : h("span"),
      action);
  }

  const onSearch = debounce(() => {
    S.query = search.value;
    S.limit = PAGE;
    renderLibrary();
  }, 120);
  search.addEventListener("input", onSearch);
  search.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && search.value) { ev.stopPropagation(); search.value = ""; onSearch(); onSearch.flush(); }
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    onSearch.flush();
    const q = S.query.trim();
    if (NAME_RE.test(q) && (q.includes(":") || q.includes("/"))) { pull(q); return; }
    const first = q && matches(q)[0];
    if (first && !S.open.has(first.name)) toggleLib(first.name);
  });

  // ─────────────────────────────────────────────
  //  Options
  // ─────────────────────────────────────────────
  function renderOptions() {
    const s = store.settings;
    const ctxValue = Number(s.ollama_num_ctx) || S.info.default_ctx || 8192;
    ctxSeg.replaceChildren(...CTX_SIZES.map(([v, label]) => h("button", {
      type: "button", class: v === ctxValue ? "active" : "", text: label, dataset: { tip: `${v.toLocaleString("fr-FR")} tokens` },
      onClick: async () => { await ctx.saveSetting("ollama_num_ctx", v); renderOptions(); renderLibrary(); },
    })));
    const keep = s.ollama_keep_alive ?? "5m";
    keepSeg.replaceChildren(...KEEP.map(([v, label]) => h("button", {
      type: "button", class: v === keep ? "active" : "", text: label,
      onClick: async () => { await ctx.saveSetting("ollama_keep_alive", v); renderOptions(); },
    })));
    const chats = S.models.filter((m) => !(m.capabilities?.includes("embedding") && !m.capabilities.includes("completion")));
    usesGrid.replaceChildren(...USES.flatMap(([id, label, key, tip]) => {
      const current = S.uses[id] || "";
      const chosen = s[key] && S.models.some((m) => m.name === s[key]);
      const btn = h("button", { class: "model-pill", type: "button", disabled: !chats.length,
        onClick: (ev) => openMenuBelow(chats.map((m) => ({
          label: m.name, checked: m.name === current, hint: m.capabilities?.includes("vision") ? "images" : "",
          onSelect: () => use(id, m.name) })), ev.currentTarget, "right") },
      ico("sparkles", 14), h("span", { class: "name", text: current || "Aucun modèle" }), ico("chevron-down", 14));
      return [h("div", { class: "om-use-label" }, h("span", { text: label }),
        h("span", { class: "hint-text", text: chosen || !current ? tip : `${tip} · choisi automatiquement` })), btn];
    }));
  }

  // ─────────────────────────────────────────────
  //  Modèles en mémoire : relus tant que la section est affichée
  // ─────────────────────────────────────────────
  async function pollRunning() {
    const res = await api.ollama_running();
    if (!res?.ok) return;
    const before = JSON.stringify(S.running);
    S.running = res.running;
    if (S.running.length || JSON.stringify(S.running) !== before) renderModels();
  }

  function syncPoll() {
    const want = active && visible && S.info.online;
    if (want && !pollTimer) pollTimer = setInterval(pollRunning, POLL_MS);
    if (!want && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // ─────────────────────────────────────────────
  //  Événements
  // ─────────────────────────────────────────────
  on("ollama:changed", ({ starting }) => {
    S.info.starting = !!starting;
    renderHead();
    if (active) refreshSoon();
  });
  on("chat:available", (chat) => {
    S.info.online = !!chat.available;
    renderHead();
    if (active) refreshSoon();
    syncPoll();
  });
  on("chat:models", () => { if (active) refreshSoon(); });
  on("window:hidden", () => { visible = false; syncPoll(); });
  on("window:shown", () => { visible = true; syncPoll(); if (active) refreshSoon(); });

  renderHead();
  renderPulls();
  renderOptions();

  return {
    el,
    onShow() {
      active = true;
      refresh();
      if (!S.library && !S.libraryLoading) loadLibrary();
      syncPoll();
    },
    onHide() {
      active = false;
      syncPoll();
    },
  };
}

function syncSwitch(sw, value) {
  sw.classList.toggle("on", !!value);
  sw.setAttribute("aria-checked", String(!!value));
}
