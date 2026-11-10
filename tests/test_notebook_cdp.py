"""Moteur du notebook CDP_5 (``notebook_cdp.py``) : parité avec le notebook, regroupement par DAT, catégories A et B,
règles de l'application branchées sur le code du notebook, repli vers le moteur de l'application."""

import random

import pytest

from palettiseur import api, notebook_cdp, optimisation as opt
from palettiseur.dat import partition
from palettiseur.geometrie import cotes_bord, cotes_visibles
from palettiseur.iata import classe as classe_de, classe_application, classe_notebook, regles
from palettiseur.modeles import Colis, Parametres
from palettiseur.verification import verifier

R = regles()


def lot_cdp():
    """Les 54 colis du notebook CDP_5 (exemple « CDP »), avec ses paramètres."""
    ex = api.exemple("cdp")
    return [Colis.depuis_dict(c) for c in ex["colis"]], Parametres.depuis_dict(ex["parametres"])


def erreurs(res):
    return [m for m in verifier(res) if m.niveau == "erreur"]


# --------------------------------------------------------------------------- parité avec le notebook

def test_exemple_cdp_est_celui_du_notebook():
    lot, p = lot_cdp()
    assert len(lot) == 54 and sum(c.masse for c in lot) == 2290
    assert lot[0].numero == "0836" and lot[0].dat == "DAT12301531890" and lot[0].classe == "9"
    classes = {}
    for c in lot:
        classes[c.classe] = classes.get(c.classe, 0) + 1
    assert classes == {"": 25, "ARME": 14, "SECU": 10, "3": 3, "9": 1, "2.2": 1}
    assert len({c.dat for c in lot}) == 27
    assert p.moteur == "notebook" and p.derogation_couloir and not p.iata_au_bord and p.support_minimal == 0.8
    assert p.regrouper_par_dat and p.marge_hauteur == 5


def test_heuristique_reproduit_les_cinq_essais_du_notebook():
    """Valeurs relevées en exécutant CDP_5 : (palettes, somme des hauteurs en m, positions testées) de chaque essai."""
    lot, p = lot_cdp()
    a_placer, _ = opt._preparer(lot, p)
    ctx = notebook_cdp.Contexte(a_placer, p, R)
    assert ctx.palettes_nb[0] == {"id": 1, "M": 4536, "L": 2.74, "W": 2.24, "H": 2.54}
    with ctx.actif():
        res = notebook_cdp.construire_placement_heuristique(ctx.colis_nb, ctx.palettes_nb)
    h = res["heuristique"]
    assert [(e["ordre"], e["palettes"], round(e["hauteur"], 2), e["positions"]) for e in h["essais"]] == [
        (1, 3, 4.38, 59426), (2, 3, 3.44, 59683), (3, 3, 3.59, 59716), (4, 4, 3.77, 58529), (5, 3, 5.07, 53160)]
    assert h["ordre_retenu"] == 2 and h["positions_testees"] == 290514
    assert [len(c["colis"]) for c in res["palettes"]] == [13, 23, 18]
    assert [round(c["hauteur_utilisee"], 2) for c in res["palettes"]] == [1.15, 1.07, 1.22]


def test_pipeline_complet_sur_l_exemple_du_notebook():
    """Exécution complète avec un budget VND réduit : trois états, quatre phases, contrôles indépendants réussis."""
    lot, p = lot_cdp()
    p.temps_limite = 8
    res = opt.optimiser(lot, p)
    assert res.moteur == "notebook" and not res.non_places
    assert [e.cle for e in res.etapes] == ["heuristique", "apres_vnd", "finale"]
    assert [j["phase"] for j in res.journal] == [1, 2, 3, 4]
    assert [j["titre"] for j in res.journal][:2] == ["Placement heuristique", "VND (transferts, replacements, échanges)"]
    assert erreurs(res) == []
    n = [len(e.palettes) for e in res.etapes]
    assert n[0] == 3 and n[1] <= n[0] and n[2] == n[1]                    # la VND ne dégrade pas le nombre de palettes
    assert "essai 2" in res.journal[0]["texte"] and "Essai retenu : 2" in res.journal[0]["texte"]
    assert "4 contrôle(s) indépendant(s)" in res.journal[3]["texte"]
    # regroupement par DAT respecté : aucun DAT n'est réparti sur plusieurs palettes sauf scission par incompatibilité
    assert not [m for m in verifier(res) if m.code == "dat_reparti"]


# --------------------------------------------------------------------------- catégories A et B, DAT

def test_conversions_de_classes():
    assert [classe_notebook(c) for c in ("", "ARME", "SECU", "9L", "3")] == [0, "A", "B", "9", "3"]
    assert [classe_application(v) for v in (0, "A", "B", "3", None)] == ["", "ARME", "SECU", "3", ""]


