// Vue Smartphone : écran d'un téléphone Android branché en débogage USB, piloté à la souris
// et au clavier (phone.py, serveur scrcpy). La section n'existe que tant qu'un téléphone est
// branché (main.js). La vidéo H.264 arrive par des messages WebView2 (pas par bridge.js) et
// est décodée ici (WebCodecs) ; toucher, touches et presse-papiers repartent en lots, dans
// l'ordre (un seul appel à Python à la fois).
import { h } from "../dom.js";
import { ico, iconButton, toast, openMenuBelow } from "../ui.js";

const STOP_DELAY = 15000;            // section ou fenêtre masquée : connexion gardée ce temps (ms)
const MAX_QUEUE = 8;                 // images en attente du décodeur avant d'en sauter
const FRAME_US = 16667;              // horodatage des images (le décodeur n'en a besoin que croissant)

// Touches non imprimables → KeyEvent.KEYCODE_* d'Android (le texte passe par l'événement input)
const KEYCODES = {
  Enter: 66, Backspace: 67, Delete: 112, Tab: 61, Escape: 111, Insert: 124,
  ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22,
  Home: 122, End: 123, PageUp: 92, PageDown: 93,
};
const KEYCODE_A = 29;

/** Modificateurs → KeyEvent.META_* (touche de gauche, comme scrcpy). */
function metaState(ev) {
  return (ev.shiftKey ? 0x41 : 0) | (ev.ctrlKey ? 0x3000 : 0) | (ev.altKey ? 0x12 : 0) | (ev.metaKey ? 0x30000 : 0);
}

const fromBase64 = typeof Uint8Array.fromBase64 === "function"
  ? (s) => Uint8Array.fromBase64(s)
  : (s) => {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  };

/** Chaîne de codec WebCodecs (« avc1.64001f ») d'après le SPS en tête d'une image clé. */
function avcCodec(b) {
  const end = Math.min(b.length - 6, 512);
  for (let i = 0; i < end; i++) {
    if (b[i] === 0 && b[i + 1] === 0 && b[i + 2] === 1) {
      if ((b[i + 3] & 0x1f) === 7) {
        return "avc1." + [b[i + 4], b[i + 5], b[i + 6]].map((x) => x.toString(16).padStart(2, "0")).join("");
      }
      i += 2;
    }
  }
  return "";
}

