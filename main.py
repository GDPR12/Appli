"""Palettiseur A400M : point d'entrée de l'application.

Usage :
    python main.py                # fenêtre native (pywebview) ; navigateur si pywebview absent
    python main.py --navigateur   # force l'ouverture dans le navigateur par défaut
    python main.py --serveur      # serveur seul (pour tests), Ctrl+C pour arrêter
    python main.py --port 8765    # port fixe
"""

from __future__ import annotations

import argparse
import sys
import time
import webbrowser

from palettiseur import __version__
from palettiseur.serveur import Serveur

TITRE = "Palettisation A400M – confection des palettes"


def lancer_fenetre(url: str) -> bool:
    """Ouvre l'interface dans une fenêtre native. Retourne False si pywebview est indisponible."""
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    fenetre = webview.create_window(TITRE, url, width=1480, height=920, min_size=(1100, 700))
    try:
        webview.start(private_mode=False)
    except TypeError:  # anciennes versions de pywebview
        webview.start()
    del fenetre
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=TITRE)
    parser.add_argument("--navigateur", action="store_true", help="ouvrir dans le navigateur plutôt qu'une fenêtre")
    parser.add_argument("--serveur", action="store_true", help="démarrer uniquement le serveur local")
    parser.add_argument("--port", type=int, default=None, help="port TCP local (aléatoire par défaut)")
    parser.add_argument("--verbeux", action="store_true", help="journaliser les requêtes HTTP")
    parser.add_argument("--version", action="version", version=f"Palettiseur A400M {__version__}")
    args = parser.parse_args(argv)

    serveur = Serveur(args.port, args.verbeux).demarrer()
    print(f"Palettiseur A400M {__version__} : interface disponible sur {serveur.url}")
    try:
        if args.serveur:
            while True:
                time.sleep(1)
        elif args.navigateur or not lancer_fenetre(serveur.url):
            if not args.navigateur:
                print("pywebview n'est pas installé : ouverture dans le navigateur "
                      "(pip install pywebview pour une fenêtre native).")
            webbrowser.open(serveur.url)
            print("Laissez cette fenêtre ouverte ; Ctrl+C pour quitter.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        serveur.arreter()
    return 0


if __name__ == "__main__":
    sys.exit(main())
