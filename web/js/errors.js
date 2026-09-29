// Chargé avant les modules : collecte les erreurs et affiche un message
// si l'interface n'a pas démarré (plutôt qu'une fenêtre vide).
(function () {
  window.__errs = [];
  function record(msg) {
    window.__errs.push(String(msg));
    if (window.__errs.length > 50) window.__errs.shift();
  }
  window.addEventListener("error", function (e) {
    record((e.message || "Erreur") + (e.filename ? " (" + e.filename.split("/").pop() + ":" + e.lineno + ")" : ""));
  });
  window.addEventListener("unhandledrejection", function (e) {
    record("Promesse rejetée : " + ((e.reason && e.reason.message) || e.reason));
  });
  // Échec de chargement d'un script ou d'une feuille de style (n'atteint pas window.onerror)
  document.addEventListener("error", function (e) {
    var t = e.target;
    if (t && (t.tagName === "SCRIPT" || t.tagName === "LINK")) {
      record("Chargement impossible : " + (t.src || t.href));
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
