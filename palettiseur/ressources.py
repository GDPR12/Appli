"""Localisation des fichiers de ressources (fonctionne aussi dans un exécutable PyInstaller)."""

from __future__ import annotations

import sys
from pathlib import Path


def racine() -> Path:
    """Dossier contenant ``ui/`` et ``data/``."""
    if getattr(sys, "frozen", False):  # exécutable PyInstaller
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        if (base / "ui").exists():
            return base
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def chemin_donnees(nom: str) -> Path:
    return racine() / "data" / nom


def dossier_interface() -> Path:
    return racine() / "ui"


def dossier_exports() -> Path:
    """Dossier d'écriture des exports (à côté de l'exécutable ou du projet)."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = racine()
    d = base / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d
