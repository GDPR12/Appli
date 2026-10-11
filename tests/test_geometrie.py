from palettiseur.geometrie import (aire_recouvrement, cotes_visibles, en_contact, orientations, se_chevauchent,
                                   supports, taux_support, tient_dans)
from palettiseur.modeles import Colis, Placement


def colis(**kw):
    base = dict(numero="X", longueur=80, largeur=60, hauteur=50, masse=10)
    base.update(kw)
    return Colis(**base)


def test_orientations_non_renversable():
    assert orientations(colis(renversable=False)) == [(80, 60, 50), (60, 80, 50)]


def test_orientations_renversable_six_permutations():
    assert len(orientations(colis(renversable=True))) == 6


def test_orientations_cube_sans_doublon():
    assert orientations(colis(longueur=50, largeur=50, hauteur=50, renversable=True)) == [(50, 50, 50)]


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
    assert tient_dans(colis(longueur=300, largeur=60, hauteur=50, renversable=False), 234, 448, 200)
    assert not tient_dans(colis(longueur=300, largeur=60, hauteur=50, renversable=False), 234, 224, 200)
    # renversable : 210 cm de haut peut être couché
    assert tient_dans(colis(longueur=60, largeur=60, hauteur=210, renversable=True), 234, 224, 200)
    assert not tient_dans(colis(longueur=60, largeur=60, hauteur=210, renversable=False), 234, 224, 200)


def test_cotes_visibles_regle_de_cdp2():
    """Un autre colis ne masque un côté que si les projections se chevauchent en x ET en z et qu'il dépasse en y."""
    a = Placement(colis(numero="A"), 0, 100, 0, 50, 50, 50)               # x 0-50, y 100-150, z 0-50
    assert cotes_visibles(a, [a]) == ("gauche", "droite")
    devant = Placement(colis(numero="B"), 10, 0, 0, 30, 100, 50)           # y 0-100 : dépasse côté gauche
    assert cotes_visibles(a, [a, devant]) == ("droite",)
    derriere = Placement(colis(numero="C"), 10, 150, 0, 30, 100, 50)       # y 150-250 : dépasse côté droit
    assert cotes_visibles(a, [a, derriere]) == ("gauche",)
    assert cotes_visibles(a, [a, devant, derriere]) == ()
    # côte à côte en x (simple contact) ou à un autre niveau en z : aucun masquage
    assert cotes_visibles(a, [a, Placement(colis(numero="D"), 50, 0, 0, 50, 300, 50)]) == ("gauche", "droite")
    assert cotes_visibles(a, [a, Placement(colis(numero="E"), 0, 0, 50, 50, 300, 50)]) == ("gauche", "droite")
    # même étendue en y : ne dépasse d'aucun côté
    assert cotes_visibles(a, [a, Placement(colis(numero="F"), 10, 100, 0, 30, 50, 50)]) == ("gauche", "droite")
    # un colis qui dépasse d'un seul côté ne masque que ce côté-là
    assert cotes_visibles(a, [a, Placement(colis(numero="G"), 10, 100, 0, 30, 120, 50)]) == ("gauche",)
    # un dépassement inférieur à la tolérance n'est pas un masquage
    assert cotes_visibles(a, [a, Placement(colis(numero="H"), 10, 100 - 1e-7, 0, 30, 50, 50)]) == ("gauche", "droite")
