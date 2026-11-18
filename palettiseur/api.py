"""Fonctions de haut niveau appelées par le serveur (ou par un script)."""

from __future__ import annotations

import base64
import csv
import binascii
import json
import threading
import time
import traceback
import uuid

from . import avion, fichiers, import_cargo
from .emballages import emballages
from .coloriage import COULEURS_CARTE, coloriage_carte, heat_map
from .geometrie import cotes_bord, cotes_visibles
from .lecteur_xlsx import ErreurXlsx
from .iata import classe as classe_de, description_classes, regles
from .indicateurs import indicateurs_etat, indicateurs_palette
from .modeles import Colis, Parametres, Resultat
from .optimisation import optimiser
from .ressources import chemin_donnees
from .verification import verifier


def configuration() -> dict:
    """Données statiques pour l'interface : classes, règles, paramètres par défaut."""
    r = regles()
    return {
        "classes": description_classes(),
        "incompatibilites": r.matrice(),
        "parametres": Parametres().vers_dict(),
        "versions_avion": [{"passagers_max": p, "palettes": n} for p, n in avion.VERSIONS],
        "couleurs_carte": COULEURS_CARTE,
        "moteurs": moteurs_disponibles(),
        "exemples": EXEMPLES,
        "emballages": emballages().description(),
    }


def moteurs_disponibles() -> list[dict]:
    return [
        {"code": "notebook", "libelle": "Code du notebook CDP_5 (heuristique + VND)", "disponible": True,
         "detail": "Cinq essais, VND, recentrage et tassement horizontal : le code du notebook, avec les règles de "
                   "l'application."},
        {"code": "heuristique", "libelle": "Moteur rapide de l'application", "disponible": True,
         "detail": "Réimplémentation rapide (quelques secondes) ; sert aussi de relais pour les BIPAL / TRIPAL."},
    ]


EXEMPLES = [
    {"cle": "cargo", "titre": "CARGO – manifeste Excel (54 colis)",
     "description": "Le manifeste CARGO.xlsx importé : numéros de série, 14 armes, 10 pièces de sécurité, 5 matières "
                    "dangereuses (classes 9, 3 et 2.2)."},
    {"cle": "cdp", "titre": "CDP – 54 colis du notebook",
     "description": "Les 54 colis de la cellule 2 du notebook (numéros à 4 chiffres, DAT, catégories A et B) avec ses "
                    "paramètres : 3 palettes après l'heuristique, 2 après la VND."},
    {"cle": "iata", "titre": "Mixte – matières dangereuses, armement et BIPAL",
     "description": "Liste variée : classes IATA incompatibles, armement, fragiles et un colis de 3 m."},
]


def colis_depuis_liste(liste: list[dict]) -> list[Colis]:
    """Convertit la liste JSON de l'interface en colis, en développant les quantités."""
    colis: list[Colis] = []
    for d in liste:
        quantite = int(float(d.get("quantite", 1) or 1))
        base = Colis.depuis_dict(d)
        if quantite <= 1:
            colis.append(base)
        else:
            for k in range(1, quantite + 1):
                copie = Colis.depuis_dict(d)
                copie.numero = f"{base.numero}-{k}"
                colis.append(copie)
    return colis


def _palettes_dict(palettes, params: Parametres) -> list[dict]:
    res = []
    for pal in palettes:
        if pal.zone == "rampe":
            largeur, longueur = params.largeur_rampe, params.longueur_rampe
        else:
            largeur, longueur = params.largeur_palette, params.longueur_palette * pal.type.positions
        d = pal.vers_dict(params.tare_palette, largeur, longueur)
        for placement, dict_placement in zip(pal.placements, d["placements"]):
            if classe_de(placement.colis.classe).visible:
                # côtés d'où l'étiquette est visible, et bord contre lequel le colis est posé (sur l'axe des côtés)
                axe = pal.type.axe_cotes
                dict_placement["acces_iata"] = list(cotes_visibles(placement, pal.placements, axe))
                dict_placement["bord_iata"] = list(cotes_bord(placement, pal.type.etendue_cotes, params.jeu_contact, axe))
        d["indicateurs"] = indicateurs_palette(pal)
        d["carte_tous"] = coloriage_carte(pal, params.jeu_contact, False)
        d["carte_speciaux"] = coloriage_carte(pal, params.jeu_contact, True)
        res.append(d)
    return res


