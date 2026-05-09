# Boostache

Application de bureau Windows légère qui vit dans le **system tray**. Elle regroupe un chat LLM local (via Ollama), un terminal intégré, un planificateur de tâches et un gestionnaire de raccourcis clavier globaux — le tout dans une fenêtre sombre toujours au premier plan.

---

## Fonctionnalités

### Chat LLM
- Conversations multi-onglets avec n'importe quel modèle Ollama installé
- Streaming des réponses token par token
- Rendu Markdown : titres, gras/italique, code inline et blocs, tableaux, listes, séparateurs
- Pièces jointes : texte/code et images (collage presse-papiers, capture d'écran, drag & drop)
- Dictée vocale (raccourci Windows intégré)
- Lecture TTS des réponses (SAPI5)
- Pré-prompt système configurable
- Persistance automatique des conversations et restauration des onglets au redémarrage

### Console
- Terminal léger multi-onglets directement dans la fenêtre
- Historique des commandes (↑ / ↓)
- Commande `cd` intégrée avec sélecteur de répertoire
- Drag & drop de fichiers et dossiers
- Envoi sur stdin d'un process en cours (Ctrl+C pour interrompre)
- Ouverture dans un vrai terminal externe (Ctrl+T)
- Lecture TTS de la sortie
- Persistance de l'état (répertoire, historique, contenu) entre les sessions

### Tâches planifiées
- Interface visuelle dans l'onglet **Tâches**
- Créer / modifier / supprimer des tâches depuis l'UI (intervalle ou heure fixe)
- Exécution de code Python arbitraire avec accès au `logger`
- Persistance des tâches custom dans les settings

### Raccourcis clavier globaux
- Définis dans `bindings.py`, actifs même lorsque la fenêtre est masquée
- Listés et déclenchables manuellement dans l'onglet **Raccourcis**

---

## Prérequis

- Python 3.11+
- Windows 10/11
- [Ollama](https://ollama.com) installé et au moins un modèle téléchargé
- Lancer en **administrateur** pour activer les hotkeys globaux

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
python main.py
```

L'application se réduit dans le system tray. Double-clic ou `Ctrl+Alt+D` pour ouvrir le dashboard.

Pour démarrer automatiquement avec Windows, créer un raccourci vers `main.py` (ou un `.bat`) dans `shell:startup`.

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

Après modification, clic droit sur l'icône tray → **Recharger** pour appliquer sans redémarrer.

---

## Structure

```
boostache/
├── main.py              # TrayApp + point d'entrée
├── dashboard.py         # Fenêtre principale (orchestration des onglets)
├── conversation_tab.py  # Onglet chat LLM (+ rendu Markdown)
├── console_tab.py       # Onglet terminal
├── engine.py            # Logger, TaskManager, HotkeyManager, TTSEngine
├── storage.py           # Persistance (settings, conversations, consoles)
├── theme.py             # Palette de couleurs
├── ui_utils.py          # Helpers UI partagés
├── tasks.py             # Tâches planifiées (à personnaliser)
├── bindings.py          # Raccourcis clavier (à personnaliser)
└── requirements.txt
```

---

## Dépendances

| Package | Rôle |
|---|---|
| `ollama` | Client API Ollama (chat LLM) |
| `pystray` | Icône system tray |
| `Pillow` | Manipulation d'images (tray, captures) |
| `schedule` | Planification des tâches |
| `keyboard` | Hotkeys globaux système |
| `tkinterdnd2` | Drag & drop dans Tkinter |
| `platformdirs` | Chemin de données utilisateur cross-platform |
| `pyttsx3` | Text-to-speech (SAPI5 Windows) |
