import random

import pytest

from palettiseur.modeles import Colis, Parametres
from palettiseur.optimisation import optimiser
from palettiseur.verification import verifier


def colis_aleatoires(n, graine, speciaux=True):
    rng = random.Random(graine)
    res = []
    for i in range(n):
        classe = rng.choice(["", "", "", "", "3", "1", "5.1", "2.2", "8", "9L", "ARME", "SECU"]) if speciaux else ""
        res.append(Colis(f"C{i}", rng.randint(30, 120), rng.randint(30, 100), rng.randint(20, 90),
                         rng.randint(5, 400), emballage=rng.choice(["", "bois", "carton", "fibre", "metal"]),
                         classe=classe, fragile=rng.random() < 0.05))
    return res


def erreurs(resultat):
    return [m for m in verifier(resultat) if m.niveau == "erreur"]


@pytest.mark.parametrize("graine", [1, 2, 3])
def test_plan_sans_erreur(graine):
    r = optimiser(colis_aleatoires(40, graine), Parametres(moteur="heuristique", duree_recherche=1.0, graine=graine))
    assert not r.non_places
    assert sum(len(p.placements) for p in r.palettes) == 40
    assert erreurs(r) == []


def test_lourd_jamais_sur_leger():
    colis = [Colis("lourd", 60, 60, 40, 300), Colis("leger", 60, 60, 40, 20), Colis("moyen", 60, 60, 40, 100)]
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5))
    for pal in r.palettes:
        for p in pal.placements:
            for q in pal.placements:
                if q is not p and abs(q.z2 - p.z) < 1e-6 and q.x < p.x2 and p.x < q.x2 and q.y < p.y2 and p.y < q.y2:
                    assert p.colis.masse <= q.colis.masse


def test_la_hauteur_reste_verticale():
    """Plus de colis renversable : un colis garde toujours sa hauteur saisie à la verticale."""
    colis = [Colis(f"L{i}", 150, 40, 40, 50) for i in range(6)]
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5))
    for pal in r.palettes:
        for p in pal.placements:
            assert p.dz == 40  # la hauteur reste verticale


def test_incompatibles_separes():
    colis = [Colis("expl", 50, 50, 50, 40, classe="1"), Colis("carb", 50, 50, 50, 40, classe="3"),
             Colis("oxy", 50, 50, 50, 40, classe="5.1"), Colis("ordi", 50, 50, 50, 40)]
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5))
    assert len(r.palettes) == 3  # 1, 3 et 5.1 sont deux à deux incompatibles
    assert erreurs(r) == []


def test_arme_et_piece_de_securite_separees():
    colis = [Colis("arme", 100, 40, 30, 60, classe="ARME"), Colis("secu", 30, 30, 20, 10, classe="SECU")]
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5))
    assert len(r.palettes) == 2


def test_colis_trop_long_sur_bipal():
    colis = [Colis("long", 320, 80, 60, 400), Colis("petit", 50, 50, 50, 30)]
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5))
    assert r.palettes[0].type.code == "BIPAL"
    assert r.positions_utilisees == 2
    assert not r.non_places
    assert erreurs(r) == []


def test_colis_impossible_en_vrac():
    p = Parametres(moteur="heuristique", duree_recherche=0.5, autoriser_tripal=False)
    colis = [Colis("helice", 500, 80, 60, 400), Colis("ok", 50, 50, 50, 30)]
    r = optimiser(colis, p)
    assert [c.numero for c, _ in r.non_places] == ["helice"]
    assert "vrac" in r.non_places[0][1]
    assert len(r.palettes) == 1


def test_colis_trop_lourd():
    r = optimiser([Colis("enclume", 50, 50, 50, 15000)], Parametres(moteur="heuristique", duree_recherche=0.5))  # > 3 x 4 406 kg
    assert r.non_places and "masse" in r.non_places[0][1]


def test_capacite_masse_palette():
    colis = [Colis(f"b{i}", 100, 100, 50, 1000) for i in range(9)]  # 9 t : au moins 3 palettes 10k (4 406 kg net)
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5, autoriser_bipal=False, autoriser_tripal=False))
    assert all(p.masse <= p.type.masse_max for p in r.palettes)
    assert len(r.palettes) >= 3
    assert erreurs(r) == []


