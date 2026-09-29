// Rendu Markdown des réponses (markdown-it + highlight.js).
// Le HTML brut est désactivé : une réponse du modèle ne peut pas injecter de code.
import { icon } from "./icons.js";

const md = window.markdownit({ html: false, linkify: true, breaks: true, typographer: false });
const esc = md.utils.escapeHtml;
const CARET = "\uE000";   // marqueur (zone à usage privé) remplacé par le curseur

md.renderer.rules.fence = (tokens, idx, _options, env) => {
  const token = tokens[idx];
  const info = (token.info || "").trim().split(/\s+/)[0] || "";
  const lang = info.toLowerCase();
  if (env?.plain) return `<pre><code>${esc(token.content)}</code></pre>\n`;
  let body = esc(token.content);
  if (env?.highlight && lang && window.hljs?.getLanguage(lang)) {
    try {
      body = window.hljs.highlight(token.content, { language: lang, ignoreIllegals: true }).value;
    } catch { /* texte brut */ }
  }
  return `<div class="code-block"><div class="code-head"><span>${esc(info || "code")}</span>`
    + `<button class="code-copy" type="button" data-copy-code>${icon("copy", 13)}<span>Copier</span></button></div>`
    + `<pre><code class="hljs">${body}</code></pre></div>`;
};

// Les images distantes sont bloquées par la politique de sécurité : on affiche un lien.
md.renderer.rules.image = (tokens, idx) => {
  const token = tokens[idx];
  const src = token.attrGet("src") || "";
  const alt = token.content || "image";
  return `<a href="${esc(src)}">${esc(alt)}</a>`;
};

/** Sépare la réflexion inline (<think>…</think>, anciens modèles) du contenu. */
export function splitThinking(text) {
  const m = /^\s*<think>([\s\S]*?)(<\/think>|$)/.exec(text || "");
  if (!m) return { thinking: "", content: text || "", open: false };
  return { thinking: m[1].trim(), content: text.slice(m[0].length).replace(/^\s+/, ""), open: !m[2] };
}

/** HTML sans les éléments d'interface (en-têtes et boutons des blocs de code), pour la copie. */
export function renderMarkdownForCopy(text) {
  return md.render(text || "", { plain: true });
}

/**
 * @param {string} text
 * @param {{highlight?: boolean, caret?: boolean}} opts
 *   highlight : coloration syntaxique (désactivée pendant le streaming)
 *   caret     : curseur clignotant en fin de texte (streaming)
 */
export function renderMarkdown(text, { highlight = true, caret = false } = {}) {
  let html = md.render(caret ? `${text}${CARET}` : text, { highlight });
  if (caret) html = html.replace(CARET, '<span class="caret"></span>');
  return html;
}