def _etat_dict(palettes, resultat: Resultat) -> dict:
    """Palettes, vérification et bilan d'un état du pipeline (mêmes règles que la solution finale)."""
    etat = Resultat(palettes, resultat.non_places, resultat.parametres)
    return {"palettes": _palettes_dict(palettes, resultat.parametres),
            "verification": [m.vers_dict() for m in verifier(etat)], "bilan": avion.bilan(etat),
            "indicateurs": indicateurs_etat(palettes)}


def resultat_vers_dict(resultat: Resultat) -> dict:
    params = resultat.parametres
    finale = _etat_dict(resultat.palettes, resultat)
    etapes = []
    for e in resultat.etapes:
        item = {"cle": e.cle, "titre": e.titre, "description": e.description,
                "nombre_palettes": len(e.palettes), "positions": sum(p.type.positions for p in e.palettes),
                "somme_hauteurs": round(sum(p.hauteur for p in e.palettes), 1)}
        if e.cle != "finale":
            item.update(_etat_dict(e.palettes, resultat))
        else:
            item["indicateurs"] = finale["indicateurs"]
        etapes.append(item)
    return {
        "statut": resultat.statut,
        "moteur": resultat.moteur,
        "duree": round(resultat.duree, 2),
        "iterations": resultat.iterations,
        "parametres": params.vers_dict(),
        "palettes": finale["palettes"],
        "non_places": [{"colis": c.vers_dict(), "motif": m} for c, m in resultat.non_places],
        "verification": finale["verification"],
        "bilan": finale["bilan"],
        "indicateurs": finale["indicateurs"],
        "heat_map": heat_map(resultat.palettes),
        "couleurs_carte": COULEURS_CARTE,
        "etapes": etapes,
        "journal": resultat.journal,
    }


def lancer_optimisation(liste_colis: list[dict], parametres: dict | None, progression=None) -> dict:
    params = Parametres.depuis_dict(parametres)
    colis = colis_depuis_liste(liste_colis)
    return resultat_vers_dict(optimiser(colis, params, progression=progression))


# ---------------------------------------------------------------------------
# Calcul en arrière-plan : le calcul peut durer plusieurs minutes (budget de 180 s par défaut), on le lance dans un
# fil et l'interface interroge son avancement.
# ---------------------------------------------------------------------------

LIBELLES_PHASES = {
    "preparation": "Préparation des colis",
    "heuristique": "Phase 1 – placement heuristique",
    "vnd": "Phase 2 – VND (transferts, replacements, échanges)",
    "recentrage": "Phase 3 – recentrage",
    "tassement_horizontal": "Phase 4 – tassement horizontal",
    "resultat": "Mise en forme du résultat",
}
_TRAVAUX: dict[str, dict] = {}
_VERROU_TRAVAUX = threading.Lock()


def demarrer_optimisation(liste_colis: list[dict], parametres: dict | None) -> dict:
    """Lance le calcul dans un fil et retourne ``{"id", "budget"}`` ; ``etat_optimisation`` donne l'avancement."""
    params = Parametres.depuis_dict(parametres)
    with _VERROU_TRAVAUX:
        if any(t["etat"] == "en_cours" for t in _TRAVAUX.values()):
            raise ValueError("un calcul est déjà en cours : attendez sa fin")
        for ancien in sorted(_TRAVAUX, key=lambda k: _TRAVAUX[k]["debut"])[:-2]:
            del _TRAVAUX[ancien]                       # on ne garde que les derniers calculs
        identifiant = uuid.uuid4().hex[:12]
        maintenant = time.time()
        travail = {"id": identifiant, "etat": "en_cours", "phase": "preparation", "debut": maintenant,
                   "debut_phase": maintenant, "budget_phase": None, "budget": params.temps_limite}
        _TRAVAUX[identifiant] = travail

    def progression(phase: str, budget: float | None = None) -> None:
        travail.update(phase=phase, debut_phase=time.time(), budget_phase=budget)

    def executer() -> None:
        try:
            resultat = lancer_optimisation(liste_colis, parametres, progression)
            travail["resultat"] = resultat
            travail["etat"] = "termine"
        except Exception as erreur:  # noqa: BLE001 - l'erreur est rendue à l'interface
            traceback.print_exc()
            travail["erreur"] = f"{type(erreur).__name__} : {erreur}"
            travail["etat"] = "erreur"
        finally:
            travail["fin"] = time.time()

    threading.Thread(target=executer, name=f"calcul-{identifiant}", daemon=True).start()
    return {"id": identifiant, "budget": params.temps_limite}