@pytest.mark.parametrize("axe", ["y", "x"])
def test_colis_iata_visibles_et_sur_le_bord_a_chaque_etape(axe):
    """Règle du notebook sur l'axe des côtés (y par défaut) : chaque colis dangereux est visible depuis un côté ET posé
    contre ce bord, à chaque étape du calcul."""
    from palettiseur.geometrie import cotes_bord, cotes_visibles
    from palettiseur.iata import classe as classe_de
    colis = [Colis(f"d{i}", 40, 40, 40, 30, classe=c) for i, c in enumerate(["3", "3", "9", "2.2", "8"])]
    colis += [Colis(f"o{i}", 50, 40, 40, 30) for i in range(30)]
    params = Parametres(moteur="heuristique", duree_recherche=0.8, axe_cotes=axe)
    r = optimiser(colis, params)
    for etape in r.etapes:
        for pal in etape.palettes:
            assert pal.type.axe_cotes == axe
            for p in pal.placements:
                if classe_de(p.colis.classe).iata:
                    visibles = cotes_visibles(p, pal.placements, axe)
                    bords = cotes_bord(p, pal.type.etendue_cotes, params.jeu_contact, axe)
                    assert set(visibles) & set(bords), (etape.cle, p.colis.numero, visibles, bords)
    assert erreurs(r) == []


@pytest.mark.parametrize("axe,largeur,longueur", [("y", 274, 196), ("x", 246, 224)])
def test_aucun_colis_ne_deborde_dans_le_couloir(axe, largeur, longueur):
    """Le couloir de 14 cm de chaque côté réduit l'axe des côtés (y : 224 -> 196 cm, x : 274 -> 246 cm)."""
    r = optimiser(colis_aleatoires(40, 5), Parametres(moteur="heuristique", duree_recherche=0.8, graine=5, axe_cotes=axe))
    for pal in r.palettes:
        assert pal.type.largeur == largeur and pal.type.longueur == longueur * pal.type.positions - (
            28 * (pal.type.positions - 1) if axe == "y" else 0)
        for p in pal.placements:
            assert p.x >= -1e-6 and p.x2 <= pal.type.largeur + 1e-6 and p.y >= -1e-6 and p.y2 <= pal.type.longueur + 1e-6


def test_derogation_couloir_utilise_toute_la_zone():
    """215 x 215 cm tient sur les 224 cm du plateau mais pas sur les 196 cm utiles (axe y) : il faut alors une BIPAL."""
    lot = [Colis("large", 215, 215, 50, 100)]
    r = optimiser(lot, Parametres(moteur="heuristique", duree_recherche=0.3, derogation_couloir=True))
    assert r.palettes[0].type.code == "10K" and r.palettes[0].type.longueur == 224 and not r.non_places
    r = optimiser(lot, Parametres(moteur="heuristique", duree_recherche=0.3))
    assert not r.non_places and r.palettes[0].type.code == "BIPAL"
    # même chose sur x : 250 x 100 passe sur 274 cm, pas sur 246 cm
    lot = [Colis("large", 250, 100, 50, 100)]
    r = optimiser(lot, Parametres(moteur="heuristique", duree_recherche=0.3, axe_cotes="x", derogation_couloir=True))
    assert r.palettes[0].type.code == "10K"
    r = optimiser(lot, Parametres(moteur="heuristique", duree_recherche=0.3, axe_cotes="x"))
    assert r.palettes[0].type.code == "BIPAL"


def test_rien_de_lourd_sur_un_carton():
    """Les cartons ne sont pas écrasés : sans charge autorisée, aucun colis n'est posé dessus."""
    lot = [Colis(f"K{i}", 60, 60, 40, 40, emballage="carton") for i in range(8)]
    lot += [Colis(f"B{i}", 60, 60, 40, 30, emballage="bois") for i in range(8)]
    r = optimiser(lot, Parametres(moteur="heuristique", duree_recherche=0.8))
    assert erreurs(r) == []
    for pal in r.palettes:
        for p in pal.placements:
            if p.colis.emballage == "carton":
                for q in pal.placements:
                    assert not (q is not p and abs(q.z - p.z2) < 1e-6 and q.x < p.x2 and p.x < q.x2 and q.y < p.y2 and p.y < q.y2)


