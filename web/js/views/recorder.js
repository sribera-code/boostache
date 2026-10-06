// Vue Enregistreur : enregistre le son qui sort du PC (et le micro en option),
// liste, lecture et transcription en texte des enregistrements du dossier choisi.
import { h, plural, isTyping } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, openModal, toast } from "../ui.js";

const FORMATS = [["mp3", "MP3"], ["m4a", "M4A"], ["wav", "WAV"]];
const TRANSCRIBE_STATES = {
  queued: "Transcription en attente…",
  download: "Téléchargement du modèle de transcription (une seule fois)…",
  load: "Chargement du modèle…",
  run: "Transcription…",
};
const FORMAT_TIPS = {
  mp3: "MP3 : lu partout (environ 1,4 Mo par minute)",
  m4a: "M4A (AAC) : un peu plus compact que le MP3 à qualité égale",
  wav: "WAV : sans compression (environ 11 Mo par minute)",
};

/** 75 → "1:15", 3725 → "1:02:05". */
function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const mm = String(Math.floor(s / 60) % 60).padStart(s >= 3600 ? 2 : 1, "0");
  const ss = String(s % 60).padStart(2, "0");
  return s >= 3600 ? `${Math.floor(s / 3600)}:${mm}:${ss}` : `${mm}:${ss}`;
}

function size(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toLocaleString("fr-FR", { maximumFractionDigits: mb < 10 ? 1 : 0 })} Mo`;
}

function when(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return `Aujourd'hui, ${time}`;
  if (d.toDateString() === yesterday.toDateString()) return `Hier, ${time}`;
  const opts = { day: "numeric", month: "short", ...(d.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) };
  return `${d.toLocaleDateString("fr-FR", opts)}, ${time}`;
}

/** Niveau crête (0–1) → largeur de la jauge, en dB de -60 à 0. */
function meterWidth(peak) {
  if (!peak || peak <= 0) return 0;
  const db = 20 * Math.log10(peak);
  return Math.max(0, Math.min(1, (db + 60) / 60));
}

