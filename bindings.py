"""
bindings.py – Raccourcis clavier globaux de Vigil
C'est ici que tu associes des combinaisons de touches à des actions.

Ajoute tes raccourcis dans register(), en suivant les exemples fournis.
Prérequis Windows : lancer Vigil en administrateur pour les hotkeys globaux.
"""

import datetime

from engine import hotkey_manager, logger


def register(open_dashboard_fn):
    """
    Appelé au démarrage. Enregistre tous les hotkeys globaux.

    `open_dashboard_fn` est injecté par main.py pour éviter
    une dépendance circulaire entre bindings et l'UI.
    """

    # ── Exemples ──────────────────────────────────────────────────────────────

    def show_dashboard():
        open_dashboard_fn()

    def log_time():
        now = datetime.datetime.now().strftime("%H:%M:%S")
        logger.log(f"⌚ Heure courante : {now}")

    hotkey_manager.add("ctrl+alt+d", show_dashboard,
                       label="Ouvrir le dashboard")
    hotkey_manager.add("ctrl+alt+t", log_time,
                       label="Logger l'heure courante")

    # ── Tes raccourcis ────────────────────────────────────────────────────────
    # Décommente et adapte selon tes besoins.

    # def mon_action():
    #     logger.log("Mon action déclenchée par hotkey.")
    #     # ... ta logique ici

    # hotkey_manager.add("ctrl+alt+x", mon_action,
    #                    label="Description de mon action")

    # Syntaxe des combinaisons :
    #   "ctrl+alt+x"        Ctrl + Alt + X
    #   "ctrl+shift+f1"     Ctrl + Shift + F1
    #   "win+r"             Touche Windows + R
    #   "f9"                Juste F9 (attention aux conflits !)
