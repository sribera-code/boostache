# Boostache

Application de bureau Windows légère qui vit dans le **system tray**. Elle regroupe un chat LLM local (via Ollama), des terminaux intégrés, un éditeur de notes, un historique du presse-papiers, des captures d'écran annotables, WhatsApp Web et Gmail avec un assistant de réponse, un planificateur de tâches et un gestionnaire de raccourcis clavier globaux — le tout dans une fenêtre sombre et moderne, toujours au premier plan.

L'interface est une page web (HTML/CSS/JS) affichée par WebView2 via [pywebview] ; toute la logique reste en Python.

---

## Fonctionnalités

### Conversations (chat LLM)
- Conversations multi-onglets avec n'importe quel modèle Ollama installé
- Streaming des réponses token par token, bouton **Arrêter** pendant la génération
- Réflexion des modèles « thinking » (gemma, qwen3, deepseek-r1…) affichée en direct dans un bloc repliable
- Rendu Markdown complet : titres, listes, tableaux, citations, liens, blocs de code avec coloration syntaxique et bouton **Copier**
- Choix du modèle par onglet (sélecteur dans la zone de saisie ou clic droit)
- Pièces jointes : texte/code et images (bouton trombone, collage presse-papiers, capture d'écran, glisser-déposer), avec aperçu
- Copier un message envoyé avec ses images (pleine résolution), ou une réponse en Markdown + mise en forme (collée telle quelle dans Word, Outlook…) ; clic droit sur une image jointe : copier, ouvrir dans Captures
- Clic sur une image (envoyée ou jointe) : visionneuse plein écran, clic sur l'image pour la taille réelle
- **Modifier** la dernière question (pièces jointes gardées) : la réponse est regénérée
- **Fork** depuis n'importe quelle question : nouvel onglet avec la conversation jusqu'à cette question et sa réponse, pour repartir dans une autre direction
- Dictée vocale (Win+H) et lecture TTS des réponses (SAPI5)
- Pré-prompt système configurable
- Persistance automatique des conversations et restauration des onglets au redémarrage
- Présence d'Ollama surveillée : lancé après Boostache, arrêté puis relancé, il est détecté tout seul (essais de plus en plus espacés tant qu'il ne répond pas ; en ligne, test à l'affichage de la fenêtre, toutes les 30 s tant qu'elle est affichée et quand un appel échoue). Tant qu'il ne répond pas, tout ce qui a besoin de lui est masqué : onglet Conversations, aide contextuelle (Ctrl+Impr. écran rendu à Windows), « Joindre à la conversation » dans Captures, assistant de réponse de WhatsApp et Gmail, pré-prompt dans les paramètres

### Aide contextuelle (Ctrl+Impr. écran)
- **Ctrl+Impr. écran**, n'importe où dans Windows : capture de la fenêtre active (même en partie cachée) et ouverture d'une conversation **Aide** dans Boostache
- Le texte de la fenêtre est lu par l'OCR intégré à Windows : les petits modèles locaux voient les images en basse résolution et ne pourraient pas lire un message d'erreur
- Le modèle propose 4 questions d'aide adaptées à ce qui est affiché (erreur, tâche en cours…) ; un clic sur une proposition la pose, clic droit pour la modifier avant, ou écrire sa propre question
- La question part avec la capture et le texte de la fenêtre ; la suite de la conversation garde ce contexte, les autres propositions restent disponibles (bouton **Autres propositions** pour en générer de nouvelles)
- Modèle choisi de préférence parmi ceux qui lisent les images (gemma3, gemma4, llava, qwen2.5vl…) et retenu pour les aides suivantes ; un modèle texte seul reçoit uniquement le texte de la fenêtre
- Désactivable dans les **Paramètres** (Captures) : Ctrl+Impr. écran retrouve alors son effet Windows