export function createPhoneView(ctx, state) {
  const { api, on, store } = ctx;
  const boot = state.phone || {};
  let devices = boot.devices || [];
  let serial = boot.session?.serial || devices.find((d) => d.state === "device")?.serial || devices[0]?.serial || null;
  let session = boot.session || null;      // { sid, serial, state, name, size, error }
  let size = session?.size || null;        // [largeur, hauteur] de la vidéo : coordonnées du toucher
  let windowVisible = state.visible !== false;
  let stopTimer = 0;
  let starting = false;
  let failedSerial = null;                 // échec : pas de nouvel essai tout seul (bouton Réessayer)

  // Décodeur
  let decoder = null;
  let codec = "";
  let waitingKey = true;
  let timestamp = 0;
  let framed = false;                      // une image affichée depuis le début de la session
  let decodeErrors = [];
  let decodeFailed = "";

  // ── En-tête ──
  const deviceBtn = h("button", { class: "btn ghost sm ph-device", type: "button", onClick: (ev) => pickDevice(ev.currentTarget) });
  const sub = h("span", { class: "view-sub ph-sub" });
  // Options (réglages, gardés et appliqués à chaque connexion) : une icône par état
  const OPTIONS = {
    phone_screen_off: {
      on: ["monitor-off", "Écran du téléphone éteint : l'image reste affichée ici — cliquer pour le rallumer"],
      off: ["monitor", "Écran du téléphone allumé — cliquer pour l'éteindre (l'image reste affichée ici)"],
    },
    phone_keep_awake: {
      on: ["coffee", "Téléphone gardé éveillé : ni veille ni verrouillage tant que la section est affichée — cliquer pour revenir à la normale"],
      off: ["moon", "Le téléphone se met en veille et se verrouille normalement — cliquer pour le garder éveillé"],
    },
  };
  const optionButtons = Object.keys(OPTIONS).map((key) => {
    const btn = h("button", { class: "icon-btn sm", type: "button", onClick: () => setOption(key) });
    btn.dataset.option = key;
    return btn;
  });
  const controls = [
    iconButton("copy", "Copier la sélection du téléphone dans Windows", () => copyFromPhone(false), { size: 15, cls: "sm", kbd: "Ctrl+C" }),
    iconButton("clipboard-paste", "Coller le presse-papiers de Windows sur le téléphone", () => send(["v"]), { size: 15, cls: "sm", kbd: "Ctrl+V" }),
    h("span", { class: "ph-sep" }),
    iconButton("bell", "Notifications", () => send(["n", "notifications"]), { size: 15, cls: "sm" }),
    iconButton("rotate-ccw", "Pivoter l'écran", () => send(["o"]), { size: 15, cls: "sm" }),
    iconButton("camera", "Capture d'écran du téléphone (ouverte dans Captures)", (ev) => screenshot(ev.currentTarget),
      { size: 15, cls: "sm" }),
    h("span", { class: "ph-sep" }),
    iconButton("volume-1", "Baisser le volume", () => send(["key", "volume_down"]), { size: 15, cls: "sm" }),
    iconButton("volume-2", "Monter le volume", () => send(["key", "volume_up"]), { size: 15, cls: "sm" }),
    iconButton("power", "Bouton marche/arrêt (verrouiller ou allumer l'écran)", () => send(["key", "power"]), { size: 15, cls: "sm" }),
  ];
  const toolbar = h("div", { class: "ph-tools" }, ...controls, h("span", { class: "ph-sep" }), ...optionButtons);
  const head = h("header", { class: "view-head" },
    h("span", { class: "view-title", text: "Smartphone" }), deviceBtn, sub,
    h("span", { class: "head-spacer" }), toolbar);

  // ── Écran ──
  const canvas = h("canvas", { class: "ph-canvas", width: 1, height: 1 });
  const g = canvas.getContext("2d", { alpha: false, desynchronized: true });
  // Reçoit le clavier : texte (y compris touches mortes, IME, dictée Win+H) et touches spéciales
  const input = h("textarea", { class: "ph-input", "aria-label": "Clavier du téléphone", autocomplete: "off",
    autocapitalize: "off", spellcheck: "false", tabindex: "-1" });
  const screen = h("div", { class: "ph-screen", hidden: true }, canvas, input);
  const message = h("div", { class: "empty ph-message" });
  const stage = h("div", { class: "ph-stage" }, screen, message);
  const nav = h("div", { class: "ph-nav", hidden: true },
    iconButton("arrow-left", "Retour — clic droit sur l'écran", () => send(["key", "back"]), { size: 16 }),
    iconButton("circle", "Accueil — clic du milieu sur l'écran", () => send(["key", "home"]), { size: 15 }),
    iconButton("square", "Applications récentes", () => send(["key", "recents"]), { size: 14 }));
  const el = h("section", { class: "ph", tabindex: "-1" }, head, h("div", { class: "ph-body" }, stage, nav));

  // ─────────────────────────────────────────
  //  Téléphone affiché, connexion
  // ─────────────────────────────────────────
  const current = () => devices.find((d) => d.serial === serial) || null;
  const running = () => !!session && session.serial === serial && session.state === "running";
  const wanted = () => ctx.isActive("phone") && windowVisible && current()?.state === "device"
    && failedSerial !== serial;

  /** Démarre la connexion quand la section est affichée, l'arrête un peu après qu'elle ne l'est plus. */
  function sync() {
    if (wanted()) {
      clearTimeout(stopTimer);
      stopTimer = 0;
      const live = session && session.serial === serial && session.state !== "stopped";
      if (!live && !starting) start();
    } else if (session && session.state !== "stopped" && !stopTimer) {
      stopTimer = setTimeout(() => {
        stopTimer = 0;
        if (!wanted() && session) api.phone_stop(session.sid);
      }, STOP_DELAY);
    }
    render();
  }

  async function start() {
    if (!serial) return;
    starting = true;
    const target = serial;
    const res = await api.phone_start(target);
    starting = false;
    if (target !== serial) { sync(); return; }
    if (!res || res.error) {
      failedSerial = target;
      session = { sid: session?.sid ?? 0, serial: target, state: "stopped", error: res?.error || "Connexion impossible." };
    } else {
      adopt(res);
    }
    render();
  }

  function adopt(info) {
    if (!session || info.sid !== session.sid) {
      resetDecoder();
      decodeFailed = "";
      decodeErrors = [];
      queue.length = 0;
    }
    session = info;
    if (info.size) size = info.size;
    if (info.state === "stopped" && info.error) failedSerial = info.serial;
  }

  function retry() {
    failedSerial = null;
    decodeFailed = "";
    decodeErrors = [];
    sync();
  }

  function pickDevice(anchor) {
    if (devices.length < 2) return;
    openMenuBelow([{ section: "Téléphone affiché" }, ...devices.map((d) => ({
      label: d.name, icon: "smartphone", hint: d.state === "device" ? (d.android ? `Android ${d.android}` : "") : stateLabel(d.state),
      checked: d.serial === serial, onSelect: () => choose(d.serial),
    }))], anchor);
  }

  function choose(id) {
    if (id === serial) return;
    if (session && session.state !== "stopped") api.phone_stop(session.sid);
    serial = id;
    failedSerial = null;
    resetDecoder();
    sync();
  }

  function stateLabel(st) {
    return { unauthorized: "à autoriser", authorizing: "autorisation…", offline: "hors ligne",
      connecting: "connexion…", recovery: "mode récupération", sideload: "mode sideload", bootloader: "bootloader" }[st] || st;
  }

  // ─────────────────────────────────────────
  //  Vidéo (WebCodecs)
  // ─────────────────────────────────────────
  function resetDecoder() {
    try { decoder?.close(); } catch { /* déjà fermé */ }
    decoder = null;
    codec = "";
    waitingKey = true;
    framed = false;
  }

  function configure(c) {
    try { decoder?.close(); } catch { /* déjà fermé */ }
    decoder = new VideoDecoder({ output: draw, error: onDecodeError });
    decoder.configure({ codec: c, optimizeForLatency: true });
    codec = c;
  }

  function onDecodeError(err) {
    window.__journal?.("warning", `Smartphone : décodage de la vidéo (${err?.message || err})`);
    decoder = null;
    codec = "";
    waitingKey = true;
    const now = Date.now();
    decodeErrors = [...decodeErrors.filter((t) => now - t < 10000), now];
    if (decodeErrors.length > 4) {
      decodeFailed = `La vidéo du téléphone ne peut pas être décodée (${err?.message || err}).`;
      render();
      return;
    }
    send(["r"]);        // nouvelle image clé
  }

  function feed(key, bytes) {
    if (decodeFailed) return;
    if (typeof VideoDecoder === "undefined") {
      decodeFailed = "Cette version de WebView2 ne sait pas décoder la vidéo (WebCodecs absent).";
      render();
      return;
    }
    if (key) {
      const c = avcCodec(bytes);
      try {
        if (c && (c !== codec || !decoder || decoder.state !== "configured")) configure(c);
      } catch (err) {
        decodeFailed = `Format vidéo non pris en charge (${c}) : ${err?.message || err}`;
        render();
        return;
      }
      waitingKey = false;
    }
    if (waitingKey || !decoder || decoder.state !== "configured") return;
    if (!key && decoder.decodeQueueSize > MAX_QUEUE) {
      // Décodeur en retard : on saute jusqu'à la prochaine image clé, demandée tout de suite
      waitingKey = true;
      send(["r"]);
      return;
    }
    timestamp += FRAME_US;
    try {
      decoder.decode(new EncodedVideoChunk({ type: key ? "key" : "delta", timestamp, data: bytes }));
    } catch (err) {
      onDecodeError(err);
    }
  }

  function draw(frame) {
    const w = frame.displayWidth, hgt = frame.displayHeight;
    if (canvas.width !== w || canvas.height !== hgt) {
      canvas.width = w;
      canvas.height = hgt;
      if (!size) size = [w, hgt];
      fit();
    }
    g.drawImage(frame, 0, 0, w, hgt);
    frame.close();
    if (!framed) {
      framed = true;
      render();
    }
  }

  window.chrome?.webview?.addEventListener("message", (ev) => {
    const m = ev.data;
    if (!m || typeof m !== "object" || m.phone === undefined || !session || m.phone !== session.sid) return;
    if (m.s) {
      size = m.s;
      fit();
    } else if (typeof m.d === "string") {
      feed(m.k === 1, fromBase64(m.d));
    }
  });

  // ─────────────────────────────────────────
  //  Commandes : un lot à la fois, dans l'ordre
  // ─────────────────────────────────────────
  const queue = [];
  let sending = false;

  function send(ev) {
    if (!running()) return;
    // Déplacements du même doigt : seul le dernier compte s'il n'est pas encore parti
    const last = queue[queue.length - 1];
    if (ev[0] === "t" && ev[1] === 2 && last && last[0] === "t" && last[1] === 2 && last[6] === ev[6]) {
      queue[queue.length - 1] = ev;
    } else {
      queue.push(ev);
    }
    if (!sending) flush();
  }

  async function flush() {
    sending = true;
    while (queue.length && running()) {
      const batch = queue.splice(0);
      const res = await api.phone_input(session.sid, batch);
      if (res?.empty_clipboard) toast("Rien à coller : le presse-papiers de Windows ne contient pas de texte.", "info");
    }
    queue.length = 0;
    sending = false;
  }

  /** Capture en pleine résolution prise par le téléphone ; noire ou impossible : l'image affichée. */
  async function screenshot(btn) {
    if (!running() || btn.classList.contains("busy")) return;
    const sid = session.sid;
    btn.classList.add("busy");
    try {
      let res = await api.phone_screenshot(sid);
      if (res?.frame) {
        res = framed ? await api.phone_screenshot_frame(sid, canvas.toDataURL("image/png"))
          : { error: "Aucune image du téléphone à capturer." };
      }
      if (res?.error) toast(res.error, "error");
    } finally {
      btn.classList.remove("busy");
    }
  }

  function copyFromPhone(cut) {
    send(["c", cut ? 2 : 1]);
  }

  /** Bascule une option : enregistrée, puis appliquée par Python au téléphone affiché. */
  async function setOption(key) {
    const on = !store.settings[key];
    await ctx.saveSetting(key, on);
    render();
    if (key === "phone_keep_awake") {
      toast(on ? "Le téléphone reste éveillé, sans se verrouiller, tant que la section est affichée."
        : "Le téléphone se met de nouveau en veille et se verrouille comme d'habitude.", "info", 4500);
    }
  }

  // ── Souris et écran tactile ──
  const pointers = new Map();      // pointerId de la page → doigt envoyé au téléphone

  function point(ev) {
    const r = canvas.getBoundingClientRect();
    const [w, hgt] = size;
    const x = Math.round(((ev.clientX - r.left) / r.width) * w);
    const y = Math.round(((ev.clientY - r.top) / r.height) * hgt);
    return [Math.max(0, Math.min(w - 1, x)), Math.max(0, Math.min(hgt - 1, y)), w, hgt];
  }

  function touch(action, ev, id) {
    if (!size) return;
    send(["t", action, ...point(ev), id]);
  }

  screen.addEventListener("pointerdown", (ev) => {
    focusInput();
    if (!running() || !size) return;
    ev.preventDefault();
    if (ev.pointerType === "mouse" && ev.button === 2) { send(["b"]); return; }       // retour (ou allumer l'écran)
    if (ev.pointerType === "mouse" && ev.button === 1) { send(["key", "home"]); return; }
    if (ev.button !== 0) return;
    try { screen.setPointerCapture(ev.pointerId); } catch { /* pointeur déjà relâché */ }
    const id = ev.pointerType === "mouse" ? -1 : ev.pointerId;
    pointers.set(ev.pointerId, id);
    touch(0, ev, id);
  });
  screen.addEventListener("pointermove", (ev) => {
    const id = pointers.get(ev.pointerId);
    if (id !== undefined) touch(2, ev, id);
  });
  const release = (ev) => {
    const id = pointers.get(ev.pointerId);
    if (id === undefined) return;
    pointers.delete(ev.pointerId);
    touch(1, ev, id);
  };
  screen.addEventListener("pointerup", release);
  screen.addEventListener("pointercancel", release);
  screen.addEventListener("lostpointercapture", release);
  screen.addEventListener("contextmenu", (ev) => ev.preventDefault());
  screen.addEventListener("auxclick", (ev) => ev.preventDefault());
  screen.addEventListener("wheel", (ev) => {
    if (!running() || !size) return;
    ev.preventDefault();
    // Crans de molette : 100 px ou 3 lignes par cran
    const unit = ev.deltaMode === 1 ? 1 / 3 : ev.deltaMode === 2 ? 3 : 1 / 100;
    const clamp = (v) => Math.max(-16, Math.min(16, v));
    send(["s", ...point(ev), clamp(-ev.deltaX * unit), clamp(-ev.deltaY * unit)]);
  }, { passive: false });

  // ── Clavier ──
  let composing = false;

  function focusInput() {
    if (document.activeElement !== input) input.focus({ preventScroll: true });
  }

  input.addEventListener("keydown", (ev) => {
    if (ev.isComposing || composing || ev.key === "Process" || ev.key === "Dead") return;
    if (!running()) return;
    const k = ev.key;
    const letter = k.length === 1 ? k.toLowerCase() : "";
    // Ctrl+lettre (pas AltGr = Ctrl+Alt, qui donne des caractères)
    if (ev.ctrlKey && !ev.altKey && !ev.metaKey && letter) {
      if (letter === "v") { ev.preventDefault(); send(["v"]); return; }
      if (letter === "c" || letter === "x") { ev.preventDefault(); copyFromPhone(letter === "x"); return; }
      if (letter >= "a" && letter <= "z") {
        ev.preventDefault();
        send(["p", KEYCODE_A + letter.charCodeAt(0) - 97, metaState(ev)]);
        return;
      }
    }
    if (ev.shiftKey && !ev.ctrlKey && k === "Insert") { ev.preventDefault(); send(["v"]); return; }
    const code = KEYCODES[k];
    if (code) {
      ev.preventDefault();
      send(["k", 0, code, ev.repeat ? 1 : 0, metaState(ev)]);
    }
  });
  input.addEventListener("keyup", (ev) => {
    const code = KEYCODES[ev.key];
    if (code && running() && !composing) {
      ev.preventDefault();
      send(["k", 1, code, 0, metaState(ev)]);
    }
  });
  input.addEventListener("beforeinput", (ev) => {
    if (ev.isComposing || composing) return;
    ev.preventDefault();         // la zone reste vide : tout part au téléphone
    if ((ev.inputType === "insertText" || ev.inputType === "insertReplacementText") && ev.data) send(["x", ev.data]);
  });
  input.addEventListener("compositionstart", () => { composing = true; });
  input.addEventListener("compositionend", (ev) => {
    composing = false;
    if (ev.data) send(["x", ev.data]);
    input.value = "";
  });
  input.addEventListener("paste", (ev) => ev.preventDefault());
  input.addEventListener("focus", () => screen.classList.add("focused"));
  input.addEventListener("blur", () => screen.classList.remove("focused"));

  // ─────────────────────────────────────────
  //  Affichage
  // ─────────────────────────────────────────
  /** Écran à la plus grande taille qui tient dans la zone, proportions gardées. */
  function fit() {
    if (!size) return;
    const [w, hgt] = size;
    const aw = stage.clientWidth - 24, ah = stage.clientHeight - 24;
    if (aw <= 0 || ah <= 0) return;
    const k = Math.min(aw / w, ah / hgt);
    screen.style.width = `${Math.max(1, Math.floor(w * k))}px`;
    screen.style.height = `${Math.max(1, Math.floor(hgt * k))}px`;
  }
  new ResizeObserver(() => { if (ctx.isActive("phone")) fit(); }).observe(stage);

  function showMessage(icon, title, text, actions = [], spin = false) {
    message.hidden = false;
    message.replaceChildren(...[
      h("div", { class: `glyph${spin ? " spin" : ""}` }, ico(icon, 22)),
      h("h3", { text: title }),
      ...(text ? [h("p", { text })] : []),
      ...(actions.length ? [h("div", { class: "empty-actions" }, ...actions)] : [])]);
  }

  function render() {
    const d = current();
    // (replaceChildren écrirait « null » : tableau étalé)
    deviceBtn.replaceChildren(...[ico("smartphone", 14), h("span", { text: d?.name || "Aucun téléphone" }),
      ...(devices.length > 1 ? [ico("chevron-down", 13)] : [])]);
    deviceBtn.classList.toggle("static", devices.length < 2);
    deviceBtn.dataset.tip = devices.length > 1 ? "Choisir le téléphone affiché" : (d ? `Numéro de série : ${d.serial}` : "");
    const live = running();
    const parts = [];
    if (d?.android) parts.push(`Android ${d.android}`);
    if (live && size) parts.push(`${size[0]} × ${size[1]}`);
    sub.textContent = parts.join(" · ");
    for (const btn of controls) if (btn.tagName === "BUTTON") btn.disabled = !live;
    for (const btn of optionButtons) {
      const active = !!store.settings[btn.dataset.option];
      const [icon, tip] = OPTIONS[btn.dataset.option][active ? "on" : "off"];
      btn.replaceChildren(ico(icon, 15));
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-pressed", String(active));
      btn.setAttribute("aria-label", tip);
      btn.dataset.tip = tip;
    }

    const showScreen = live && framed && !decodeFailed;
    screen.hidden = !showScreen;
    nav.hidden = !showScreen;
    if (showScreen) {
      message.hidden = true;
      fit();
      return;
    }
    const retryBtn = h("button", { class: "btn sm", type: "button", onClick: retry }, ico("refresh-cw", 14), "Réessayer");
    if (!d) {
      showMessage("usb", "Aucun téléphone branché",
        "Branchez un téléphone Android en USB avec le débogage USB activé (Options pour les développeurs).");
    } else if (d.state === "unauthorized") {
      showMessage("lock", "Autorisez le débogage USB",
        "Sur le téléphone, acceptez « Autoriser le débogage USB ? » (cochez « Toujours autoriser sur cet ordinateur » pour ne plus avoir à le faire).");
    } else if (d.state === "authorizing" || d.state === "connecting") {
      showMessage("loader-circle", "Connexion au téléphone…", "", [], true);
    } else if (d.state !== "device") {
      showMessage("triangle-alert", `Téléphone ${stateLabel(d.state)}`,
        "Débranchez puis rebranchez le câble, ou désactivez et réactivez le débogage USB sur le téléphone.");
    } else if (decodeFailed) {
      showMessage("triangle-alert", "Affichage impossible", decodeFailed, [retryBtn]);
    } else if (session && session.serial === serial && session.state === "stopped" && session.error) {
      showMessage("triangle-alert", "Connexion au téléphone impossible", session.error, [retryBtn]);
    } else if (!windowVisible || !ctx.isActive("phone")) {
      showMessage("smartphone", d.name, "");
    } else {
      showMessage("loader-circle", `Connexion à ${d.name}…`,
        "Le téléphone peut demander d'autoriser l'enregistrement de l'écran la première fois.", [], true);
    }
  }

  // ─────────────────────────────────────────
  //  Événements de Python
  // ─────────────────────────────────────────
  on("phone:devices", ({ devices: list }) => {
    const before = new Map(devices.map((d) => [d.serial, d.state]));
    devices = list || [];
    // Rebranché, ou enfin autorisé : nouvel essai après un échec
    if (failedSerial && before.get(failedSerial) !== devices.find((d) => d.serial === failedSerial)?.state) failedSerial = null;
    if (!current()) {
      const next = devices.find((d) => d.state === "device") || devices[0];
      serial = next?.serial || null;
      resetDecoder();
    }
    sync();
  });
  on("phone:state", (info) => {
    if (session && info.sid < session.sid) return;
    if (info.serial !== serial) return;
    const wasRunning = running();
    adopt(info);
    if (wasRunning && info.state === "stopped" && !info.error) {
      // Arrêtée sans erreur (section masquée, autre téléphone) : rien à signaler
      resetDecoder();
    }
    sync();
  });
  on("phone:clipboard", ({ ok }) => {
    if (!ctx.isActive("phone")) return;
    if (ok) toast("Texte copié depuis le téléphone");
    else toast("Copie impossible : le presse-papiers de Windows est occupé.", "error");
  });
  on("window:hidden", () => { windowVisible = false; sync(); });
  on("window:shown", () => { windowVisible = true; sync(); });

  // Session déjà ouverte au démarrage de la page (interface rechargée)
  if (session?.state === "running") setTimeout(() => send(["r"]), 0);
  render();

  return {
    el,
    onShow() { sync(); fit(); },
    onHide() { sync(); },
    onWindowShown() { windowVisible = true; sync(); },
    focus() { if (running()) focusInput(); else el.focus({ preventScroll: true }); },
  };
}
