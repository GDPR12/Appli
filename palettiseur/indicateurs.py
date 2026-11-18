"""Indicateurs de qualité d'un chargement, ceux de la visualisation du notebook (cellules 22 et 23).

Ce sont des mesures descriptives : elles n'influencent ni le placement ni ses priorités. Définitions du notebook :

* **hauteur** : sommet le plus haut des colis de la palette ;
* **compacité** : volume des colis / volume de leur boîte englobante ;
* **remplissage volumique** : volume des colis / (L x W x H) de la palette ;
* **centre de masse** : cargaison seule, mesuré depuis le centre du plateau (x selon l'axe X, y selon l'axe Y) ;
* **décalage horizontal** : distance entre ce centre de masse et le centre du plateau (0 = centré) ;
* **surface au sol** : union des bases des colis posés sur le plateau, sans compter deux fois les recouvrements
  (``surface_sol`` et ``taux_surface_sol`` de ``actualiser_chargement``).

Unités de sortie : mètres, mètres carrés et mètres cubes, comme le notebook ; rapports entre 0 et 1.
"""

from __future__ import annotations

from .modeles import Palette

CM_EN_M = 0.01
SOL = 1e-3          # cm : un colis dont la base est à moins de 0,01 mm du plateau est posé au sol


def aire_union(rectangles: list[tuple[float, float, float, float]]) -> float:
    """Aire de l'union de rectangles ``(x0, x1, y0, y1)``, sans double comptage (balayage par bandes)."""
    xs = sorted({x for r in rectangles for x in r[:2]})
    aire = 0.0
    for gauche, droite in zip(xs, xs[1:]):
        intervalles = sorted((y0, y1) for x0, x1, y0, y1 in rectangles if x0 < droite and x1 > gauche)
        if not intervalles:
            continue
        bas, haut = intervalles[0]
        longueur = 0.0
        for y0, y1 in intervalles[1:]:
            if y0 <= haut:
                haut = max(haut, y1)
            else:
                longueur += haut - bas
                bas, haut = y0, y1
        aire += (droite - gauche) * (longueur + haut - bas)
    return aire


def indicateurs_palette(pal: Palette) -> dict:
    """Mesures de qualité d'une palette (voir le module)."""
    t = pal.type
    placements = pal.placements
    base = {"palette": pal.numero, "nombre_colis": len(placements), "masse": pal.masse, "masse_max": t.masse_max,
            "taux_masse": pal.masse / t.masse_max if t.masse_max > 0 else 0.0}
    if not placements:
        return {**base, "hauteur": 0.0, "compacite": 0.0, "remplissage_volumique": 0.0, "volume_colis": 0.0,
                "volume_enveloppe": 0.0, "centre_masse_x": 0.0, "centre_masse_y": 0.0, "decalage_horizontal": 0.0,
                "surface_sol": 0.0, "taux_surface_sol": 0.0}
    volume = sum(p.dx * p.dy * p.dz for p in placements) * CM_EN_M ** 3
    enveloppe = ((max(p.x2 for p in placements) - min(p.x for p in placements))
                 * (max(p.y2 for p in placements) - min(p.y for p in placements))
                 * (max(p.z2 for p in placements) - min(p.z for p in placements))) * CM_EN_M ** 3
    masse = sum(p.colis.masse for p in placements)
    cx = sum(p.colis.masse * p.centre[0] for p in placements) / masse - t.largeur / 2
    cy = sum(p.colis.masse * p.centre[1] for p in placements) / masse - t.longueur / 2
    surface = aire_union([(p.x, p.x2, p.y, p.y2) for p in placements if p.z <= SOL]) * CM_EN_M ** 2
    plateau = t.largeur * t.longueur * CM_EN_M ** 2
    return {
        **base,
        "hauteur": max(p.z2 for p in placements) * CM_EN_M,
        "compacite": volume / enveloppe if enveloppe > 0 else 0.0,
        "remplissage_volumique": volume / (t.largeur * t.longueur * t.hauteur_max * CM_EN_M ** 3),
        "volume_colis": volume, "volume_enveloppe": enveloppe,
        "centre_masse_x": cx * CM_EN_M, "centre_masse_y": cy * CM_EN_M,
        "decalage_horizontal": (cx * cx + cy * cy) ** 0.5 * CM_EN_M,
        "surface_sol": surface, "taux_surface_sol": surface / plateau if plateau > 0 else 0.0,
    }


def indicateurs_etat(palettes: list[Palette]) -> dict:
    """Mesures de toutes les palettes d'un état du calcul et résumé (nombre de palettes, hauteurs)."""
    mesures = [indicateurs_palette(p) for p in palettes if p.placements]
    return {
        "nombre_palettes": len(mesures),
        "hauteur_maximale": max((m["hauteur"] for m in mesures), default=0.0),
        "somme_hauteurs": sum(m["hauteur"] for m in mesures),
        "remplissage_moyen": (sum(m["remplissage_volumique"] for m in mesures) / len(mesures)) if mesures else 0.0,
        "palettes": mesures,
    }
