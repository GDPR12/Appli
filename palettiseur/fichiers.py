"""Import (CSV / JSON) des listes de colis et export du plan de palettisation."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .modeles import Colis, Resultat

COLONNES_CSV = ["numero", "longueur", "largeur", "hauteur", "masse", "classe", "fragile", "emballage", "reference", "commentaire"]

# Noms de colonnes acceptés à l'import (minuscules, sans accents)
ALIAS = {
    "numero": ["numero", "numéro", "id", "n°", "no", "colis", "ref"],
    "longueur": ["longueur", "long", "l", "length"],
    "largeur": ["largeur", "larg", "w", "width"],
    "hauteur": ["hauteur", "haut", "h", "height"],
    "masse": ["masse", "poids", "kg", "weight", "m"],
    "classe": ["classe", "class", "iata", "dgr", "type", "special", "spécial"],
    "fragile": ["fragile"],
    "emballage": ["emballage", "matiere", "matiere d'emballage", "packing", "type of packing", "materiau"],
    "reference": ["reference", "serie", "numero de serie", "serial", "sscc"],
    "commentaire": ["commentaire", "comment", "description", "libelle", "libellé", "contenu", "remarque"],
}


def _normaliser(nom: str) -> str:
    nom = nom.strip().lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ç", "c"), ("°", "")):
        nom = nom.replace(a, b)
    return nom.strip(" .:")


def _colonne(entete: list[str], champ: str) -> int | None:
    normalises = [_normaliser(e) for e in entete]
    for alias in ALIAS[champ]:
        alias = _normaliser(alias)
        if alias in normalises:
            return normalises.index(alias)
    return None


def lire_csv(texte: str) -> tuple[list[Colis], list[str]]:
    """Lit une liste de colis depuis un texte CSV (séparateur ; , ou tabulation).

    Retourne (colis, avertissements). Les lignes invalides sont ignorées avec un message.
    """
    texte = texte.lstrip("﻿")
    try:
        dialecte = csv.Sniffer().sniff(texte[:2000], delimiters=";,\t")
    except csv.Error:
        dialecte = csv.excel
        dialecte.delimiter = ";"
    lignes = list(csv.reader(io.StringIO(texte), dialecte))
    lignes = [l for l in lignes if any(cell.strip() for cell in l)]
    if not lignes:
        return [], ["fichier vide"]
    entete = lignes[0]
    colonnes = {champ: _colonne(entete, champ) for champ in ALIAS}
    manquantes = [c for c in ("longueur", "largeur", "hauteur", "masse") if colonnes[c] is None]
    if manquantes:
        return [], [f"colonnes introuvables dans l'en-tête : {', '.join(manquantes)}. "
                    f"Colonnes attendues : {', '.join(COLONNES_CSV)}"]
    colis, avertissements = [], []
    for num, ligne in enumerate(lignes[1:], start=2):
        d = {}
        for champ, idx in colonnes.items():
            if idx is not None and idx < len(ligne):
                d[champ] = ligne[idx].strip()
        if not d.get("numero"):
            d["numero"] = str(num - 1)
        try:
            c = Colis.depuis_dict(d)
        except (TypeError, ValueError) as e:
            avertissements.append(f"ligne {num} ignorée : {e}")
            continue
        erreurs = c.valider()
        if erreurs:
            avertissements.append(f"ligne {num} ignorée : {', '.join(erreurs)}")
            continue
        colis.append(c)
    return colis, avertissements


def ecrire_csv(colis: list[Colis]) -> str:
    sortie = io.StringIO()
    w = csv.writer(sortie, delimiter=";", lineterminator="\n")
    w.writerow(COLONNES_CSV)
    for c in colis:
        w.writerow([c.numero, c.longueur, c.largeur, c.hauteur, c.masse, c.classe, "oui" if c.fragile else "non",
                    c.emballage, c.reference, c.commentaire])
    return sortie.getvalue()


def plan_csv(resultat: Resultat) -> str:
    """Plan de chargement : une ligne par colis placé, avec sa palette et sa position."""
    sortie = io.StringIO()
    w = csv.writer(sortie, delimiter=";", lineterminator="\n")
    w.writerow(["palette", "type_palette", "zone", "numero_colis", "classe", "masse_kg", "x_cm", "y_cm", "z_cm",
                "dim_x_cm", "dim_y_cm", "dim_z_cm", "fragile", "emballage", "reference", "commentaire"])
    for pal in resultat.palettes:
        for p in sorted(pal.placements, key=lambda p: (p.z, p.y, p.x)):
            c = p.colis
            w.writerow([pal.numero, pal.type.code, pal.zone, c.numero, c.classe, c.masse,
                        round(p.x, 1), round(p.y, 1), round(p.z, 1), p.dx, p.dy, p.dz,
                        "oui" if c.fragile else "non", c.emballage, c.reference, c.commentaire])
    for c, motif in resultat.non_places:
        w.writerow(["", "", "NON PLACÉ", c.numero, c.classe, c.masse, "", "", "", c.longueur, c.largeur, c.hauteur,
                    "oui" if c.fragile else "non", c.emballage, c.reference, motif])
    return sortie.getvalue()


def plan_json(resultat_dict: dict) -> str:
    return json.dumps({"date": datetime.now().isoformat(timespec="seconds"), **resultat_dict},
                      ensure_ascii=False, indent=2)
