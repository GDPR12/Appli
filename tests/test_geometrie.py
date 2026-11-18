from palettiseur.geometrie import (aire_recouvrement, cotes_bord, cotes_visibles, en_contact, iata_en_regle, orientations,
                                   plafond_charge, se_chevauchent, supports, taux_support, tient_dans)
import pytest

from palettiseur.modeles import Colis, Parametres, Placement


def colis(**kw):
    base = dict(numero="X", longueur=80, largeur=60, hauteur=50, masse=10)
    base.update(kw)
    return Colis(**base)


def test_orientations_toujours_a_plat():
    """Le colis garde sa hauteur : seules la longueur et la largeur s'échangent (pas de colis couché)."""
    assert orientations(colis()) == [(80, 60, 50), (60, 80, 50)]


def test_orientations_carre_sans_doublon():
    assert orientations(colis(longueur=50, largeur=50, hauteur=70)) == [(50, 50, 70)]


def test_chevauchement_et_contact():
    a = Placement(colis(), 0, 0, 0, 80, 60, 50)
    b = Placement(colis(numero="Y"), 80, 0, 0, 80, 60, 50)   # contact latéral en x
    c = Placement(colis(numero="Z"), 40, 0, 0, 80, 60, 50)   # chevauche a
    d = Placement(colis(numero="W"), 0, 0, 50, 80, 60, 50)   # posé sur a
    assert not se_chevauchent(a, b) and en_contact(a, b)
    assert se_chevauchent(a, c)
    assert not se_chevauchent(a, d) and en_contact(a, d)
    assert aire_recouvrement(a, d) == 80 * 60
    assert sorted(q.colis.numero for q, _ in supports(d, [a, b, c])) == ["X", "Z"]  # c chevauche aussi l'empreinte de d
    assert taux_support(d, [a]) == 1.0
    assert taux_support(a, []) == 1.0  # au sol


def test_un_recouvrement_infime_n_est_pas_un_appui():
    """Le tassement s'arrête à ~5 micromètres d'un colis : ce n'est pas une surface de contact."""
    haut = Placement(colis(numero="H", masse=179), 0, 0, 50, 80, 60, 50)
    leger = Placement(colis(numero="L", masse=17), 80 - 4.6e-4, 0, 0, 40, 60, 50)     # dépasse de 4,6 µm sous le colis haut
    assert aire_recouvrement(haut, leger) == 0.0
    assert supports(haut, [leger]) == []
    vrai = Placement(colis(numero="V", masse=17), 70, 0, 0, 40, 60, 50)              # 10 cm de recouvrement
    assert aire_recouvrement(haut, vrai) == 10 * 60


def test_taux_support_partiel():
    a = Placement(colis(), 0, 0, 0, 80, 60, 50)
    e = Placement(colis(numero="E"), 40, 0, 50, 80, 60, 50)  # moitié en porte-à-faux
    assert abs(taux_support(e, [a]) - 0.5) < 1e-9


def test_tient_dans():
    assert tient_dans(colis(longueur=300, largeur=60, hauteur=50), 246, 448, 254)
    assert not tient_dans(colis(longueur=300, largeur=60, hauteur=50), 246, 224, 254)
    # un colis de 210 cm de haut ne peut pas être couché : il doit tenir debout
    assert tient_dans(colis(longueur=60, largeur=60, hauteur=210), 246, 224, 254)
    assert not tient_dans(colis(longueur=60, largeur=60, hauteur=210), 246, 224, 200)
    # 274 cm de large dépassent la largeur utile de 246 cm
    assert not tient_dans(colis(longueur=270, largeur=60, hauteur=50), 246, 224, 254)


def pl(axe, s, t, z, ds, dt, dz, numero="X", **kw):
    """Placement dont ``s`` / ``ds`` sont la position et la taille sur l'axe des côtés, ``t`` / ``dt`` sur l'autre axe."""
    c = colis(numero=numero, **kw)
    return Placement(c, s, t, z, ds, dt, dz) if axe == "x" else Placement(c, t, s, z, dt, ds, dz)