def etat_optimisation(identifiant: str) -> dict:
    """Avancement d'un calcul : ``etat`` (``en_cours``, ``termine`` ou ``erreur``), phase en cours, temps écoulé et, une
    fois terminé, le ``resultat`` ou l'``erreur``."""
    travail = _TRAVAUX.get(identifiant)
    if travail is None:
        raise ValueError("calcul inconnu (le serveur a peut-être été relancé)")
    maintenant = travail.get("fin") or time.time()
    reponse = {"id": identifiant, "etat": travail["etat"], "phase": travail["phase"],
               "libelle": LIBELLES_PHASES.get(travail["phase"], travail["phase"]),
               "ecoule": round(maintenant - travail["debut"], 1),
               "ecoule_phase": round(maintenant - travail["debut_phase"], 1),
               "budget_phase": travail["budget_phase"], "budget": travail["budget"]}
    if travail["etat"] == "termine":
        reponse["resultat"] = travail["resultat"]
    elif travail["etat"] == "erreur":
        reponse["erreur"] = travail["erreur"]
    return reponse


def importer_texte(texte: str) -> dict:
    """Importe une liste de colis depuis un CSV ou un JSON (projet enregistré)."""
    texte_strip = texte.strip()
    if texte_strip.startswith("PK") or "\x00" in texte[:2000]:
        raise ValueError("ce fichier est binaire (classeur Excel ?) : enregistrez-le au format .xlsx "
                         "et utilisez « Importer… », qui lit les .xlsx, les .csv et les projets .json")
    if texte_strip.startswith("{") or texte_strip.startswith("["):
        import json
        try:
            data = json.loads(texte_strip)
        except ValueError:
            raise ValueError("fichier JSON invalide : ce n'est pas un projet enregistré par l'application") from None
        if isinstance(data, dict):
            liste = data.get("colis", [])
            parametres = data.get("parametres")
        else:
            liste, parametres = data, None
        colis = [Colis.depuis_dict(d) for d in liste]
        avert = []
        valides = []
        for c in colis:
            e = c.valider()
            if e:
                avert.append(f"colis {c.numero!r} ignoré : {', '.join(e)}")
            else:
                valides.append(c)
        return {"colis": [c.vers_dict() for c in valides], "avertissements": avert, "parametres": parametres}
    try:
        colis, avert = fichiers.lire_csv(texte)
    except csv.Error:
        raise ValueError("fichier illisible : ce n'est ni un CSV ni un projet JSON valide") from None
    return {"colis": [c.vers_dict() for c in colis], "avertissements": avert, "parametres": None}


