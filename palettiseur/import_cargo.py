"""Import d'un manifeste de fret Excel (.xlsx) au format « CARGO ».

Colonnes lues (par leur en-tête, dans l'ordre que l'on veut) : ``Class/Div``, ``Type of Packing``,
``Proper Shipping Name``, ``Weight (Kg)``, ``Length (cm)``, ``Width (cm)``, ``Height (cm)``, ``UN/ID No``,
``Sub Haz``, ``Quantity``, ``Cargo Nat Ref 1`` (DAT) et la **dernière colonne** (numéro de série). Les autres
colonnes, dont la température mentionnée dans les désignations, sont ignorées.

Pour chaque ligne :

- **numéro du colis** : les 4 derniers chiffres du numéro de série (dernière colonne) ; le numéro complet est
  conservé dans le champ « référence » ;
- **DAT** : la chaîne complète de ``Cargo Nat Ref 1``, sans troncature : les colis d'un même DAT voyagent ensemble
  (regroupement par DAT du notebook CDP_5) ;
- **classe** : la classe IATA de ``Class/Div`` ; à défaut, ``ARME`` (catégorie A du notebook, ``WEAPONS``) ou ``SECU``
  (catégorie B, ``WEAPONRY SECURITY PARTS``) reconnus dans la désignation (règles de ``data/import_cargo.json``) ;
- **emballage** : la matière déduite de ``Type of Packing`` (carton, bois…), pour ne pas écraser les cartons ;
- **fragile** : jamais déduit du fichier (à cocher ensuite dans la liste) ;
- **sens** : la hauteur reste toujours verticale (le manifeste n'indique pas de notion de renversement).
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .emballages import emballages
from .iata import PAR_CODE
from .lecteur_xlsx import ErreurXlsx, lire_classeur
from .ressources import chemin_donnees

# En-têtes reconnus (normalisés : minuscules, sans accents, ponctuation -> espace).
EN_TETES = {
    "classe": ["class div", "class", "classe", "classe iata", "iata"],
    "emballage": ["type of packing", "emballage", "packing", "type d emballage"],
    "designation": ["proper shipping name", "designation", "désignation"],
    "masse": ["weight kg", "poids kg", "masse kg", "weight", "poids", "masse"],
    "longueur": ["length cm", "longueur cm", "length", "longueur"],
    "largeur": ["width cm", "largeur cm", "width", "largeur"],
    "hauteur": ["height cm", "hauteur cm", "height", "hauteur"],
    "onu": ["un id no", "un id", "n onu", "numero onu", "onu"],
    "sous_risque": ["sub haz", "risque subsidiaire"],
    "quantite": ["quantity", "quantite"],
    "dat": ["cargo nat ref 1", "dat", "n dat", "numero dat"],
}
OBLIGATOIRES = ("masse", "longueur", "largeur", "hauteur", "designation")
CHIFFRES_SERIE = 4


def _normaliser(texte) -> str:
    texte = unicodedata.normalize("NFD", str(texte if texte is not None else "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def _nombre(valeur) -> float | None:
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    texte = str(valeur).strip().replace(",", ".").replace(" ", "").replace(" ", "")
    try:
        return float(texte)
    except ValueError:
        return None


def _texte(valeur) -> str:
    if valeur is None:
        return ""
    if isinstance(valeur, float) and valeur.is_integer():
        valeur = int(valeur)
    return str(valeur).strip()


def _code_classe(valeur) -> str:
    """Classe IATA lue dans la cellule : 9, '9', 3.0, '2.2', '1.4S' ; vide ou « N/A » : pas de matière dangereuse."""
    brut = _texte(valeur).replace(",", ".")
    return "" if brut.upper() in ("", "0", "N/A", "NA", "NONE", "-") else brut.upper()


class Regles:
    """Règles de l'import chargées depuis ``data/import_cargo.json``."""

    def __init__(self, chemin: Path | None = None) -> None:
        with open(chemin or chemin_donnees("import_cargo.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.masse_minimale = float(data.get("masse_minimale", 0.1))
        self.regles_classe = [(r["classe"], r.get("libelle", r["classe"]), [_normaliser(k).upper() for k in r["mots_cles"]])
                              for r in data["regles_classe"]]

    def classe_depuis_designation(self, designation: str) -> str:
        """Première règle dont un mot-clé figure (comme mots entiers) dans la désignation, sinon « »."""
        mots = f" {_normaliser(designation).upper()} "
        for classe, _, cles in self.regles_classe:
            if any(f" {k} " in mots for k in cles if k):
                return classe
        return ""


_REGLES: Regles | None = None


def regles() -> Regles:
    global _REGLES
    if _REGLES is None:
        _REGLES = Regles()
    return _REGLES


def _trouver_en_tetes(lignes: list[list]) -> tuple[int, dict[str, int]] | None:
    """Première ligne (parmi les 15 premières) qui contient toutes les colonnes obligatoires."""
    for numero, ligne in enumerate(lignes[:15]):
        normalises = [_normaliser(c) for c in ligne]
        colonnes = {}
        for champ, alias in EN_TETES.items():
            for a in alias:
                a = _normaliser(a)
                if a in normalises:
                    colonnes[champ] = normalises.index(a)
                    break
        if all(c in colonnes for c in OBLIGATOIRES):
            return numero, colonnes
    return None


def lire_manifeste(contenu: bytes) -> dict:
    """Lit un manifeste .xlsx. Retourne ``{"colis": [...], "avertissements": [...], "resume": {...}}``.

    Lève ``ErreurXlsx`` si le fichier n'est pas un classeur lisible ou s'il manque des colonnes obligatoires.
    """
    classeur = lire_classeur(contenu)
    trouve = None
    for nom, lignes in classeur.items():
        resultat = _trouver_en_tetes(lignes)
        if resultat:
            trouve = (nom, lignes, *resultat)
            break
    if trouve is None:
        premiere = next(iter(classeur.values()), [])
        presents = {_normaliser(c) for ligne in premiere[:15] for c in ligne}
        manquantes = [c for c in OBLIGATOIRES if not any(_normaliser(a) in presents for a in EN_TETES[c])]
        raise ErreurXlsx("colonnes introuvables dans l'en-tête : " + ", ".join(manquantes or OBLIGATOIRES)
                         + ". Colonnes attendues : Proper Shipping Name, Weight (Kg), Length (cm), Width (cm), Height (cm)")
    nom_feuille, lignes, ligne_en_tete, col = trouve
    en_tete = lignes[ligne_en_tete]
    col_serie = max((i for i, c in enumerate(en_tete) if _texte(c)), default=len(en_tete) - 1)
    nom_col_serie = _texte(en_tete[col_serie])

    regles_import, emb = regles(), emballages()
    colis: list[dict] = []
    avertissements: list[str] = []
    vus: dict[str, int] = {}

    for numero_ligne, ligne in enumerate(lignes[ligne_en_tete + 1:], start=ligne_en_tete + 2):
        ligne = list(ligne) + [None] * (len(en_tete) - len(ligne))
        if not any(_texte(ligne[col[c]]) for c in OBLIGATOIRES) and not _texte(ligne[col_serie]):
            continue                                                    # ligne vide
        dims = [_nombre(ligne[col[c]]) for c in ("longueur", "largeur", "hauteur")]
        if any(d is None or d <= 0 for d in dims):
            avertissements.append(f"ligne {numero_ligne} ignorée : dimensions manquantes ou nulles "
                                  f"({_texte(ligne[col['designation']]) or 'sans désignation'})")
            continue
        masse = _nombre(ligne[col["masse"]])
        serie_brute = ligne[col_serie]
        serie = _texte(serie_brute)
        chiffres = re.sub(r"\D", "", serie)
        if isinstance(serie_brute, (int, float)) and abs(serie_brute) >= 1e15:
            avertissements.append(f"ligne {numero_ligne} : le numéro de série est stocké comme un nombre, ses derniers "
                                  "chiffres peuvent être altérés ; enregistrez la colonne au format texte")
        if chiffres:
            numero = chiffres[-CHIFFRES_SERIE:]
        else:
            numero = f"L{numero_ligne}"
            avertissements.append(f"ligne {numero_ligne} : numéro de série absent, numéro attribué : {numero}")
        if numero in vus:
            vus[numero] += 1
            nouveau = f"{numero}-{vus[numero]}"
            avertissements.append(f"ligne {numero_ligne} : numéro {numero} déjà utilisé, ce colis devient {nouveau}")
            numero = nouveau
        else:
            vus[numero] = 1
        if masse is None or masse <= 0:
            avertissements.append(f"colis {numero} (ligne {numero_ligne}) : masse nulle ou absente, remplacée par "
                                  f"{regles_import.masse_minimale:g} kg ; corrigez-la dans la liste si besoin")
            masse = regles_import.masse_minimale

        designation = _texte(ligne[col["designation"]])
        classe = _code_classe(ligne[col["classe"]]) if "classe" in col else ""
        if classe and classe not in PAR_CODE:
            avertissements.append(f"colis {numero} (ligne {numero_ligne}) : classe « {classe} » inconnue, traitée comme "
                                  "une matière dangereuse sans règle de séparation")
        if not classe:
            classe = regles_import.classe_depuis_designation(designation)
        emballage_brut = _texte(ligne[col["emballage"]]) if "emballage" in col else ""
        emballage = emb.code_depuis_texte(emballage_brut)

        quantite = 1
        if "quantite" in col:
            q = _nombre(ligne[col["quantite"]])
            if q is not None and q >= 2 and float(q).is_integer():
                quantite = int(q)
                avertissements.append(f"colis {numero} (ligne {numero_ligne}) : quantité {quantite}, masse et dimensions "
                                      "supposées par colis")

        morceaux = []
        onu = _texte(ligne[col["onu"]]) if "onu" in col else ""
        if onu:
            morceaux.append(f"UN{onu}")
        if designation:
            morceaux.append(designation)
        sous_risque = _texte(ligne[col["sous_risque"]]) if "sous_risque" in col else ""
        if sous_risque:
            morceaux.append(f"risque subsidiaire {sous_risque}")
        if emballage_brut:
            morceaux.append(emballage_brut)
        dat = _texte(ligne[col["dat"]]) if "dat" in col else ""
        colis.append({
            "numero": numero, "longueur": dims[0], "largeur": dims[1], "hauteur": dims[2], "masse": masse,
            "classe": classe, "fragile": False, "emballage": emballage, "reference": serie, "dat": dat,
            "commentaire": " · ".join(morceaux), "quantite": quantite,
        })

    resume = {
        "feuille": nom_feuille, "colonne_serie": nom_col_serie, "colis": len(colis),
        "armes": sum(1 for c in colis if c["classe"] == "ARME"),
        "pieces_securite": sum(1 for c in colis if c["classe"] == "SECU"),
        "dangereux": sum(1 for c in colis if c["classe"] and c["classe"] not in ("ARME", "SECU")),
        "carton": sum(1 for c in colis if c["emballage"] == "carton"),
        "fibre": sum(1 for c in colis if c["emballage"] == "fibre"),
        "dat": len({c["dat"] for c in colis if c["dat"]}),
        "lignes_ignorees": sum(1 for a in avertissements if "ignorée" in a),
    }
    return {"colis": colis, "avertissements": avertissements, "resume": resume}


def lire_fichier(chemin: str | Path) -> dict:
    return lire_manifeste(Path(chemin).read_bytes())