def test_armes_et_pieces_de_securite_doivent_rester_visibles():
    """Comme dans le notebook, les catégories A et B déclenchent l'accessibilité latérale (sans être posées au bord)."""
    assert classe_de("ARME").visible and classe_de("SECU").visible and not classe_de("ARME").iata
    assert classe_de("3").visible and classe_de("3").iata and not classe_de("").visible
    rng = random.Random(3)
    lot = [Colis(f"C{i}", rng.randint(30, 80), rng.randint(30, 70), rng.randint(20, 50), rng.randint(5, 60),
                 classe=rng.choice(["", "ARME", "ARME", "SECU"])) for i in range(30)]
    for moteur in ("heuristique", "notebook"):
        res = opt.optimiser(lot, Parametres(moteur=moteur, duree_recherche=1.0, temps_limite=3, regrouper_par_dat=False))
        assert not res.non_places and erreurs(res) == []
        for pal in res.palettes:
            for pl in pal.placements:
                if pl.colis.classe in ("ARME", "SECU"):
                    assert cotes_visibles(pl, pal.placements, pal.type.axe_cotes), (moteur, pl.colis.numero)
        # l'arme et sa pièce de sécurité ne partagent jamais une palette
        for pal in res.palettes:
            assert not {"ARME", "SECU"} <= {pl.colis.classe for pl in pal.placements}


def test_partition_par_dat_comme_le_notebook():
    lot = [Colis("1", 10, 10, 10, 1, classe="ARME", dat="D1"), Colis("2", 10, 10, 10, 1, classe="SECU", dat="D1"),
           Colis("3", 10, 10, 10, 1, classe="ARME", dat="D1"), Colis("4", 10, 10, 10, 1, dat="D1"),
           Colis("5", 10, 10, 10, 1, dat="D2"), Colis("6", 10, 10, 10, 1, dat="D2"), Colis("7", 10, 10, 10, 1)]
    cles = partition(lot, R)
    par_numero = {c.numero: cles[id(c)] for c in lot}
    # D1 : A et B incompatibles -> deux sous-groupes ; l'ordinaire rejoint le premier groupe compatible
    assert par_numero["1"] == par_numero["3"] == par_numero["4"] and par_numero["2"] != par_numero["1"]
    assert par_numero["1"][:2] == ("DAT", "D1") and par_numero["2"][:2] == ("DAT", "D1")
    assert par_numero["5"] == par_numero["6"] == ("DAT", "D2", 0)
    assert par_numero["7"] == ("colis", "7")                                   # sans DAT : indépendant
    assert all(c[0] == "colis" for c in partition(lot, R, regrouper=False).values())


def test_un_dat_compatible_reste_sur_une_palette():
    """Trois DAT de quatre colis : chaque DAT doit tenir sur une seule palette."""
    lot = []
    for d in range(3):
        for k in range(4):
            lot.append(Colis(f"D{d}-{k}", 60, 40, 40, 80, dat=f"DAT{d}"))
    res = opt.optimiser(lot, Parametres(duree_recherche=0.5, temps_limite=3))
    assert res.moteur == "notebook" and erreurs(res) == []
    par_dat = {}
    for pal in res.palettes:
        for pl in pal.placements:
            par_dat.setdefault(pl.colis.dat, set()).add(pal.numero)
    assert all(len(v) == 1 for v in par_dat.values())
    assert not [m for m in verifier(res) if m.code == "dat_reparti"]


def test_un_dat_conflictuel_est_divise_en_groupes_compatibles():
    lot = [Colis("a1", 50, 40, 30, 20, classe="ARME", dat="X"), Colis("a2", 50, 40, 30, 20, classe="ARME", dat="X"),
           Colis("s1", 50, 40, 30, 20, classe="SECU", dat="X"), Colis("o1", 50, 40, 30, 20, dat="X")]
    res = opt.optimiser(lot, Parametres(duree_recherche=0.5, temps_limite=3))
    assert res.moteur == "notebook" and erreurs(res) == []
    assert len(res.palettes) == 2                                              # armes d'un côté, pièce de sécurité de l'autre
    assert "1 DAT séparés" in res.journal[0]["texte"]
    assert not [m for m in verifier(res) if m.code == "dat_reparti"]           # sous-groupes compatibles respectés


