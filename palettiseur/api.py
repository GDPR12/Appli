"""Fonctions de haut niveau appelées par le serveur (ou par un script)."""

from __future__ import annotations

from . import avion, fichiers
from .coloriage import COULEURS_CARTE, coloriage_carte, heat_map
from .iata import description_classes, regles
from .modeles import Colis, Parametres, Resultat
from .optimisation import optimiser
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
    }


def moteurs_disponibles() -> list[dict]:
    liste = [{"code": "heuristique", "libelle": "Heuristique multi-départ (rapide)", "disponible": True}]
    try:
        from . import moteur_scip  # noqa: F401
        dispo, detail = moteur_scip.disponible()
    except Exception as e:  # pragma: no cover - dépend de l'installation
        dispo, detail = False, str(e)
    liste.append({"code": "scip", "libelle": "Programme linéaire en nombres entiers SCIP (exact, lent)",
                  "disponible": dispo, "detail": detail})
    return liste


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


def resultat_vers_dict(resultat: Resultat) -> dict:
    params = resultat.parametres
    messages = verifier(resultat)
    palettes = []
    for pal in resultat.palettes:
        d = pal.vers_dict(params.tare_palette)
        d["carte_tous"] = coloriage_carte(pal, params.jeu_contact, False)
        d["carte_speciaux"] = coloriage_carte(pal, params.jeu_contact, True)
        palettes.append(d)
    return {
        "statut": resultat.statut,
        "moteur": resultat.moteur,
        "duree": round(resultat.duree, 2),
        "iterations": resultat.iterations,
        "parametres": params.vers_dict(),
        "palettes": palettes,
        "non_places": [{"colis": c.vers_dict(), "motif": m} for c, m in resultat.non_places],
        "verification": [m.vers_dict() for m in messages],
        "bilan": avion.bilan(resultat),
        "heat_map": heat_map(resultat.palettes),
        "couleurs_carte": COULEURS_CARTE,
    }


def lancer_optimisation(liste_colis: list[dict], parametres: dict | None) -> dict:
    params = Parametres.depuis_dict(parametres)
    colis = colis_depuis_liste(liste_colis)
    if params.moteur == "scip":
        from . import moteur_scip
        resultat = moteur_scip.optimiser(colis, params)
    else:
        resultat = optimiser(colis, params)
    return resultat_vers_dict(resultat)


def importer_texte(texte: str) -> dict:
    """Importe une liste de colis depuis un CSV ou un JSON (projet enregistré)."""
    texte_strip = texte.strip()
    if texte_strip.startswith("{") or texte_strip.startswith("["):
        import json
        data = json.loads(texte_strip)
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
    colis, avert = fichiers.lire_csv(texte)
    return {"colis": [c.vers_dict() for c in colis], "avertissements": avert, "parametres": None}


def exemple() -> list[dict]:
    """Jeu de colis d'exemple (inspiré du notebook CDP et des cas du projet)."""
    base = [
        dict(numero="C-001", longueur=80, largeur=60, hauteur=50, masse=120, renversable=True, classe="", commentaire="Caisse outillage"),
        dict(numero="C-002", longueur=70, largeur=50, hauteur=40, masse=90, renversable=True, classe="", commentaire=""),
        dict(numero="C-003", longueur=120, largeur=80, hauteur=60, masse=250, renversable=False, classe="", commentaire="Groupe électrogène (sens obligatoire)"),
        dict(numero="C-004", longueur=50, largeur=40, hauteur=30, masse=60, renversable=True, classe="", commentaire=""),
        dict(numero="C-005", longueur=100, largeur=70, hauteur=50, masse=180, renversable=False, classe="", commentaire=""),
        dict(numero="C-006", longueur=60, largeur=50, hauteur=40, masse=75, renversable=True, classe="", commentaire=""),
        dict(numero="C-007", longueur=130, largeur=90, hauteur=70, masse=300, renversable=False, classe="", commentaire="Caisse pièces moteur"),
        dict(numero="C-008", longueur=40, largeur=35, hauteur=30, masse=45, renversable=True, classe="", commentaire=""),
        dict(numero="C-009", longueur=60, largeur=40, hauteur=40, masse=55, renversable=True, classe="", commentaire="", quantite=6),
        dict(numero="C-010", longueur=110, largeur=45, hauteur=45, masse=35, renversable=True, classe="", commentaire="", quantite=4),
        dict(numero="C-011", longueur=80, largeur=60, hauteur=45, masse=140, renversable=True, classe="", commentaire="", quantite=3),
        dict(numero="JER-01", longueur=47, largeur=35, hauteur=35, masse=22, renversable=False, classe="3", commentaire="Jerricans carburant", quantite=6),
        dict(numero="MUN-01", longueur=60, largeur=40, hauteur=35, masse=55, renversable=False, classe="1.4S", commentaire="Munitions 5,56 (1.4S)", quantite=4),
        dict(numero="EXP-01", longueur=90, largeur=50, hauteur=40, masse=80, renversable=False, classe="1", commentaire="Explosifs de démolition"),
        dict(numero="OXY-01", longueur=30, largeur=30, hauteur=140, masse=70, renversable=False, classe="2.2", commentaire="Bouteilles d'oxygène"),
        dict(numero="BAT-01", longueur=60, largeur=40, hauteur=30, masse=48, renversable=False, classe="9L", commentaire="Batteries lithium", quantite=2),
        dict(numero="ACD-01", longueur=50, largeur=50, hauteur=60, masse=90, renversable=False, classe="8", commentaire="Acide (corrosif)"),
        dict(numero="ARM-01", longueur=120, largeur=40, hauteur=30, masse=60, renversable=True, classe="ARME", commentaire="Caisse d'armes"),
        dict(numero="ARM-02", longueur=40, largeur=30, hauteur=20, masse=12, renversable=True, classe="SECU", commentaire="Culasses (pièces de sécurité)"),
        dict(numero="MED-01", longueur=60, largeur=40, hauteur=40, masse=18, renversable=False, classe="", commentaire="Matériel médical", fragile=True, quantite=2),
        dict(numero="LONG-01", longueur=310, largeur=60, hauteur=50, masse=420, renversable=False, classe="", commentaire="Mât d'antenne (BIPAL)"),
    ]
    for d in base:
        d.setdefault("quantite", 1)
        d.setdefault("fragile", False)
    return base
