"""Classes de colis spéciaux et règles de séparation.

Les règles IATA (tableau 9.3.A) sont lues dans ``data/iata_9_3_A.json`` afin
que les agents puissent les corriger sans toucher au code. Ce module ajoute
les colis spéciaux non IATA du projet (armement) et les couleurs d'affichage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .ressources import chemin_donnees


@dataclass(frozen=True)
class ClasseColis:
    code: str
    libelle: str
    couleur: str          # couleur d'affichage (hex)
    iata: bool            # étiquette IATA (pictogramme) -> doit être au bord de la palette
    description: str = ""


# Ordre d'affichage du menu déroulant de l'interface.
CLASSES: list[ClasseColis] = [
    ClasseColis("",     "Aucun (colis ordinaire)",                       "#b8c2cc", False),
    ClasseColis("1",    "Explosifs (classe 1)",                          "#ff7f0e", True,
                "Munitions, artifices, charges… hors 1.4S."),
    ClasseColis("1.4S", "Explosifs 1.4S (danger minime)",                "#ffbb78", True,
                "Munitions de petit calibre conditionnées 1.4S : pas de séparation exigée."),
    ClasseColis("2.1",  "Gaz inflammable (2.1)",                         "#d62728", True),
    ClasseColis("2.2",  "Gaz non inflammable, non toxique (2.2)",        "#2ca02c", True),
    ClasseColis("2.3",  "Gaz toxique (2.3)",                             "#8c564b", True),
    ClasseColis("3",    "Liquide inflammable / carburant (classe 3)",    "#e8353b", True),
    ClasseColis("4.1",  "Solide inflammable (4.1)",                      "#bcbd22", True),
    ClasseColis("4.2",  "Matière auto-inflammable (4.2)",                "#ff9896", True),
    ClasseColis("4.3",  "Dangereux au contact de l'eau (4.3)",           "#1f77b4", True),
    ClasseColis("5.1",  "Comburant (5.1)",                               "#ffd400", True),
    ClasseColis("5.2",  "Peroxyde organique (5.2)",                      "#f28e2b", True),
    ClasseColis("6.1",  "Matière toxique (6.1)",                         "#9467bd", True),
    ClasseColis("6.2",  "Matière infectieuse (6.2)",                     "#c5b0d5", True),
    ClasseColis("7",    "Matière radioactive (classe 7)",                "#17becf", True),
    ClasseColis("8",    "Matière corrosive (classe 8)",                  "#3d4a5c", True),
    ClasseColis("9",    "Divers (classe 9)",                             "#98df8a", True),
    ClasseColis("9L",   "Piles / batteries au lithium (classe 9)",       "#4e9bd8", True),
    ClasseColis("ARME", "Armement : arme",                               "#5c6b2f", False,
                "Doit voyager sur une autre palette que sa pièce de sécurité."),
    ClasseColis("SECU", "Armement : pièce de sécurité",                  "#a8b86b", False,
                "Doit voyager sur une autre palette que l'arme."),
]

PAR_CODE: dict[str, ClasseColis] = {c.code: c for c in CLASSES}


def classe(code: str | None) -> ClasseColis:
    """Retourne la classe du code, ou une classe générique pour un code inconnu."""
    code = (code or "").strip()
    if code in PAR_CODE:
        return PAR_CODE[code]
    return ClasseColis(code, f"Classe {code}", "#ff00ff", True)


class ReglesSeparation:
    """Règles de séparation chargées depuis le fichier JSON."""

    def __init__(self, chemin: Path | None = None) -> None:
        self.chemin = chemin or chemin_donnees("iata_9_3_A.json")
        with open(self.chemin, encoding="utf-8") as f:
            data = json.load(f)
        self.colonnes: list[str] = data["colonnes"]
        self.correspondance: dict[str, str] = data.get("correspondance_classes", {})
        self.notes: dict[str, str] = data.get("notes", {})
        self.incompatibles: set[frozenset[str]] = set()
        for a, liste in data["incompatibilites"].items():
            for b in liste:
                self.incompatibles.add(frozenset((a, b)))
        # Vérification de symétrie : le tableau IATA est symétrique par construction.
        for paire in list(self.incompatibles):
            a, b = tuple(paire) if len(paire) == 2 else (next(iter(paire)),) * 2
            self.incompatibles.add(frozenset((a, b)))
        self.autres: dict[frozenset[str], str] = {}
        for regle in data.get("autres_separations", []):
            self.autres[frozenset((regle["a"], regle["b"]))] = regle.get("motif", "")

    # ------------------------------------------------------------------
    def colonne(self, code: str) -> str | None:
        return self.correspondance.get(code)

    def motif_incompatibilite(self, code_a: str, code_b: str) -> str | None:
        """Retourne le motif si deux codes ne peuvent pas partager une palette, sinon None."""
        if not code_a or not code_b:
            return None
        paire = frozenset((code_a, code_b))
        if paire in self.autres:
            return self.autres[paire]
        ca, cb = self.colonne(code_a), self.colonne(code_b)
        if ca and cb and frozenset((ca, cb)) in self.incompatibles:
            return (f"Tableau IATA 9.3.A : les étiquettes {code_a} et {code_b} "
                    f"ne doivent pas être chargées ensemble.")
        return None

    def compatibles(self, code_a: str, code_b: str) -> bool:
        return self.motif_incompatibilite(code_a, code_b) is None

    def avertissement(self, code_a: str, code_b: str) -> str | None:
        """Avertissement (non bloquant) pour une paire de classes acceptée avec réserve."""
        if code_a == "1" and code_b == "1":
            return self.notes.get("1")
        return None

    def matrice(self) -> dict[str, list[str]]:
        """Matrice code -> liste des codes incompatibles, pour l'interface."""
        codes = [c.code for c in CLASSES if c.code]
        return {a: [b for b in codes if not self.compatibles(a, b)] for a in codes}


_REGLES: ReglesSeparation | None = None


def regles() -> ReglesSeparation:
    global _REGLES
    if _REGLES is None:
        _REGLES = ReglesSeparation()
    return _REGLES


def description_classes() -> list[dict]:
    """Description des classes pour l'interface (menu déroulant + légende)."""
    return [{"code": c.code, "libelle": c.libelle, "couleur": c.couleur,
             "iata": c.iata, "description": c.description} for c in CLASSES]
