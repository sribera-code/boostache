"""
tasks.py – Tâches planifiées de Vigil
C'est ici que tu définis ce que Vigil fait en arrière-plan.

Ajoute tes tâches dans register(), en suivant les exemples fournis.
Documentation schedule : https://schedule.readthedocs.io
"""

import datetime
import schedule

from engine import task_manager, logger


def register():
    """
    Appelé au démarrage. Enregistre toutes les tâches planifiées.
    C'est le seul endroit à modifier pour gérer tes tâches.
    """

    # ── Exemples ──────────────────────────────────────────────────────────────

    def heartbeat():
        logger.log("Heartbeat – Boostache est actif ✓")

    def daily_reminder():
        logger.log("🔔 Rappel : pense à sauvegarder ton travail !")

    task_manager.add(
        schedule.every(1).minutes.do(heartbeat),
        label="Heartbeat (toutes les minutes)"
    )
    task_manager.add(
        schedule.every().day.at("09:00").do(daily_reminder),
        label="Rappel quotidien à 09:00"
    )

    # ── Tes tâches ────────────────────────────────────────────────────────────
    # Décommente et adapte selon tes besoins.

    # def ma_tache():
    #     logger.log("Ma tâche s'exécute.")
    #     # ... ta logique ici

    # task_manager.add(
    #     schedule.every(30).minutes.do(ma_tache),
    #     label="Ma tâche (toutes les 30 min)"
    # )

    # Autres intervalles utiles :
    #   schedule.every(10).seconds.do(fn)
    #   schedule.every().hour.do(fn)
    #   schedule.every().day.at("18:30").do(fn)
    #   schedule.every().monday.at("08:00").do(fn)
    #   schedule.every().week.do(fn)