def test_regroupement_impossible_est_relache_puis_signale():
    """Un DAT trop volumineux pour une palette : le moteur relâche le regroupement avant de changer de moteur."""
    lot = [Colis(f"g{i}", 200, 150, 100, 900, dat="GROS") for i in range(5)]      # 4,5 t : plus d'une palette
    res = opt.optimiser(lot, Parametres(duree_recherche=0.5, temps_limite=3, derogation_couloir=True))
    assert res.moteur == "notebook" and not res.non_places and erreurs(res) == []
    assert "est impossible avec les règles actuelles" in res.journal[0]["texte"] and "regroupement par DAT : non" in res.journal[0]["texte"]
    assert [m for m in verifier(res) if m.code == "dat_reparti"]               # le plan le signale
    sans = opt.optimiser(lot, Parametres(duree_recherche=0.5, temps_limite=3, derogation_couloir=True, regrouper_par_dat=False))
    assert not [m for m in verifier(sans) if m.code == "dat_reparti"]


# --------------------------------------------------------------------------- règles de l'application, repli

def test_les_accroches_ne_restent_pas_branchees():
    lot = [Colis(f"c{i}", 60, 40, 40, 30) for i in range(6)]
    opt.optimiser(lot, Parametres(duree_recherche=0.5, temps_limite=2))
    assert notebook_cdp.HOOKS.regles is None and notebook_cdp.HOOKS.incompatibles is None
    assert notebook_cdp.support_minimal == 0.80 and notebook_cdp.regrouper_par_DAT is True and notebook_cdp.marge_hauteur == 0.05


@pytest.mark.parametrize("axe", ["y", "x"])
def test_le_notebook_applique_les_regles_de_l_application(axe):
    """Cartons, lourd sur léger, fragile, armes et bord des colis IATA, sur l'axe des côtés choisi."""
    rng = random.Random(4)
    lot = [Colis(f"C{i}", rng.randint(30, 90), rng.randint(30, 70), rng.randint(20, 60), rng.randint(5, 150),
                 emballage=rng.choice(["", "bois", "carton"]), classe=rng.choice(["", "", "", "3", "9", "ARME", "SECU"]),
                 fragile=rng.random() < 0.05) for i in range(26)]
    prm = Parametres(duree_recherche=1.0, temps_limite=4, axe_cotes=axe, regrouper_par_dat=False)
    res = opt.optimiser(lot, prm)
    assert res.moteur == "notebook" and not res.non_places and erreurs(res) == []
    for pal in res.palettes:
        for p in pal.placements:
            if classe_de(p.colis.classe).iata:         # vraie classe IATA : bord et visibilité
                assert set(cotes_visibles(p, pal.placements, axe)) & set(
                    cotes_bord(p, pal.type.etendue_cotes, prm.jeu_contact, axe)), p.colis.numero


def test_le_notebook_cede_la_place_au_moteur_de_l_application():
    """Le notebook ne connaît pas la BIPAL : un colis de 3 m fait passer le calcul sur le moteur de l'application."""
    lot = [Colis("long", 320, 80, 60, 400), Colis("petit", 50, 50, 50, 30)]
    res = opt.optimiser(lot, Parametres(duree_recherche=0.5))
    assert res.moteur == "heuristique" and res.palettes[0].type.code == "BIPAL" and not res.non_places
    assert "Moteur du notebook indisponible" in res.journal[0]["texte"] and "BIPAL" in res.journal[0]["texte"]
    assert [e.cle for e in res.etapes] == ["heuristique", "apres_vnd", "finale"]


def test_conversion_des_coordonnees_selon_l_axe_des_cotes():
    lot, _ = lot_cdp()
    base = dict(derogation_couloir=True)
    px, py = Parametres(axe_cotes="x", **base), Parametres(axe_cotes="y", **base)
    ctx_x = notebook_cdp.Contexte([(c, px.types_palettes()[0]) for c in lot], px, R)
    ctx_y = notebook_cdp.Contexte([(c, py.types_palettes()[0]) for c in lot], py, R)
    assert ctx_x.palettes_nb[0]["L"] == pytest.approx(2.24) and ctx_x.palettes_nb[0]["W"] == pytest.approx(2.74)
    assert ctx_y.palettes_nb[0]["L"] == pytest.approx(2.74) and ctx_y.palettes_nb[0]["W"] == pytest.approx(2.24)
    brut = {"palettes": [{"id": 1, "colis": [
        {"id": "0836", "m": 15, "iata": "9", "x": 0.4, "y": -0.3, "z": 0.075, "l_place": 0.45, "w_place": 0.3, "h_place": 0.15}]}]}
    b = ctx_x.vers_chargements(brut)[0].boites[0]
    assert (round(b.x1, 6), round(b.y1, 6), round(b.dx, 6), round(b.dy, 6)) == (92, 129.5, 30, 45)
    b = ctx_y.vers_chargements(brut)[0].boites[0]
    assert (round(b.x1, 6), round(b.y1, 6), round(b.dx, 6), round(b.dy, 6)) == (137 + 40 - 22.5, 112 - 30 - 15, 45, 30)