def test_charge_maximale_sur_carton_autorise_un_colis_leger():
    lot = [Colis("carton", 100, 100, 40, 80, emballage="carton"), Colis("leger", 100, 100, 40, 20)]
    sans = optimiser(lot, Parametres(moteur="heuristique", duree_recherche=0.5, support_minimal=1.0))
    assert erreurs(sans) == []
    avec = optimiser(lot, Parametres(moteur="heuristique", duree_recherche=0.5, charge_max_carton=25))
    assert erreurs(avec) == []


def test_aucun_colis_isole():
    """Règle du projet : un colis ne reste jamais seul à l'écart des autres sur sa palette."""
    from palettiseur.geometrie import en_contact
    r = optimiser(colis_aleatoires(40, 4, speciaux=False), Parametres(moteur="heuristique", duree_recherche=1.0, graine=4))
    for pal in r.palettes:
        if len(pal.placements) < 2:
            continue
        for p in pal.placements:
            assert any(q is not p and en_contact(p, q) for q in pal.placements), p.colis.numero


def test_les_colis_dangereux_sont_places_en_premier_les_plus_incompatibles_d_abord():
    import random
    from palettiseur.optimisation import Strategie, _ordre_placement, _type_requis
    from palettiseur.iata import regles
    p = Parametres()
    lot = [Colis("ordinaire", 100, 100, 100, 500), Colis("c2.2", 30, 30, 30, 1, classe="2.2"),
           Colis("c1", 30, 30, 30, 1, classe="1"), Colis("c3", 30, 30, 30, 1, classe="3"), Colis("c9", 30, 30, 30, 1, classe="9")]
    a_placer = [(c, _type_requis(c, p.types_palettes())) for c in lot]
    ordre = [c.numero for c, _ in _ordre_placement(a_placer, p, Strategie("masse", "cdp"), random.Random(0), regles())]
    # nombre de classes incompatibles : 1 -> 11, 9 -> 5, 3 -> 3, 2.2 -> 1 ; l'ordinaire passe en dernier malgré sa masse
    assert ordre == ["c1", "c9", "c3", "c2.2", "ordinaire"]


def test_fragile_rien_dessus():
    colis = [Colis("frag", 60, 60, 40, 200, fragile=True)] + [Colis(f"o{i}", 60, 60, 40, 50) for i in range(30)]
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5))
    for pal in r.palettes:
        frag = [p for p in pal.placements if p.colis.fragile]
        for f in frag:
            for q in pal.placements:
                if q is not f and abs(q.z - f.z2) < 1e-6:
                    assert not (q.x < f.x2 and f.x < q.x2 and q.y < f.y2 and f.y < q.y2)


def test_rampe_apres_soute():
    colis = [Colis(f"b{i}", 190, 190, 100, 2500) for i in range(9)]  # une palette par colis (2,5 t < 3 t brut)
    r = optimiser(colis, Parametres(moteur="heuristique", duree_recherche=0.5, autoriser_bipal=False, autoriser_tripal=False))
    zones = [p.zone for p in r.palettes]
    assert zones.count("rampe") == 2 and zones[:7] == ["soute"] * 7
    # les palettes rampe respectent 3 t brut
    assert all(p.masse <= Parametres().masse_nette_max_rampe for p in r.palettes if p.zone == "rampe")


def test_liste_vide_et_saisie_invalide():
    assert optimiser([], Parametres(moteur="heuristique", duree_recherche=0.2)).palettes == []
    r = optimiser([Colis("", 0, 10, 10, 5)], Parametres(moteur="heuristique", duree_recherche=0.2))
    assert r.non_places and "saisie invalide" in r.non_places[0][1]
