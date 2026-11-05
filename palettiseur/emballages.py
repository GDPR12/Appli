"""Matières d'emballage : reconnaissance du texte d'un manifeste et règle « ne pas écraser le carton ».

Les matières sont décrites dans ``data/emballages.json`` (modifiable sans toucher au code). Un colis porte
un *code* de matière (« carton », « bois »…) ; le code vide signifie « non précisée ».
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .ressources import chemin_donnees


@dataclass(frozen=True)
class Matiere:
    code: str
    libelle: str
    ecrasable: bool
    mots_cles: tuple[str, ...]


def _normaliser(texte: str) -> str:
    """Majuscules, sans accents, ponctuation remplacée par des espaces."""
    texte = unicodedata.normalize("NFD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", texte.upper()).strip()


class Emballages:
    def __init__(self, chemin: Path | None = None) -> None:
        self.chemin = chemin or chemin_donnees("emballages.json")
        with open(self.chemin, encoding="utf-8") as f:
            data = json.load(f)
        self.matieres: list[Matiere] = [
            Matiere(m["code"], m["libelle"], bool(m.get("ecrasable", False)),
                    tuple(_normaliser(k) for k in m.get("mots_cles", [])))
            for m in data["matieres"]
        ]
        self.par_code = {m.code: m for m in self.matieres}

    def code_depuis_texte(self, texte: str | None) -> str:
        """Code de matière d'un texte libre (« Box, Fibreboard », « carton », « Pallet, Euro »…).

        Un code connu est renvoyé tel quel. Sinon on cherche les mots-clés comme mots entiers, dans l'ordre de
        la table ; un texte inconnu donne « » (non précisée).
        """
        brut = str(texte or "").strip()
        if brut in self.par_code:
            return brut
        mots = f" {_normaliser(brut)} "
        if mots.strip() == "":
            return ""
        for m in self.matieres:
            if any(f" {k} " in mots for k in m.mots_cles if k):
                return m.code
        return ""

    def ecrasable(self, code: str) -> bool:
        """Matière écrasable par défaut (réglage initial de « ne pas écraser »)."""
        m = self.par_code.get(code)
        return bool(m and m.ecrasable)

    def codes_ecrasables(self) -> list[str]:
        return [m.code for m in self.matieres if m.code and m.ecrasable]

    def libelle(self, code: str) -> str:
        m = self.par_code.get(code)
        return m.libelle if m else (code or "Non précisée")

    def description(self) -> list[dict]:
        """Liste pour l'interface : menu déroulant du formulaire."""
        return [{"code": m.code, "libelle": m.libelle, "ecrasable": m.ecrasable} for m in self.matieres]


_EMBALLAGES: Emballages | None = None


def emballages() -> Emballages:
    global _EMBALLAGES
    if _EMBALLAGES is None:
        _EMBALLAGES = Emballages()
    return _EMBALLAGES
