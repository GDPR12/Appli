from palettiseur.geometrie import (aire_recouvrement, en_contact, orientations, se_chevauchent,
                                   supports, taux_support, tient_dans, touche_bord_couloir)
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


def test_taux_support_partiel():
    a = Placement(colis(), 0, 0, 0, 80, 60, 50)
    e = Placement(colis(numero="E"), 40, 0, 50, 80, 60, 50)  # moitié en porte-à-faux
    assert abs(taux_support(e, [a]) - 0.5) < 1e-9


def test_bord_couloir_et_tient_dans():
    p = Placement(colis(), 0, 10, 0, 80, 60, 50)
    assert touche_bord_couloir(p, 234)
    assert touche_bord_couloir(Placement(colis(), 154, 10, 0, 80, 60, 50), 234)
    assert not touche_bord_couloir(Placement(colis(), 50, 10, 0, 80, 60, 50), 234)
    assert tient_dans(colis(longueur=300, largeur=60, hauteur=50, renversable=False), 234, 448, 200)
    assert not tient_dans(colis(longueur=300, largeur=60, hauteur=50, renversable=False), 234, 224, 200)
    # renversable : 210 cm de haut peut être couché
    assert tient_dans(colis(longueur=60, largeur=60, hauteur=210, renversable=True), 234, 224, 200)
    assert not tient_dans(colis(longueur=60, largeur=60, hauteur=210, renversable=False), 234, 224, 200)