@pytest.mark.parametrize("axe", ["x", "y"])
def test_cotes_visibles_regle_du_notebook(axe):
    """Un autre colis ne masque un côté que si les projections se chevauchent sur l'autre axe ET en z et qu'il dépasse
    sur l'axe des côtés (``cotes_accessibles_iata`` du notebook, qui travaille sur l'axe y)."""
    a = pl(axe, 100, 0, 0, 50, 50, 50, "A")                                  # côtés 100-150, autre axe 0-50
    v = lambda *autres: cotes_visibles(a, [a, *autres], axe)
    assert v() == ("gauche", "droite")
    cote_gauche = pl(axe, 0, 10, 0, 100, 30, 50, "B")                        # 0-100 : dépasse côté gauche
    cote_droit = pl(axe, 150, 10, 0, 96, 30, 50, "C")                        # 150-246 : dépasse côté droit
    assert v(cote_gauche) == ("droite",)
    assert v(cote_droit) == ("gauche",)
    assert v(cote_gauche, cote_droit) == ()
    # côte à côte sur l'autre axe (simple contact) ou à un autre niveau en z : aucun masquage
    assert v(pl(axe, 0, 50, 0, 246, 50, 50, "D")) == ("gauche", "droite")
    assert v(pl(axe, 0, 0, 50, 246, 50, 50, "E")) == ("gauche", "droite")
    # même étendue sur l'axe des côtés : ne dépasse d'aucun côté
    assert v(pl(axe, 100, 10, 0, 50, 30, 50, "F")) == ("gauche", "droite")
    # un colis qui dépasse d'un seul côté ne masque que ce côté-là
    assert v(pl(axe, 100, 10, 0, 120, 30, 50, "G")) == ("gauche",)
    # un dépassement inférieur à la tolérance n'est pas un masquage
    assert v(pl(axe, 100 - 1e-7, 10, 0, 50, 30, 50, "H")) == ("gauche", "droite")


def test_l_axe_des_cotes_change_la_visibilite():
    """Deux colis qui se masquent sur l'axe y ne se masquent pas sur l'axe x, et inversement."""
    a = Placement(colis(numero="A"), 100, 0, 0, 50, 50, 50)
    b = Placement(colis(numero="B"), 0, 10, 0, 100, 30, 50)       # à gauche de a (x plus petit), recouvre a en y
    assert cotes_visibles(a, [a, b], "x") == ("droite",)
    assert cotes_visibles(a, [a, b], "y") == ("gauche", "droite")


@pytest.mark.parametrize("axe", ["x", "y"])
def test_cotes_bord(axe):
    assert cotes_bord(pl(axe, 0, 30, 0, 50, 50, 50), 246, 0.5, axe) == ("gauche",)
    assert cotes_bord(pl(axe, 196, 30, 0, 50, 50, 50), 246, 0.5, axe) == ("droite",)
    assert cotes_bord(pl(axe, 0, 0, 0, 246, 50, 50), 246, 0.5, axe) == ("gauche", "droite")
    assert cotes_bord(pl(axe, 20, 30, 0, 50, 50, 50), 246, 0.5, axe) == ()


@pytest.mark.parametrize("axe", ["x", "y"])
def test_iata_en_regle_exige_le_bord_et_la_visibilite(axe):
    params = Parametres(axe_cotes=axe, iata_au_bord=True)
    dangereux = dict(numero="D", classe="3", longueur=50, largeur=50)
    au_bord = pl(axe, 0, 0, 0, 50, 50, 50, **dangereux)
    loin = pl(axe, 60, 0, 0, 50, 50, 50, **dangereux)
    assert iata_en_regle(au_bord, [au_bord], 246, params, axe)
    assert not iata_en_regle(loin, [loin], 246, params, axe)
    assert iata_en_regle(loin, [loin], 246, Parametres(iata_au_bord=False, axe_cotes=axe), axe)    # visible seulement
    # contre le bord gauche, masqué à droite par un colis plus large : reste le côté gauche
    voisin = pl(axe, 0, 0, 0, 120, 50, 50, "V")
    assert iata_en_regle(au_bord, [au_bord, voisin], 246, params, axe)
    # masqué des deux côtés : un colis dépasse à gauche et un autre à droite
    gauche = pl(axe, -10, 0, 0, 30, 50, 50, "G")
    droite = pl(axe, 40, 0, 0, 100, 50, 50, "R")
    assert not iata_en_regle(au_bord, [au_bord, gauche, droite], 246, params, axe)


def test_plafond_charge_fragile_et_masse():
    """Rien sur un colis fragile ; « lourd sur léger » seulement si ``facteur_masse`` > 0 (désactivé par défaut)."""
    assert plafond_charge(colis(masse=100), Parametres()) == float("inf")                  # le notebook n'a pas cette règle
    assert plafond_charge(colis(masse=100), Parametres(facteur_masse=1)) == 100
    assert plafond_charge(colis(masse=100), Parametres(facteur_masse=1.5)) == 150
    assert plafond_charge(colis(masse=100, fragile=True), Parametres(facteur_masse=1)) == 0
    assert plafond_charge(colis(masse=100, fragile=True), Parametres()) == 0


