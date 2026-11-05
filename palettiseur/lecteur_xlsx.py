"""Lecteur minimal de classeurs Excel (.xlsx), sans dépendance extérieure.

Un fichier .xlsx est une archive zip de fichiers XML. On y lit la liste des feuilles, les chaînes partagées et les
cellules de chaque feuille. Seules les valeurs sont lues (texte, nombres, booléens) : pas de formules évaluées, de
mises en forme ni de dates. Cela suffit pour un manifeste de fret au format fixe.

Le contenu vient d'un fichier que l'utilisateur choisit : on borne donc la taille décompressée et on refuse toute
déclaration DTD/entité dans le XML (un classeur Excel n'en contient jamais).
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
TAILLE_MAX_ARCHIVE = 25 * 1024 * 1024          # octets, fichier .xlsx
TAILLE_MAX_PARTIE = 80 * 1024 * 1024           # octets, une fois décompressée
LIGNES_MAX = 20000


class ErreurXlsx(ValueError):
    """Le fichier n'est pas un classeur .xlsx lisible."""


def _q(ns: str, nom: str) -> str:
    return f"{{{ns}}}{nom}"


def _xml(archive: zipfile.ZipFile, chemin: str) -> ET.Element:
    try:
        info = archive.getinfo(chemin)
    except KeyError:
        raise ErreurXlsx(f"partie « {chemin} » absente : ce n'est pas un classeur .xlsx valide") from None
    if info.file_size > TAILLE_MAX_PARTIE:
        raise ErreurXlsx("fichier trop volumineux une fois décompressé")
    donnees = archive.read(chemin)
    if b"<!DOCTYPE" in donnees or b"<!ENTITY" in donnees:
        raise ErreurXlsx("contenu XML non autorisé dans un classeur")
    try:
        return ET.fromstring(donnees)
    except ET.ParseError as e:
        raise ErreurXlsx(f"XML illisible dans « {chemin} » : {e}") from None


def _indice_colonne(reference: str) -> int:
    """« B7 » -> 1 ; « AA3 » -> 26 (indices à partir de 0)."""
    lettres = re.match(r"[A-Za-z]+", reference)
    if not lettres:
        raise ErreurXlsx(f"référence de cellule invalide : {reference!r}")
    n = 0
    for c in lettres.group().upper():
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _texte_si(si: ET.Element) -> str:
    """Texte d'une chaîne partagée : simple (<t>) ou en morceaux (<r><t>), sans les annotations phonétiques."""
    morceaux = [t.text or "" for t in si.findall(_q(NS_MAIN, "t"))]
    for r in si.findall(_q(NS_MAIN, "r")):
        morceaux.extend(t.text or "" for t in r.findall(_q(NS_MAIN, "t")))
    return "".join(morceaux)


def _chaines_partagees(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    racine = _xml(archive, "xl/sharedStrings.xml")
    return [_texte_si(si) for si in racine.findall(_q(NS_MAIN, "si"))]


def _nombre(texte: str):
    """Entier si le texte est un entier, sinon flottant."""
    try:
        return int(texte)
    except ValueError:
        valeur = float(texte)
        return int(valeur) if valeur.is_integer() and abs(valeur) < 1e15 else valeur


def _valeur(cellule: ET.Element, chaines: list[str]):
    type_ = cellule.get("t", "n")
    if type_ == "inlineStr":
        est = cellule.find(_q(NS_MAIN, "is"))
        return _texte_si(est) if est is not None else ""
    v = cellule.find(_q(NS_MAIN, "v"))
    if v is None or v.text is None:
        return None
    texte = v.text
    if type_ == "s":
        indice = int(texte)
        if not 0 <= indice < len(chaines):
            raise ErreurXlsx(f"chaîne partagée {indice} introuvable")
        return chaines[indice]
    if type_ in ("str", "e"):
        return texte
    if type_ == "b":
        return texte.strip() == "1"
    try:
        return _nombre(texte)
    except ValueError:
        return texte


def _feuilles(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(nom, chemin de la partie) de chaque feuille, dans l'ordre du classeur."""
    classeur = _xml(archive, "xl/workbook.xml")
    relations = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        for rel in _xml(archive, "xl/_rels/workbook.xml.rels").findall(_q(NS_PKG_REL, "Relationship")):
            cible = rel.get("Target", "")
            relations[rel.get("Id")] = cible.lstrip("/") if cible.startswith("/") else "xl/" + cible
    feuilles = []
    conteneur = classeur.find(_q(NS_MAIN, "sheets"))
    if conteneur is None:
        raise ErreurXlsx("aucune feuille dans ce classeur")
    for numero, feuille in enumerate(conteneur.findall(_q(NS_MAIN, "sheet")), start=1):
        chemin = relations.get(feuille.get(_q(NS_REL, "id")), f"xl/worksheets/sheet{numero}.xml")
        feuilles.append((feuille.get("name", f"Feuille{numero}"), chemin))
    return feuilles


def _lignes(archive: zipfile.ZipFile, chemin: str, chaines: list[str]) -> list[list]:
    racine = _xml(archive, chemin)
    donnees = racine.find(_q(NS_MAIN, "sheetData"))
    lignes: dict[int, dict[int, object]] = {}
    if donnees is not None:
        for position, ligne in enumerate(donnees.findall(_q(NS_MAIN, "row")), start=1):
            numero = int(ligne.get("r", position))
            if numero > LIGNES_MAX:
                raise ErreurXlsx(f"plus de {LIGNES_MAX} lignes : fichier non pris en charge")
            cellules = lignes.setdefault(numero, {})
            for suivante, cellule in enumerate(ligne.findall(_q(NS_MAIN, "c"))):
                ref = cellule.get("r")
                colonne = _indice_colonne(ref) if ref else (max(cellules) + 1 if cellules else suivante)
                cellules[colonne] = _valeur(cellule, chaines)
    if not lignes:
        return []
    largeur = max((max(c) + 1 for c in lignes.values() if c), default=0)
    return [[lignes.get(n, {}).get(k) for k in range(largeur)] for n in range(1, max(lignes) + 1)]


def lire_classeur(contenu: bytes) -> dict[str, list[list]]:
    """Toutes les feuilles du classeur : ``{nom: lignes}``, chaque ligne étant une liste de valeurs (None si vide)."""
    if len(contenu) > TAILLE_MAX_ARCHIVE:
        raise ErreurXlsx("fichier trop volumineux")
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
    except zipfile.BadZipFile:
        raise ErreurXlsx("ce fichier n'est pas un classeur Excel .xlsx (enregistrez-le au format .xlsx)") from None
    with archive:
        chaines = _chaines_partagees(archive)
        return {nom: _lignes(archive, chemin, chaines) for nom, chemin in _feuilles(archive)}
