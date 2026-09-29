// Pont avec Python : appels de méthodes (api) et événements poussés (on).

const listeners = new Map();
const collectors = [];

/** S'abonne à un événement émis par Python. Retourne une fonction de désabonnement. */
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

/** Déclenche un événement localement (utile entre vues). */
export function emit(event, data) {
  const set = listeners.get(event);
  if (!set) return;
  for (const fn of set) {
    try { fn(data); } catch (err) { console.error(`[${event}]`, err); }
  }
}

/** Enregistre une fonction qui complète l'état à sauvegarder avant fermeture. */
export function onCollect(fn) { collectors.push(fn); }

// Points d'entrée appelés par Python (bridge.py, app.py)
window.__boostache = {
  dispatch(batch) {
    for (const { event, data } of batch) emit(event, data);
  },
  collectState() {
    const state = { notes: {}, terminals: {}, captures: {} };
    for (const fn of collectors) {
      try { fn(state); } catch (err) { console.error(err); }
    }
    return state;
  },
};

/** Attend que pywebview ait injecté son API. */
export function ready() {
  return new Promise((resolve) => {
    if (window.pywebview?.api?.ui_ready) return resolve();
    window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
}

let errorHandler = (msg) => console.error(msg);
export function setErrorHandler(fn) { errorHandler = fn; }

/** api.nom(...args) → Promise. Une exception Python devient une notification
 *  d'erreur et la promesse se résout à null. */
export const api = new Proxy({}, {
  get(_, name) {
    return async (...args) => {
      const fn = window.pywebview?.api?.[name];
      if (!fn) {
        errorHandler(`Fonction indisponible : ${String(name)}`);
        return null;
      }
      try {
        return await fn(...args);
      } catch (err) {
        errorHandler(err?.message || String(err));
        return null;
      }
    };
  },
});
