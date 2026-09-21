"""Contrôle indépendant d'un plan de palettisation.

Ce module ne dépend pas de l'heuristique : il relit chaque palette et
vérifie toutes les règles du projet. Il produit des *erreurs* (règle absolue
violée) et des *avertissements* (règle de préférence non respectée ou point
d'attention pour le mécanicien navigant).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import combinations

from . import avion
from .geometrie import se_chevauchent, supports, taux_support, touche_bord_couloir, orientations
from .iata import ReglesSeparation, classe as classe_de, regles as regles_par_defaut
from .modeles import Palette, Parametres, Resultat


@dataclass
class Message:
    niveau: str            # "erreur" ou "avertissement"
    code: str
    message: str
    palette: int | None = None
    colis: list[str] | None = None

    def vers_dict(self) -> dict:
        return asdict(self)


def verifier_palette(pal: Palette, params: Parametres, regles: ReglesSeparation) -> list[Message]:
    msgs: list[Message] = []
    L, W, H = pal.type.largeur, pal.type.longueur, pal.type.hauteur_max
    jeu = params.jeu_contact
    n = pal.numero
    pls = pal.placements

    if pal.masse > pal.type.masse_max + 1e-6:
        msgs.append(Message("erreur", "masse", f"Palette {n} : masse nette {pal.masse:g} kg > maximum "
                            f"{pal.type.masse_max:g} kg.", n))
    for p in pls:
        c = p.colis
        # Orientation autorisée
        if (p.dx, p.dy, p.dz) not in orientations(c):
            msgs.append(Message("erreur", "orientation", f"Colis {c.numero} : orientation non autorisée "
                                f"(colis à sens obligatoire).", n, [c.numero]))
        # Dans le volume
        if p.x < -jeu or p.y < -jeu or p.z < -jeu or p.x2 > L + jeu or p.y2 > W + jeu or p.z2 > H + jeu:
            msgs.append(Message("erreur", "volume", f"Colis {c.numero} dépasse du volume de la palette {n}.",
                                n, [c.numero]))
        # Support
        ts = taux_support(p, pls, jeu)
        if ts < params.support_minimal - 1e-6:
            msgs.append(Message("erreur", "support", f"Colis {c.numero} : seulement {ts:.0%} de sa base est "
                                f"soutenue (minimum {params.support_minimal:.0%}).", n, [c.numero]))
        # Masse et fragilité des supports
        for q, _ in supports(p, pls, jeu):
            if c.masse > params.facteur_masse * q.colis.masse + 1e-6:
                msgs.append(Message("erreur", "lourd_sur_leger", f"Colis {c.numero} ({c.masse:g} kg) est posé "
                                    f"sur le colis {q.colis.numero} plus léger ({q.colis.masse:g} kg).",
                                    n, [c.numero, q.colis.numero]))
            if q.colis.fragile:
                msgs.append(Message("erreur", "fragile", f"Colis {c.numero} est posé sur le colis fragile "
                                    f"{q.colis.numero}.", n, [c.numero, q.colis.numero]))
        # Colis IATA au bord
        if params.iata_au_bord and classe_de(c.classe).iata and not touche_bord_couloir(p, L, jeu):
            msgs.append(Message("avertissement", "iata_bord", f"Colis {c.numero} (étiquette {c.classe}) n'est pas "
                                "contre une face côté couloir : étiquette non accessible.", n, [c.numero]))
        # Fragile au sommet (sangles)
        if c.fragile and abs(p.z2 - pal.hauteur) <= jeu and len(pls) > 1:
            msgs.append(Message("avertissement", "fragile_sommet", f"Colis fragile {c.numero} au sommet de la "
                                "palette : risque d'écrasement par les sangles / le filet.", n, [c.numero]))
    # Chevauchements
    for p, q in combinations(pls, 2):
        if se_chevauchent(p, q):
            msgs.append(Message("erreur", "chevauchement", f"Les colis {p.colis.numero} et {q.colis.numero} "
                                f"se chevauchent.", n, [p.colis.numero, q.colis.numero]))
    # Séparation IATA / armement
    notes: dict[str, list[str]] = {}
    for p, q in combinations(pls, 2):
        motif = regles.motif_incompatibilite(p.colis.classe, q.colis.classe)
        if motif:
            msgs.append(Message("erreur", "iata", f"Colis {p.colis.numero} et {q.colis.numero} : {motif}",
                                n, [p.colis.numero, q.colis.numero]))
        else:
            avert = regles.avertissement(p.colis.classe, q.colis.classe)
            if avert:
                liste = notes.setdefault(avert, [])
                for num in (p.colis.numero, q.colis.numero):
                    if num not in liste:
                        liste.append(num)
    for avert, numeros in notes.items():
        msgs.append(Message("avertissement", "iata_note", f"Palette {n}, colis {', '.join(numeros)} : {avert}",
                            n, numeros))
    # Rampe
    if pal.zone == "rampe":
        if pal.masse > params.masse_nette_max_rampe + 1e-6:
            msgs.append(Message("avertissement", "rampe_masse", f"Palette {n} prévue sur la rampe : masse nette "
                                f"{pal.masse:g} kg > {params.masse_nette_max_rampe:g} kg autorisés (3 t brut).", n))
        if pal.hauteur > params.hauteur_max_rampe + 1e-6:
            msgs.append(Message("avertissement", "rampe_hauteur", f"Palette {n} prévue sur la rampe : hauteur "
                                f"{pal.hauteur:g} cm > {params.hauteur_max_rampe:g} cm.", n))
    # Répartition du poids sur la surface (préférence)
    if pls and pal.taux_occupation_sol() < 0.5 and pal.hauteur > 0 and len(pls) > 2:
        msgs.append(Message("avertissement", "surface", f"Palette {n} : seulement {pal.taux_occupation_sol():.0%} "
                            "de la surface est utilisée au sol ; le poids est peu réparti.", n))
    return msgs


def verifier(resultat: Resultat, regles: ReglesSeparation | None = None) -> list[Message]:
    regles = regles or regles_par_defaut()
    params = resultat.parametres
    msgs: list[Message] = []
    for pal in resultat.palettes:
        msgs.extend(verifier_palette(pal, params, regles))
    for c, motif in resultat.non_places:
        msgs.append(Message("erreur", "non_place", f"Colis {c.numero} non palettisé : {motif}.", None, [c.numero]))

    # Contraintes de l'avion
    bilan = avion.bilan(resultat)
    if bilan["positions_utilisees"] > bilan["positions_disponibles"]:
        msgs.append(Message("erreur", "positions", f"{bilan['positions_utilisees']} emplacements nécessaires pour "
                            f"{bilan['positions_disponibles']} disponibles avec {params.passagers} passagers."))
    if bilan["masse_totale_brute"] > params.capacite_avion:
        msgs.append(Message("erreur", "capacite", f"Charge totale {bilan['masse_totale_brute']:.0f} kg (fret + palettes"
                            f" + passagers) > capacité indicative {params.capacite_avion:.0f} kg."))
    # Palettes contenant des classes à séparer entre elles dans l'avion (information mécanicien navigant)
    for a, b in combinations(resultat.palettes, 2):
        paires = [f"{ca} / {cb}" for ca in sorted(a.classes) for cb in sorted(b.classes)
                  if regles.motif_incompatibilite(ca, cb)]
        if paires:
            msgs.append(Message("avertissement", "avion_separation",
                                f"Palettes {a.numero} et {b.numero} : à ne pas charger côte à côte dans l'avion "
                                f"(étiquettes {', '.join(paires)}).", None))
    # Dédoublonnage
    vus, uniques = set(), []
    for m in msgs:
        cle = (m.niveau, m.code, m.message)
        if cle not in vus:
            vus.add(cle)
            uniques.append(m)
    uniques.sort(key=lambda m: (0 if m.niveau == "erreur" else 1, m.palette or 0))
    return uniques
