from palettiseur.geometrie import (aire_recouvrement, cotes_bord, cotes_visibles, en_contact, iata_en_regle, orientations,
                                   plafond_charge, se_chevauchent, supports, taux_support, tient_dans)
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


def test_cotes_visibles_regle_de_cdp3_sur_l_axe_du_couloir():
    """Un autre colis ne masque un côté que si les projections se chevauchent en y ET en z et qu'il dépasse en x."""
    a = Placement(colis(numero="A"), 100, 0, 0, 50, 50, 50)               # x 100-150, y 0-50, z 0-50
    assert cotes_visibles(a, [a]) == ("gauche", "droite")
    cote_gauche = Placement(colis(numero="B"), 0, 10, 0, 100, 30, 50)     # x 0-100 : dépasse côté gauche
    assert cotes_visibles(a, [a, cote_gauche]) == ("droite",)
    cote_droit = Placement(colis(numero="C"), 150, 10, 0, 96, 30, 50)     # x 150-246 : dépasse côté droit
    assert cotes_visibles(a, [a, cote_droit]) == ("gauche",)
    assert cotes_visibles(a, [a, cote_gauche, cote_droit]) == ()
    # côte à côte en y (simple contact) ou à un autre niveau en z : aucun masquage
    assert cotes_visibles(a, [a, Placement(colis(numero="D"), 0, 50, 0, 246, 50, 50)]) == ("gauche", "droite")
    assert cotes_visibles(a, [a, Placement(colis(numero="E"), 0, 0, 50, 246, 50, 50)]) == ("gauche", "droite")
    # même étendue en x : ne dépasse d'aucun côté
    assert cotes_visibles(a, [a, Placement(colis(numero="F"), 100, 10, 0, 50, 30, 50)]) == ("gauche", "droite")
    # un colis qui dépasse d'un seul côté ne masque que ce côté-là
    assert cotes_visibles(a, [a, Placement(colis(numero="G"), 100, 10, 0, 120, 30, 50)]) == ("gauche",)
    # un dépassement inférieur à la tolérance n'est pas un masquage
    assert cotes_visibles(a, [a, Placement(colis(numero="H"), 100 - 1e-7, 10, 0, 50, 30, 50)]) == ("gauche", "droite")


def test_cotes_bord():
    assert cotes_bord(Placement(colis(), 0, 30, 0, 50, 50, 50), 246, 0.5) == ("gauche",)
    assert cotes_bord(Placement(colis(), 196, 30, 0, 50, 50, 50), 246, 0.5) == ("droite",)
    assert cotes_bord(Placement(colis(), 0, 0, 0, 246, 50, 50), 246, 0.5) == ("gauche", "droite")
    assert cotes_bord(Placement(colis(), 20, 30, 0, 50, 50, 50), 246, 0.5) == ()


def test_iata_en_regle_exige_le_bord_et_la_visibilite():
    params = Parametres()
    dangereux = colis(numero="D", classe="3", longueur=50, largeur=50)
    au_bord = Placement(dangereux, 0, 0, 0, 50, 50, 50)
    loin = Placement(dangereux, 60, 0, 0, 50, 50, 50)
    assert iata_en_regle(au_bord, [au_bord], 246, params)
    assert not iata_en_regle(loin, [loin], 246, params)
    assert iata_en_regle(loin, [loin], 246, Parametres(iata_au_bord=False))       # visible seulement : suffit
    # contre le bord gauche mais masqué à gauche par rien (il est au bord) et à droite par un colis plus large : reste le gauche
    voisin = Placement(colis(numero="V"), 0, 0, 0, 120, 50, 50)
    assert iata_en_regle(au_bord, [au_bord, voisin], 246, params)
    # masqué des deux côtés : un colis dépasse à gauche et un autre à droite
    gauche = Placement(colis(numero="G"), -10, 0, 0, 30, 50, 50)
    droite = Placement(colis(numero="R"), 40, 0, 0, 100, 50, 50)
    assert not iata_en_regle(au_bord, [au_bord, gauche, droite], 246, params)


def test_plafond_charge_fragile_carton_et_masse():
    params = Parametres()
    assert plafond_charge(colis(masse=100), params) == 100                                   # facteur masse 1
    assert plafond_charge(colis(masse=100, fragile=True), params) == 0
    assert plafond_charge(colis(masse=100, emballage="carton"), params) == 0               # carton : rien dessus
    assert plafond_charge(colis(masse=100, emballage="fibre"), params) == 0
    assert plafond_charge(colis(masse=100, emballage="bois"), params) == 100
    assert plafond_charge(colis(masse=100, emballage="carton"), Parametres(charge_max_carton=30)) == 30
    assert plafond_charge(colis(masse=20, emballage="carton"), Parametres(charge_max_carton=30)) == 20
    # le réglage « matières à ne pas écraser » est modifiable
    assert plafond_charge(colis(masse=100, emballage="fibre"), Parametres(emballages_ecrasables="carton")) == 100
    assert plafond_charge(colis(masse=100, emballage="bois"), Parametres(emballages_ecrasables="bois,carton")) == 0