### Consoles
- Vrais terminaux (ConPTY + xterm.js) : couleurs, barres de progression, programmes interactifs, historique ↑/↓ du shell, Ctrl+C
- PowerShell ou Invite de commandes, au choix par console (PowerShell 7 détecté s'il est installé)
- Suivi du répertoire courant, affiché dans la barre et utilisé comme titre d'onglet
- Changer de dossier : clic sur le chemin, ou glisser-déposer d'un dossier dessus
- Glisser-déposer de fichiers dans le terminal : insère leurs chemins
- Ouverture dans un terminal externe (Ctrl+T, Windows Terminal si présent)
- Lecture TTS de la dernière sortie ou de tout le contenu
- Persistance entre les sessions (répertoire, contenu, nom de l'onglet)

### Notes
- Éditeur de texte multi-onglets, un onglet = une note
- Titre automatique tiré de la première ligne tant que l'onglet n'est pas renommé
- Compteur de mots et de caractères, sauvegarde automatique
- Lecture TTS de la sélection ou de toute la note, dictée vocale

### Presse-papiers
- Capture automatique de chaque copie texte (notification Win32 `AddClipboardFormatListener`, sans polling)
- Recherche instantanée, aperçu, visionneuse du contenu complet
- Copier, lire, supprimer, tout effacer ; navigation au clavier (↑/↓, Entrée, Suppr)
- Contenus marqués confidentiels (gestionnaires de mots de passe) ignorés
- Historique **en mémoire uniquement** (rien sur disque), taille maximale configurable
- La vue est quittée automatiquement quand la fenêtre est masquée

### Captures
- **Impr. écran**, n'importe où dans Windows : sélection d'une zone de l'écran (outil Capture de Windows), ouverte dans un nouvel onglet — un onglet par image
- Éditeur façon Paint : crayon, surligneur, gomme (fait réapparaître la capture d'origine), ligne, flèche, rectangle, ellipse (Maj : angles de 45°, carré, cercle), texte, remplissage, pipette, pixellisation d'une zone
- **Sélection** d'un cadre, déplaçable et redimensionnable directement sur l'image (poignées, flèches du clavier) : copier (Ctrl+C), **rogner** l'image (Ctrl+Maj+X), enregistrer, pixelliser ou effacer la zone (Suppr) ; Ctrl+A sélectionne tout
- Palette de couleurs + couleur libre, quatre épaisseurs, formes et texte pleins
- Annuler / Rétablir (Ctrl+Z, Ctrl+Y), zoom (Ctrl+molette ; Ctrl+0 ajuste l'image à la fenêtre, petites captures comprises), déplacement (Espace ou clic molette + glisser)
- Copier l'image (Ctrl+C sans sélection), **Enregistrer sous** (PNG, JPEG, WebP, BMP ; Ctrl+S), joindre à une conversation
- Le bouton **+** ouvre un onglet vierge : capturer une zone, coller une image (Ctrl+V) ou en ouvrir une ; elle prend la place de l'onglet. Glisser-déposer des images fonctionne aussi
- Onglets et retouches conservés entre les sessions ; la touche Impr. écran peut être rendue à Windows dans les **Paramètres**

### WhatsApp et Gmail
- WhatsApp Web et Gmail directement dans la fenêtre (sections **WhatsApp** et **Gmail**) : QR code à scanner (WhatsApp) ou connexion Google (Gmail) une seule fois, la session est conservée entre les lancements
- Chargés à la première ouverture de leur section, puis gardés en arrière-plan : le nombre de non-lus s'affiche dans la barre latérale
- Liens des messages ouverts dans le navigateur par défaut ; Ctrl+1 à Ctrl+9 restent actifs
- **Assistant de réponse** (Ollama, en local — affiché tant qu'Ollama répond) sous le site :
  - **Suggérer des réponses** : trois propositions adaptées à la discussion ou à l'e-mail ouvert ; un clic place la réponse dans la zone de saisie (Gmail : la réponse s'ouvre si besoin, signature et citation sont conservées)
  - **Améliorer le brouillon** : corrige et reformule le message en cours de saisie (bouton **Rétablir l'original**)
  - Un brouillon remplacé par une proposition peut être récupéré (**Rétablir mon brouillon**)
  - Consigne facultative (« plus formel », « décline poliment », « accepte mardi 14 h »…) et choix du modèle
  - Rien n'est jamais envoyé automatiquement : il reste à relire et envoyer
- Bouton **Recharger** (ou **Réessayer** si le site s'est arrêté)

### Historique, tâches et raccourcis
- **Historique** : journal de l'application
- **Tâches** : tâches de `tasks.py` + tâches créées depuis l'interface (intervalle ou heure fixe, code Python avec accès au `logger`), exécution manuelle
- **Raccourcis** : raccourcis globaux de `bindings.py` (et Impr. écran, Ctrl+Impr. écran), actifs même fenêtre masquée, déclenchables à la main

### Interface
- Barre latérale repliable, onglets renommables (double-clic ou F2), menus contextuels
- Navigation au clavier : Ctrl+1 à Ctrl+9 pour les sections, Ctrl+, pour les paramètres
- **Écran partagé** : deux sections côte à côte (Conversations et Consoles, par exemple) — bouton en haut de la barre latérale ou `Ctrl+Shift+S`. La barre latérale ouvre les sections dans le volet actif (souligné en haut) ; `Ctrl+clic` ou clic du milieu sur une section l'ouvre dans l'autre volet ; clic droit : « Ouvrir à gauche / à droite », ou glisser la section vers une moitié de l'écran. Séparateur redimensionnable (double-clic : parts égales ; clic droit : inverser, fermer un volet), `F6` pour passer d'un volet à l'autre ; la disposition est retrouvée au démarrage
- Fermer ou réduire la fenêtre la range dans le tray ; `Ctrl+Shift+D` la rappelle
- Bouton **épingle** (bas de la barre latérale) : garder n'importe quelle fenêtre ouverte au premier plan, Boostache compris — chaque fenêtre avec l'icône de son application, une miniature et son titre complet ; un curseur règle sa transparence (20 à 100 %, double-clic pour la rendre opaque ; celle de Boostache est mémorisée) ; filtre au clavier et « Tout libérer »

---

## Prérequis

- Python 3.11+
- Windows 10/11 avec le runtime [WebView2] (installé d'office avec Edge ; sinon, le télécharger)
- [Ollama](https://ollama.com) installé et au moins un modèle téléchargé (les fonctions qui en ont besoin n'apparaissent que lorsque son serveur répond)
- Lancer en **administrateur** pour activer les hotkeys globaux dans toutes les applications

---

## Installation

```bash
git clone https://github.com/sribera-code/boostache.git
cd boostache
pip install -r requirements.txt
```

---

## Démarrage

```bash
python main.py            # démarre dans le tray
python main.py --show     # affiche la fenêtre dès le démarrage
python main.py --debug    # outils de développement web (Ctrl+Maj+clic droit → Inspecter)
```

L'application se range dans le system tray. Double-clic sur l'icône ou `Ctrl+Shift+D` pour ouvrir la fenêtre.

Pour démarrer automatiquement avec Windows, créer un raccourci vers `pythonw.exe main.py` (voir `Boostache.lnk`) dans `shell:startup`.

---

## Personnalisation

### Tâches planifiées — `tasks.py`

```python
def register():
    def ma_tache():
        logger.log("Ma tâche s'exécute.")

    task_manager.add(
        schedule.every(30).minutes.do(ma_tache),
        label="Ma tâche (toutes les 30 min)"
    )
```

### Raccourcis clavier — `bindings.py`

```python
def register(open_dashboard_fn):
    def mon_action():
        logger.log("Action déclenchée.")

    hotkey_manager.add("ctrl+alt+x", mon_action, label="Mon action")
```

Après modification, clic droit sur l'icône tray → **Recharger** : Boostache relance proprement le process pour prendre en compte tous les changements (interface, tâches, raccourcis).

### Interface — `web/`

L'interface n'a pas d'étape de compilation : modifier un fichier de `web/` puis **Recharger** suffit. Les couleurs sont des variables CSS en tête de `web/css/app.css`.

---

## Structure

```
boostache/
├── main.py               # Point d'entrée + icône du tray
├── app.py                # Fenêtre pywebview, cycle de vie, état initial de l'interface
├── api.py                # Méthodes Python appelées par l'interface
├── bridge.py             # Événements Python → interface (file + regroupement)
├── chat.py               # Conversations Ollama (streaming, réflexion, pièces jointes, aide contextuelle)
├── ocr.py                # Texte d'une image (OCR de Windows, via PowerShell)
├── terminals.py          # Consoles : shells ConPTY (pywinpty)
├── notes.py              # Notes
├── captures.py           # Captures : zone de l'écran (Win+Maj+S), onglets, export
├── webpane.py           # Sites intégrés : contrôle WebView2 posé sur la fenêtre, assistant de réponse
├── whatsapp.py           # WhatsApp Web (lecture de la discussion, zone de saisie)
├── gmail.py              # Gmail (lecture du fil, éditeur de réponse)
├── custom_tasks.py       # Tâches créées depuis l'interface, raccourcis
├── clipboard_listener.py # Presse-papiers Win32 (écoute push, lecture, écriture)
├── winutil.py            # Utilitaires Win32 (premier plan, fenêtre active, icônes, miniatures et captures des fenêtres, explorateur, liens, touche Impr. écran)
├── engine.py             # Logger, TaskManager, HotkeyManager, TTSEngine
├── storage.py            # Persistance (réglages, conversations, consoles, notes, captures)
├── tasks.py              # Tâches planifiées (à personnaliser)
├── bindings.py           # Raccourcis clavier (à personnaliser)
├── web/
│   ├── index.html
│   ├── css/app.css       # Thème et mise en page
│   ├── js/               # main.js (coquille), layout.js (écran partagé), ui.js (composants), pin.js (panneau premier plan), views/ (une vue par section)
│   └── vendor/           # Bibliothèques embarquées (voir vendor/licenses)
└── requirements.txt
```

Les données sont stockées dans `%LOCALAPPDATA%\Boostache\Boostache` (bouton **Ouvrir** dans les paramètres) ; les sessions WhatsApp Web et Gmail sont dans les sous-dossiers `whatsapp` et `gmail` (les supprimer déconnecte). La variable d'environnement `BOOSTACHE_DATA_DIR` permet d'utiliser un autre dossier, par exemple pour des essais.

---

## Dépendances

| Package | Rôle |
|---|---|
| `pywebview` | Fenêtre native affichant l'interface web (WebView2) |
| `pywinpty` | Pseudo-consoles Windows (ConPTY) pour les terminaux |
| `ollama` | Client API Ollama (chat LLM) |
| `pystray` | Icône system tray |
| `Pillow` | Images (tray, captures, miniatures) |
| `schedule` | Planification des tâches |
| `keyboard` | Hotkeys globaux système |
| `platformdirs` | Chemin de données utilisateur |
| `pyttsx3` | Text-to-speech (SAPI5 Windows) |

Embarqués dans `web/vendor/` (aucun accès réseau) : [markdown-it], [highlight.js], [xterm.js], icônes [Lucide], polices Inter et JetBrains Mono.

[pywebview]: https://pywebview.flowrl.com
[WebView2]: https://developer.microsoft.com/microsoft-edge/webview2/
[markdown-it]: https://github.com/markdown-it/markdown-it
[highlight.js]: https://highlightjs.org
[xterm.js]: https://xtermjs.org
[Lucide]: https://lucide.dev