def importer_cargo(nom: str, contenu_base64: str) -> dict:
    """Importe un manifeste Excel (.xlsx) envoyé par l'interface (contenu encodé en base64)."""
    if not str(nom).lower().endswith((".xlsx", ".xlsm")):
        raise ValueError("format non pris en charge : choisissez un fichier Excel .xlsx "
                         "(Fichier > Enregistrer sous > Classeur Excel)")
    try:
        contenu = base64.b64decode(contenu_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("fichier illisible (encodage invalide)") from None
    try:
        resultat = import_cargo.lire_manifeste(contenu)
    except ErreurXlsx as e:
        raise ValueError(str(e)) from None
    resultat["parametres"] = None
    return resultat


def exemple(nom: str = "iata") -> dict:
    """Jeu de colis d'exemple : ``{"titre", "description", "colis", "parametres", "avertissements"}``."""
    if nom == "cargo":
        lu = import_cargo.lire_fichier(chemin_donnees("exemple_cargo.xlsx"))
        return {"titre": EXEMPLES[0]["titre"], "description": EXEMPLES[0]["description"], "colis": lu["colis"],
                "parametres": None, "avertissements": lu["avertissements"], "resume": lu["resume"]}
    if nom == "cdp":
        with open(chemin_donnees("exemple_cdp.json"), encoding="utf-8") as f:
            data = json.load(f)
        return {"titre": EXEMPLES[1]["titre"], "description": EXEMPLES[1]["description"], "colis": data["colis"],
                "parametres": data.get("parametres"), "avertissements": []}
    return {"titre": EXEMPLES[2]["titre"], "description": EXEMPLES[2]["description"],
            "colis": _exemple_iata(), "parametres": None, "avertissements": []}


def _exemple_iata() -> list[dict]:
    """Liste variée (21 lignes) : colis ordinaires, matières dangereuses, armement, fragiles, un colis de 3 m."""
    base = [
        dict(numero="C-001", longueur=80, largeur=60, hauteur=50, masse=120, emballage="bois", commentaire="Caisse outillage"),
        dict(numero="C-002", longueur=70, largeur=50, hauteur=40, masse=90, emballage="bois"),
        dict(numero="C-003", longueur=120, largeur=80, hauteur=60, masse=250, emballage="metal", commentaire="Groupe électrogène"),
        dict(numero="C-004", longueur=50, largeur=40, hauteur=30, masse=60, emballage="carton"),
        dict(numero="C-005", longueur=100, largeur=70, hauteur=50, masse=180, emballage="bois"),
        dict(numero="C-006", longueur=60, largeur=50, hauteur=40, masse=75, emballage="carton"),
        dict(numero="C-007", longueur=130, largeur=90, hauteur=70, masse=300, emballage="bois", commentaire="Caisse pièces moteur"),
        dict(numero="C-008", longueur=40, largeur=35, hauteur=30, masse=45, emballage="carton"),
        dict(numero="C-009", longueur=60, largeur=40, hauteur=40, masse=55, emballage="carton", quantite=6),
        dict(numero="C-010", longueur=110, largeur=45, hauteur=45, masse=35, emballage="carton", quantite=4),
        dict(numero="C-011", longueur=80, largeur=60, hauteur=45, masse=140, emballage="bois", quantite=3),
        dict(numero="JER-01", longueur=47, largeur=35, hauteur=35, masse=22, classe="3", emballage="plastique", commentaire="Jerricans carburant", quantite=6),
        dict(numero="MUN-01", longueur=60, largeur=40, hauteur=35, masse=55, classe="1.4S", emballage="metal", commentaire="Munitions 5,56 (1.4S)", quantite=4),
        dict(numero="EXP-01", longueur=90, largeur=50, hauteur=40, masse=80, classe="1", emballage="bois", commentaire="Explosifs de démolition"),
        dict(numero="OXY-01", longueur=30, largeur=30, hauteur=140, masse=70, classe="2.2", emballage="metal", commentaire="Bouteilles d'oxygène"),
        dict(numero="BAT-01", longueur=60, largeur=40, hauteur=30, masse=48, classe="9", emballage="composite", commentaire="Batteries lithium", quantite=2),
        dict(numero="ACD-01", longueur=50, largeur=50, hauteur=60, masse=90, classe="8", emballage="plastique", commentaire="Acide (corrosif)"),
        dict(numero="ARM-01", longueur=120, largeur=40, hauteur=30, masse=60, classe="ARME", emballage="metal", commentaire="Caisse d'armes"),
        dict(numero="ARM-02", longueur=40, largeur=30, hauteur=20, masse=12, classe="SECU", emballage="metal", commentaire="Culasses (pièces de sécurité)"),
        dict(numero="MED-01", longueur=60, largeur=40, hauteur=40, masse=18, emballage="carton", commentaire="Matériel médical", fragile=True, quantite=2),
        dict(numero="LONG-01", longueur=310, largeur=60, hauteur=50, masse=420, emballage="bois", commentaire="Mât d'antenne (BIPAL)"),
    ]
    for d in base:
        d.setdefault("quantite", 1)
        d.setdefault("fragile", False)
        d.setdefault("classe", "")
        d.setdefault("commentaire", "")
    return base