export function createRecorderView(ctx, state) {
  const { api, on } = ctx;
  const init = state.recorder || {};
  const S = {
    available: !!init.available,
    folder: init.folder || "",
    format: init.format || "mp3",
    device: init.device || "",
    mic: !!init.mic,
    session: init.session || null,
    files: init.files || [],
    outputs: [],
    micName: "",
    stt: init.transcribe || { available: false },
    starting: false,
    selected: null,         // nom du fichier sélectionné (clavier)
    renaming: null,
  };
  const durations = new Map();   // url → durée lue par le navigateur (MP3, M4A)

  // ── Lecture ─────────────────────────────────
  const audio = new Audio();
  audio.preload = "auto";
  const player = { name: null };

  // ── Panneau d'enregistrement ────────────────
  const recBtn = h("button", { class: "rec-btn", type: "button", onClick: toggleRecording });
  const pauseBtn = iconButton("pause", "Pause", togglePause, { size: 18, cls: "rec-pause" });
  const timeEl = h("div", { class: "rec-time", text: "0:00" });
  const stateEl = h("div", { class: "rec-state" });
  const meter = (label, iconName) => {
    const fill = h("div", { class: "rec-meter-fill" });
    const row = h("div", { class: "rec-meter" }, ico(iconName, 14),
      h("span", { class: "rec-meter-label", text: label }),
      h("div", { class: "rec-meter-track" }, fill));
    return { row, fill, peak: 0 };
  };
  const outMeter = meter("PC", "volume-2");
  const micMeter = meter("Micro", "mic");
  const meters = h("div", { class: "rec-meters" }, outMeter.row, micMeter.row);

  const sourceBtn = h("button", { class: "btn sm rec-source", type: "button", onClick: sourceMenu });
  const micBtn = h("button", { class: "btn sm rec-mic", type: "button", role: "switch", onClick: toggleMic });
  const formatSeg = h("div", { class: "segmented rec-format" });
  const options = h("div", { class: "rec-options" }, sourceBtn, micBtn, h("div", { class: "head-spacer" }), formatSeg);

  const deck = h("div", { class: "rec-deck" },
    h("div", { class: "rec-main" }, recBtn, h("div", { class: "rec-status" }, timeEl, stateEl), pauseBtn),
    meters, options);

  // ── Liste ───────────────────────────────────
  const count = h("span", { class: "view-sub" });
  const sttBtn = h("button", { class: "btn ghost sm", type: "button", hidden: !S.stt.available, onClick: sttMenu,
    dataset: { tip: "Réglages de la transcription en texte (Whisper, en local)" } });
  const folderBtn = h("button", { class: "rec-folder", type: "button", onClick: () => api.rec_reveal(null),
    dataset: { tip: "Ouvrir le dossier dans l'explorateur" } });
  const list = h("div", { class: "rec-list", role: "listbox", tabindex: "0", "aria-label": "Enregistrements" });
  const body = h("div", { class: "rec-body" }, deck,
    h("div", { class: "rec-list-head" }, h("span", { class: "rec-list-title", text: "Enregistrements" }),
      folderBtn,
      sttBtn,
      h("button", { class: "btn ghost sm", type: "button", onClick: pickFolder }, ico("folder-input", 14), "Changer de dossier")),
    list);
  const unavailable = h("div", { class: "empty" },
    h("div", { class: "glyph" }, ico("audio-lines", 22)),
    h("h3", { text: "Enregistreur indisponible" }),
    h("p", { text: `Le son du PC ne peut pas être capturé : ${init.reason || "erreur inconnue"}.` }));

  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Enregistreur" }), count,
      h("div", { class: "head-spacer" }),
      h("button", { class: "btn ghost sm", type: "button", onClick: () => api.rec_reveal(null) },
        ico("folder-open", 14), "Ouvrir le dossier")),
    S.available ? body : unavailable);

  list.addEventListener("keydown", onKey);
  list.addEventListener("contextmenu", (ev) => {
    if (ev.target.closest(".rec-item")) return;
    ev.preventDefault();
    openMenu([
      { label: "Ouvrir le dossier", icon: "folder-open", onSelect: () => api.rec_reveal(null) },
      { label: "Changer de dossier…", icon: "folder-input", onSelect: pickFolder },
    ], ev.clientX, ev.clientY);
  });

  // ── Enregistrement ──────────────────────────
  async function toggleRecording() {
    if (S.session) { recBtn.disabled = true; await api.rec_stop(); recBtn.disabled = false; return; }
    if (S.starting) return;
    S.starting = true;
    renderDeck();
    const res = await api.rec_start(S.device, S.mic);
    S.starting = false;
    if (res?.error) toast(res.error, "error");
    else if (res?.session) setSession(res.session);
    renderDeck();
  }

  function togglePause() {
    if (S.session) api.rec_pause(!S.session.paused);
  }

  function toggleMic() {
    if (S.session) return;
    S.mic = !S.mic;
    renderDeck();
  }

  function setSession(session) {
    const was = !!S.session;
    S.session = session;
    if (!session) {
      for (const m of [outMeter, micMeter]) {
        m.peak = 0;
        m.fill.style.transform = "";
        m.row.classList.remove("hot");
      }
      if (was) loadFiles();
    }
    ctx.setBadge("recorder", session ? "dot" : 0, "rec");
    renderDeck();
  }

  async function loadDevices() {
    const res = await api.rec_devices();
    S.outputs = res?.outputs || [];
    S.micName = res?.mic || "";
    renderDeck();
  }

  async function sourceMenu(ev) {
    if (S.session) return;
    const anchor = ev.currentTarget;
    await loadDevices();
    const fallback = S.outputs.find((d) => d.default);
    openMenuBelow([
      { section: "Son enregistré" },
      { label: fallback ? `Sortie par défaut (${fallback.name})` : "Sortie par défaut", checked: !S.device,
        onSelect: () => { S.device = ""; renderDeck(); } },
      ...(S.outputs.length ? ["-"] : []),
      ...S.outputs.map((d) => ({ label: d.name, checked: S.device === d.id,
        onSelect: () => { S.device = d.id; renderDeck(); } })),
    ], anchor);
  }

  function deviceLabel() {
    if (S.session) return S.session.device;
    const chosen = S.device && S.outputs.find((d) => d.id === S.device);
    if (chosen) return chosen.name;
    const fallback = S.outputs.find((d) => d.default);
    return fallback ? `Sortie par défaut (${fallback.name})` : "Sortie par défaut";
  }

  function renderDeck() {
    const rec = !!S.session;
    const paused = rec && S.session.paused;
    deck.classList.toggle("recording", rec && !paused);
    deck.classList.toggle("paused", paused);
    recBtn.replaceChildren(h("span", { class: rec ? "rec-glyph stop" : "rec-glyph" }));
    recBtn.disabled = S.starting;
    recBtn.dataset.tip = rec ? "Arrêter et enregistrer le fichier" : "Enregistrer le son du PC";
    recBtn.setAttribute("aria-label", recBtn.dataset.tip);
    pauseBtn.hidden = !rec;
    pauseBtn.replaceChildren(ico(paused ? "play" : "pause", 18));
    pauseBtn.dataset.tip = paused ? "Reprendre" : "Pause";
    pauseBtn.setAttribute("aria-label", pauseBtn.dataset.tip);
    timeEl.textContent = clock(rec ? S.session.seconds : 0);

    if (S.starting) stateEl.textContent = "Démarrage…";
    else if (paused) stateEl.textContent = "En pause — rien n'est enregistré";
    else if (rec) stateEl.textContent = `Enregistrement de « ${S.session.device} »${S.session.mic ? " + micro" : ""}`;
    else stateEl.textContent = "Musique, vidéos, appels : tout ce que vous entendez est enregistré, pas les autres sons de la pièce.";

    meters.hidden = !rec;
    micMeter.row.hidden = !(rec && S.session.mic);

    sourceBtn.disabled = micBtn.disabled = rec || S.starting;
    sourceBtn.replaceChildren(ico("volume-2", 14), h("span", { class: "rec-source-name", text: deviceLabel() }),
      ico("chevron-down", 14));
    sourceBtn.dataset.tip = rec ? "Source de l'enregistrement en cours" : "Sortie audio à enregistrer";
    const micOn = rec ? !!S.session.mic : S.mic;
    micBtn.classList.toggle("on", micOn);
    micBtn.setAttribute("aria-checked", String(micOn));
    micBtn.replaceChildren(ico("mic", 14), micOn ? "Micro inclus" : "Ajouter le micro");
    micBtn.dataset.tip = micOn
      ? `Votre voix est mêlée au son du PC${S.micName ? ` (${S.micName})` : ""}`
      : `Mêler votre voix au son du PC, pour un appel par exemple${S.micName ? ` (${S.micName})` : ""}`;

    formatSeg.replaceChildren(...FORMATS.map(([id, label]) => h("button", {
      type: "button", class: S.format === id ? "active" : "", text: label, dataset: { tip: FORMAT_TIPS[id] },
      onClick: async () => { await ctx.saveSetting("audio_format", id); S.format = ctx.store.settings.audio_format || id; renderDeck(); },
    })));
  }

  function onLevel({ seconds, out, mic }) {
    if (!S.session) return;
    S.session.seconds = seconds;
    timeEl.textContent = clock(seconds);
    // Retombée progressive : la jauge reste lisible entre deux crêtes (à zéro en pause)
    const decay = S.session.paused ? 0 : 0.8;
    outMeter.peak = Math.max(out || 0, outMeter.peak * decay);
    micMeter.peak = Math.max(mic || 0, micMeter.peak * decay);
    for (const m of [outMeter, micMeter]) {
      m.fill.style.transform = `scaleX(${1 - meterWidth(m.peak)})`;   // cache le haut de la jauge
      m.row.classList.toggle("hot", m.peak >= 0.99);                  // saturation
    }
  }

  // ── Fichiers ────────────────────────────────
  async function loadFiles() {
    setFiles(await api.rec_files());
  }

  function setFiles(files) {
    S.files = files || [];
    if (player.name && !S.files.some((f) => f.name === player.name)) stopPlayback();
    if (S.selected && !S.files.some((f) => f.name === S.selected)) S.selected = null;
    renderList();
  }

  async function pickFolder() {
    const folder = await api.rec_pick_folder();
    if (!folder) return;
    stopPlayback();
    S.folder = folder;
    renderList();
  }

  const fileOf = (name) => S.files.find((f) => f.name === name);

  function durationOf(f) {
    return f.duration ?? durations.get(f.url) ?? null;
  }

  /** Durée des MP3/M4A : lue par le navigateur, une à la fois. */
  const probeQueue = [];
  let probing = false;
  function probeDuration(f) {
    if (!f.url || f.duration != null || durations.has(f.url) || probeQueue.includes(f.url)) return;
    probeQueue.push(f.url);
    if (!probing) nextProbe();
  }
  function nextProbe() {
    const url = probeQueue.shift();
    if (!url) { probing = false; return; }
    probing = true;
    const probe = new Audio();
    probe.preload = "metadata";
    const done = () => {
      if (Number.isFinite(probe.duration)) durations.set(url, probe.duration);
      probe.removeAttribute("src");
      probe.load();
      const f = S.files.find((x) => x.url === url);
      if (f) updateMeta(f);
      nextProbe();
    };
    probe.addEventListener("loadedmetadata", done, { once: true });
    probe.addEventListener("error", done, { once: true });
    probe.src = url;
  }

  function metaText(f) {
    const parts = [when(f.mtime)];
    const d = durationOf(f);
    if (d != null) parts.push(clock(d));
    parts.push(size(f.size), f.format);
    return parts.join(" · ");
  }

  /** Ligne d'information : date, durée…, ou avancement de la conversion / transcription. */
  function metaContent(f) {
    if (f.converting) return [h("span", { class: "rec-meta-text", text: `Conversion en ${S.format.toUpperCase()}…` })];
    const job = f.transcribing;
    if (job) {
      const label = TRANSCRIBE_STATES[job.state] || TRANSCRIBE_STATES.run;
      const pct = job.state === "run" ? ` ${Math.round(job.progress * 100)} %` : "";
      return [h("span", { class: "rec-stt-state" }, ico("loader-circle", 12), `${label}${pct}`),
        job.state === "run" ? h("span", { class: "rec-stt-bar" },
          h("span", { style: { transform: `scaleX(${job.progress})` } })) : null];
    }
    return [h("span", { class: "rec-meta-text", text: metaText(f) }),
      f.transcript ? h("span", { class: "rec-tag", text: "Texte" }) : null];
  }

  function updateMeta(f) {
    const meta = list.querySelector(`.rec-item[data-name="${CSS.escape(f.name)}"] .rec-meta`);
    if (meta) meta.replaceChildren(...metaContent(f).filter(Boolean));
  }

  function row(f) {
    const playing = player.name === f.name;
    const stt = S.stt.available;
    const playBtn = h("button", { class: `rec-play${playing && !audio.paused ? " on" : ""}`, type: "button",
      disabled: f.converting || !f.url,
      dataset: { tip: !f.url ? "Lecture indisponible" : playing && !audio.paused ? "Pause" : "Écouter" },
      onClick: (ev) => { ev.stopPropagation(); togglePlay(f.name); } },
      f.converting ? ico("loader-circle", 16) : ico(playing && !audio.paused ? "pause" : "play", 15));
    const title = S.renaming === f.name ? renameInput(f) : h("div", { class: "rec-title", text: f.title, dataset: { tip: f.name } });
    const meta = h("div", { class: "rec-meta" }, ...metaContent(f).filter(Boolean));
    let actions = null;
    if (f.transcribing) {
      actions = h("div", { class: "rec-actions shown" },
        iconButton("x", "Annuler la transcription", () => api.rec_transcribe_cancel(f.name), { size: 15, cls: "sm" }));
    } else if (!f.converting) {
      actions = h("div", { class: "rec-actions" },
        stt ? iconButton("file-text", f.transcript ? "Voir le texte" : "Transcrire en texte",
          () => (f.transcript ? showTranscript(f.name) : transcribe(f.name)), { size: 15, cls: `sm${f.transcript ? " on" : ""}`, kbd: "T" }) : null,
        iconButton("copy", "Copier le fichier (à coller dans un dossier, un e-mail…)", () => copyFile(f.name), { size: 15, cls: "sm" }),
        iconButton("folder-open", "Afficher dans l'explorateur", () => api.rec_reveal(f.name), { size: 15, cls: "sm" }),
        iconButton("pencil", "Renommer", () => startRename(f.name), { size: 15, cls: "sm", kbd: "F2" }),
        iconButton("trash-2", "Supprimer (corbeille)", () => remove(f.name), { size: 15, cls: "sm", kbd: "Suppr" }));
    }
    const busy = f.converting || f.transcribing;
    const item = h("div", { class: `rec-item${f.name === S.selected ? " selected" : ""}${playing ? " playing" : ""}${f.converting ? " busy" : ""}`,
      role: "option", dataset: { name: f.name } },
      playBtn,
      h("div", { class: "rec-text" }, title, meta, playing ? seekBar() : null),
      actions);
    item.addEventListener("click", () => setSelected(f.name));
    item.addEventListener("dblclick", (ev) => { if (!ev.target.closest("button, input")) togglePlay(f.name); });
    item.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      setSelected(f.name);
      if (f.converting) return;
      const isPlaying = player.name === f.name && !audio.paused;
      const sttItems = !stt ? [] : f.transcribing
        ? [{ label: "Annuler la transcription", icon: "x", onSelect: () => api.rec_transcribe_cancel(f.name) }]
        : f.transcript
          ? [{ label: "Voir le texte", icon: "file-text", hint: "T", onSelect: () => showTranscript(f.name) },
            ctx.destinationMenuItem("notes", { label: "Texte dans une note", newLabel: "Texte dans une nouvelle note",
              icon: "notebook-pen" }, (slot) => sendTranscript(f.name, "notes", slot)),
            ctx.store.ollama ? ctx.destinationMenuItem("chat", { label: "Texte dans une conversation",
              newLabel: "Texte dans une nouvelle conversation", icon: "message-square" },
              (tab) => sendTranscript(f.name, "chat", tab)) : null,
            { label: "Transcrire à nouveau", icon: "refresh-cw", onSelect: () => transcribe(f.name) }]
          : [{ label: "Transcrire en texte", icon: "file-text", hint: "T", onSelect: () => transcribe(f.name) }];
      if (busy) { openMenu(sttItems, ev.clientX, ev.clientY); return; }
      openMenu([
        { label: isPlaying ? "Pause" : "Écouter", icon: isPlaying ? "pause" : "play", hint: "Espace",
          disabled: !f.url, onSelect: () => togglePlay(f.name) },
        ...sttItems,
        { label: "Copier le fichier", icon: "copy", hint: "Ctrl+C", onSelect: () => copyFile(f.name) },
        { label: "Afficher dans l'explorateur", icon: "folder-open", onSelect: () => api.rec_reveal(f.name) },
        { label: "Renommer…", icon: "pencil", hint: "F2", onSelect: () => startRename(f.name) },
        "-",
        { label: "Supprimer", icon: "trash-2", hint: "Suppr", danger: true, onSelect: () => remove(f.name) },
      ], ev.clientX, ev.clientY);
    });
    probeDuration(f);
    return item;
  }

  function renderList() {
    count.textContent = S.files.length ? plural(S.files.length, "enregistrement", "enregistrements") : "";
    folderBtn.replaceChildren(ico("folder", 13), h("span", { text: S.folder }));
    if (!S.files.length) {
      list.replaceChildren(h("div", { class: "empty rec-empty" },
        h("div", { class: "glyph" }, ico("audio-lines", 22)),
        h("h3", { text: "Aucun enregistrement" }),
        h("p", { text: "Lancez un enregistrement avec le bouton rouge : le fichier apparaîtra ici à l'arrêt." })));
      return;
    }
    const focused = list.contains(document.activeElement) && document.activeElement.tagName === "INPUT";
    if (focused) return;   // renommage en cours : la liste sera redessinée après
    list.replaceChildren(...S.files.map(row));
  }

  function setSelected(name) {
    S.selected = name;
    for (const n of list.querySelectorAll(".rec-item")) n.classList.toggle("selected", n.dataset.name === name);
    list.querySelector(".rec-item.selected")?.scrollIntoView({ block: "nearest" });
  }

  function onKey(ev) {
    if (isTyping(ev.target)) return;
    const idx = S.files.findIndex((f) => f.name === S.selected);
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      const next = S.files[Math.min(S.files.length - 1, Math.max(0, idx + (ev.key === "ArrowDown" ? 1 : -1)))];
      if (next) setSelected(next.name);
      return;
    }
    if (!S.selected) return;
    if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); togglePlay(S.selected); }
    else if (ev.key === "F2") { ev.preventDefault(); startRename(S.selected); }
    else if (ev.key.toLowerCase() === "t" && !ev.ctrlKey && !ev.altKey && S.stt.available) {
      ev.preventDefault();
      const f = fileOf(S.selected);
      if (f?.transcript) showTranscript(f.name);
      else if (f) transcribe(f.name);
    }
    else if (ev.key === "Delete") { ev.preventDefault(); remove(S.selected); }
    else if (ev.key.toLowerCase() === "c" && ev.ctrlKey) { ev.preventDefault(); copyFile(S.selected); }
  }

  async function copyFile(name) {
    if (await api.rec_copy_file(name)) toast("Fichier copié : collez-le dans un dossier, un e-mail ou une discussion");
    else toast("Copie impossible.", "error");
  }

  async function remove(name) {
    const f = fileOf(name);
    if (!f || f.converting) return;
    if (f.transcribing) { toast("Transcription en cours : annulez-la d'abord.", "info"); return; }
    if (player.name === name) stopPlayback();
    if (await api.rec_delete(name)) toast(`« ${f.title} » envoyé à la corbeille`, "info");
    else toast("Suppression impossible : le fichier est peut-être ouvert ailleurs.", "error");
  }

  function startRename(name) {
    const f = fileOf(name);
    if (!f || f.converting) return;
    if (f.transcribing) { toast("Transcription en cours : annulez-la d'abord.", "info"); return; }
    S.renaming = name;
    renderList();
    const input = list.querySelector(".rec-rename");
    input?.focus();
    input?.select();
  }

  function renameInput(f) {
    const input = h("input", { class: "rec-rename", type: "text", value: f.title, spellcheck: "false",
      "aria-label": "Nouveau nom" });
    let done = false;
    const finish = async (commit) => {
      if (done) return;
      done = true;
      S.renaming = null;
      const title = input.value.trim();
      if (commit && title && title !== f.title) {
        // Le fichier en lecture est libéré, sinon Windows refuse le renommage
        if (player.name === f.name) stopPlayback();
        const res = await api.rec_rename(f.name, title);
        if (res?.error) toast(res.error, "error");
        else if (res?.name) S.selected = res.name;
      }
      input.blur();
      await loadFiles();
      list.focus();
    };
    input.addEventListener("keydown", (ev) => {
      ev.stopPropagation();
      if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
      else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("click", (ev) => ev.stopPropagation());
    return input;
  }

  // ── Transcription ───────────────────────────
  async function transcribe(name) {
    const res = await api.rec_transcribe(name);
    if (res?.error) toast(res.error, "error");
  }

  function sttLabel() {
    const model = (S.stt.models || []).find((m) => m.id === S.stt.model);
    return `Texte : ${model ? model.label : "?"}`;
  }

  function renderSttBtn() {
    sttBtn.replaceChildren(ico("file-text", 14), sttLabel(), ico("chevron-down", 13));
  }

  async function saveStt(key, field, value) {
    const saved = await ctx.saveSetting(key, value);
    if (saved !== null && saved !== undefined) S.stt[field] = saved;
    renderSttBtn();
  }

  function sttMenu(ev) {
    const st = S.stt;
    openMenuBelow([
      { section: "Modèle de transcription" },
      ...(st.models || []).map((m) => ({
        label: `${m.label} (${m.size})`, checked: st.model === m.id,
        hint: { base: "le plus vite", small: "conseillé", "large-v3-turbo": "plus lent" }[m.id] || "",
        onSelect: () => saveStt("transcribe_model", "model", m.id),
      })),
      { section: "Langue parlée" },
      { label: (st.languages || []).find((l) => l.id === st.language)?.label || "Détection automatique",
        icon: "message-square", submenu: (st.languages || []).map((l) => ({
          label: l.label, checked: st.language === l.id,
          onSelect: () => saveStt("transcribe_language", "language", l.id),
        })) },
      "-",
      { label: "Transcrire chaque nouvel enregistrement", checked: !!st.auto,
        onSelect: () => saveStt("audio_transcribe", "auto", !st.auto) },
    ], ev.currentTarget, "right");
  }

  const toChat = (path, tab) => ctx.emit("chat:attach", { paths: [path], tab });
  const toNote = (text, slot) => ctx.emit("note:insert", { text, slot });

  /** Texte de la transcription, envoyé à une note ou joint à une conversation. */
  async function sendTranscript(name, kind, id) {
    const res = await api.rec_transcript(name);
    if (!res) { toast("Pas encore de texte pour cet enregistrement.", "info"); return; }
    if (kind === "notes") toNote(res.text, id);
    else toChat(res.path, id);
  }

  async function showTranscript(name) {
    const f = fileOf(name);
    const res = await api.rec_transcript(name);
    if (!f || !res) { toast("Pas encore de texte pour cet enregistrement.", "info"); return; }
    const viewer = h("pre", { class: "viewer rec-transcript", tabindex: "0", text: res.text });
    const source = `transcript:${name}`;
    viewer.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      const sel = window.getSelection().toString();
      openMenu([
        { label: "Copier la sélection", icon: "copy", disabled: !sel, onSelect: () => ctx.copy(sel) },
        { label: "Copier tout", icon: "copy", onSelect: () => ctx.copy(res.text) },
        "-",
        { label: "Lire la sélection", icon: "volume-2", disabled: !sel, onSelect: () => ctx.speak(sel, source) },
        { label: "Tout lire", icon: "volume-2", onSelect: () => ctx.speak(res.text, source) },
      ], ev.clientX, ev.clientY);
    });
    const words = (res.text.match(/\S+/g) || []).length;
    const modal = openModal({
      title: f.title,
      subtitle: `Transcription · ${plural(words, "mot", "mots")}`,
      body: viewer,
      width: 720,
      onClose: () => { if (ctx.speaking(source)) api.tts_stop(); },
      actions: [
        { label: "Ouvrir dans le Bloc-notes", icon: "external-link", kind: "ghost", left: true, iconOnly: true,
          onClick: () => { api.rec_open_transcript(name); return false; } },
        { label: "Transcrire à nouveau", icon: "refresh-cw", kind: "ghost", left: true, iconOnly: true,
          onClick: () => transcribe(name) },
        { label: "Dans une note", icon: "notebook-pen", kind: "ghost",
          onClick: (btn) => {
            ctx.pickDestination("notes", btn, (slot) => { modal.close(); toNote(res.text, slot); });
            return false;
          } },
        ...(ctx.store.ollama ? [{ label: "Dans une conversation", icon: "message-square", kind: "ghost",
          onClick: (btn) => {
            ctx.pickDestination("chat", btn, (tab) => { modal.close(); toChat(res.path, tab); });
            return false;
          } }] : []),
        { label: "Copier", icon: "copy", kind: "primary",
          onClick: async () => { await ctx.copy(res.text, "Texte copié"); } },
      ],
    });
  }

  // ── Lecteur ─────────────────────────────────
  const seek = h("input", { class: "rec-seek", type: "range", min: "0", max: "1000", value: "0",
    "aria-label": "Position de lecture" });
  const seekTime = h("span", { class: "rec-seek-time" });
  let seeking = false;
  seek.addEventListener("input", () => {
    seeking = true;
    if (Number.isFinite(audio.duration)) seekTime.textContent = `${clock(seek.value / 1000 * audio.duration)} / ${clock(audio.duration)}`;
  });
  seek.addEventListener("change", () => {
    if (Number.isFinite(audio.duration)) audio.currentTime = seek.value / 1000 * audio.duration;
    seeking = false;
  });
  for (const type of ["click", "dblclick", "keydown"]) seek.addEventListener(type, (ev) => ev.stopPropagation());

  function seekBar() {
    updateSeek();
    return h("div", { class: "rec-seekbar" }, seek, seekTime);
  }

  function updateSeek() {
    const d = audio.duration;
    if (!seeking) seek.value = Number.isFinite(d) && d > 0 ? String(Math.round(audio.currentTime / d * 1000)) : "0";
    seek.style.setProperty("--pos", `${seek.value / 10}%`);
    seekTime.textContent = Number.isFinite(d) ? `${clock(audio.currentTime)} / ${clock(d)}` : clock(audio.currentTime);
  }

  function togglePlay(name) {
    const f = fileOf(name);
    if (!f || !f.url || f.converting) return;
    setSelected(name);
    if (player.name === name) {
      if (audio.paused) audio.play().catch(playError);
      else audio.pause();
      return;
    }
    player.name = name;
    audio.src = f.url;
    audio.play().catch(playError);
    renderList();
  }

  function playError(err) {
    if (err?.name === "AbortError") return;
    toast("Lecture impossible.", "error");
    stopPlayback();
  }

  function stopPlayback() {
    if (!player.name) return;
    player.name = null;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    renderList();
  }

  const refreshPlayState = () => {
    const item = player.name && list.querySelector(`.rec-item[data-name="${CSS.escape(player.name)}"]`);
    if (!item) return;
    const btn = item.querySelector(".rec-play");
    btn.classList.toggle("on", !audio.paused);
    btn.replaceChildren(ico(audio.paused ? "play" : "pause", 15));
    btn.dataset.tip = audio.paused ? "Écouter" : "Pause";
  };
  audio.addEventListener("play", refreshPlayState);
  audio.addEventListener("pause", refreshPlayState);
  audio.addEventListener("timeupdate", updateSeek);
  audio.addEventListener("loadedmetadata", updateSeek);
  audio.addEventListener("ended", () => { audio.currentTime = 0; updateSeek(); });
  audio.addEventListener("error", () => { if (player.name) playError(); });

  // ── Événements Python ───────────────────────
  on("rec:state", ({ session }) => setSession(session));
  on("rec:level", onLevel);
  on("rec:files", ({ files }) => setFiles(files));
  on("rec:transcribe", ({ name, state, progress }) => {
    const f = fileOf(name);
    if (!f) return;
    const started = !f.transcribing;
    f.transcribing = { state, progress };
    if (started) renderList();     // actions de la ligne : bouton d'annulation
    else updateMeta(f);
  });
  on("rec:transcribed", ({ name, title }) => {
    toast(`Texte prêt : « ${title} »`);
    if (ctx.isActive("recorder") && S.selected === name) showTranscript(name);
  });

  renderDeck();
  renderSttBtn();
  renderList();
  // (la barre latérale est construite juste après les vues)
  if (S.session) setTimeout(() => ctx.setBadge("recorder", "dot", "rec"));

  return {
    el,
    onShow() {
      if (!S.available) return;
      loadDevices();
      loadFiles();
    },
    focus() { if (S.available && !(isTyping() && el.contains(document.activeElement))) list.focus(); },
  };
}