@pytest.mark.parametrize("matiere", ["", "carton", "fibre", "bois", "metal"])
def test_la_matiere_de_l_emballage_n_intervient_pas(matiere):
    """La matière n'est qu'une information : même charge admissible pour toutes, comme dans le notebook."""
    assert plafond_charge(colis(masse=100, emballage=matiere), Parametres(facteur_masse=1)) == 100
    assert plafond_charge(colis(masse=100, emballage=matiere), Parametres()) == float("inf")


def test_dimensions_utiles_par_defaut_comme_le_notebook():
    """Cellule 2 du notebook : 2,74 x 2,24 x 2,54 m, aucun couloir, aucune tare."""
    p = Parametres()
    t = {t.code: t for t in p.types_palettes()}
    assert (t["10K"].largeur, t["10K"].longueur, t["10K"].hauteur_max, t["10K"].masse_max) == (274, 224, 254, 4536)
    assert (t["BIPAL"].largeur, t["BIPAL"].longueur) == (274, 448) and t["TRIPAL"].longueur == 672
    r = p.type_rampe()
    assert (r.largeur, r.longueur, r.hauteur_max, r.masse_max) == (274, 224, 254, 3000)
    assert (p.reduction_couloir, p.tare_palette, p.iata_au_bord, p.facteur_masse) == (0, 0, False, 0)


def test_dimensions_utiles_selon_l_axe_des_cotes():
    """Un couloir de 14 cm de chaque côté réduit l'axe des côtés : y (notebook) par défaut, ou x."""
    t = {t.code: t for t in Parametres(reduction_couloir=14).types_palettes()}
    assert (t["10K"].largeur, t["10K"].longueur, t["10K"].axe_cotes) == (274, 196, "y")
    assert (t["BIPAL"].largeur, t["BIPAL"].longueur) == (274, 420)         # le couloir n'est laissé qu'aux deux extrémités
    assert t["TRIPAL"].longueur == 644 and t["10K"].etendue_cotes == 196
    t = {t.code: t for t in Parametres(axe_cotes="x", reduction_couloir=14).types_palettes()}
    assert (t["10K"].largeur, t["10K"].longueur, t["10K"].etendue_cotes) == (246, 224, 246)
    assert t["BIPAL"].longueur == 448
    r = Parametres(reduction_couloir=14).type_rampe()
    assert (r.largeur, r.longueur) == (274, 196)                           # la rampe aussi a son couloir
    # projets enregistrés avec l'ancienne dérogation : pas de couloir
    assert Parametres.depuis_dict({"reduction_couloir": 14, "derogation_couloir": True}).reduction_couloir == 0


def test_emplacements_soute_et_rampe():
    """7 emplacements en soute (1 à 7) et 2 sur la rampe (8 et 9), déclarables indisponibles."""
    p = Parametres()
    assert p.numeros_emplacements("soute") == [1, 2, 3, 4, 5, 6, 7] and p.numeros_emplacements("rampe") == [8, 9]
    assert p.emplacements_libres() == list(range(1, 10)) and (p.places_soute, p.places_rampe) == (7, 2)
    p = Parametres.depuis_dict({"emplacements_indisponibles": [2, "9"]})
    assert p.emplacements_libres() == [1, 3, 4, 5, 6, 7, 8] and (p.places_soute, p.places_rampe) == (6, 1)
    assert Parametres.depuis_dict({"emplacements_indisponibles": "1, 8"}).emplacements_libres() == [2, 3, 4, 5, 6, 7, 9]
    # caractéristiques différentes : 4 536 kg en soute, 3 000 kg sur la rampe
    assert (Parametres().types_palettes()[0].masse_max, Parametres().type_rampe().masse_max) == (4536, 3000)
    assert Parametres(tare_palette=130).type_rampe().masse_max == 2870
    # les sièges des passagers prennent les emplacements de l'avant (version à 64 passagers : 7 palettes au plus)
    p = Parametres(passagers=64)
    assert p.emplacements_passagers() == [1, 2] and p.emplacements_libres() == [3, 4, 5, 6, 7, 8, 9]
    assert Parametres(passagers=64, emplacements_indisponibles=[1]).emplacements_passagers() == [2]
    assert Parametres(passagers=200).emplacements_libres() == []

