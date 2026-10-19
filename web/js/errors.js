// Chargé avant les modules : collecte les erreurs et affiche un message
// si l'interface n'a pas démarré (plutôt qu'une fenêtre vide). Erreurs et
// avertissements (console.error / console.warn compris) partent aussi dans le
// journal de Boostache (section Journal, fichier de la session).
(function () {
  var ORIGIN = location.origin + "/";
  var BURST = 30, BURST_MS = 10000;     // au plus 30 envois par tranche de 10 s
  var queue = [], sent = 0, burstStart = 0, sending = false;

  window.__errs = [];
  function record(msg) {
    window.__errs.push(String(msg));
    if (window.__errs.length > 50) window.__errs.shift();
  }

  /** Chemins relatifs dans les traces (« js/views/chat.js:12:5 »). */
  function short(text) { return String(text || "").split(ORIGIN).join(""); }

  function flush() {
    var api = window.pywebview && window.pywebview.api;
    if (!api || !api.logs_client || sending) return;
    sending = true;
    try {
      while (queue.length) {
        var e = queue.shift();
        var p = api.logs_client(e[0], e[1], e[2]);
        if (p && p.catch) p.catch(function () {});
      }
    } catch (_) { /* pont indisponible : tant pis */ }
    sending = false;
  }

  /** Envoi au journal (Python), dès que pywebview est prêt. */
  function journal(level, message, details) {
    var now = Date.now();
    if (now - burstStart > BURST_MS) { burstStart = now; sent = 0; }
    if (++sent > BURST) return;
    queue.push([level, short(message).slice(0, 2000), short(details).slice(0, 12000)]);
    flush();
  }
  window.__journal = journal;
  window.addEventListener("pywebviewready", flush);

  function describe(args) {
    var parts = [], stack = "";
    for (var i = 0; i < args.length; i++) {
      var a = args[i];
      if (a instanceof Error) {
        parts.push(a.message || String(a));
        if (!stack) stack = a.stack || "";
      } else if (a && typeof a === "object") {
        try { parts.push(JSON.stringify(a).slice(0, 500)); } catch (_) { parts.push(String(a)); }
      } else parts.push(String(a));
    }
    return [parts.join(" "), stack];
  }
  ["error", "warn"].forEach(function (name) {
    var original = console[name];
    console[name] = function () {
      original.apply(console, arguments);
      try {
        var d = describe(arguments);
        journal(name === "error" ? "error" : "warning", d[0], d[1]);
      } catch (_) { /* jamais d'erreur en signalant une erreur */ }
    };
  });

  window.addEventListener("error", function (e) {
    if (!e.message && !e.error) return;    // chargement d'une ressource : voir plus bas
    var where = e.filename ? " (" + e.filename.split("/").pop() + ":" + e.lineno + ")" : "";
    record((e.message || "Erreur") + where);
    journal("error", (e.message || "Erreur") + where, e.error && e.error.stack);
  });
  window.addEventListener("unhandledrejection", function (e) {
    var reason = e.reason;
    var msg = "Promesse rejetée : " + ((reason && reason.message) || reason);
    record(msg);
    journal("error", msg, reason && reason.stack);
  });
  // Échec de chargement d'un script ou d'une feuille de style (n'atteint pas window.onerror)
  document.addEventListener("error", function (e) {
    var t = e.target;
    if (t && (t.tagName === "SCRIPT" || t.tagName === "LINK")) {
      record("Chargement impossible : " + (t.src || t.href));
      journal("error", "Chargement impossible : " + (t.src || t.href));
    }
  }, true);
  setTimeout(function () {
    var app = document.getElementById("app");
    if (!app || !app.classList.contains("booting") || !window.__errs.length) return;
    var pre = document.createElement("pre");
    pre.style.cssText = "position:fixed;inset:24px;margin:0;padding:20px;overflow:auto;color:#ee7d7d;"
      + "background:#141414;border:1px solid #333;border-radius:12px;font:12.5px/1.6 Consolas,monospace;white-space:pre-wrap";
    pre.textContent = "L'interface n'a pas pu démarrer :\n\n" + window.__errs.join("\n");
    document.body.appendChild(pre);
  }, 8000);
})();
