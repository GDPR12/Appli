from palettiseur import avion
from palettiseur.coloriage import coloriage_carte, dsatur, graphe_contact
from palettiseur.geometrie import en_contact
from palettiseur.modeles import Colis, Palette, Parametres, Placement, Resultat
from palettiseur.optimisation import optimiser
from palettiseur.verification import verifier


def palette_manuelle():
    t = Parametres().types_palettes()[0]
    pal = Palette(1, t)
    pal.placements = [
        Placement(Colis("A", 100, 100, 50, 200), 0, 0, 0, 100, 100, 50),
        Placement(Colis("B", 100, 100, 50, 300), 0, 0, 50, 100, 100, 50),      # plus lourd sur plus léger
        Placement(Colis("C", 50, 50, 50, 10, classe="3"), 100, 100, 0, 50, 50, 50),  # visible : rien devant ni derrière
        Placement(Colis("D", 50, 50, 50, 10, classe="5.1"), 150, 100, 0, 50, 50, 50),  # incompatible avec C
        Placement(Colis("E", 50, 50, 50, 10), 120, 0, 60, 50, 50, 50),   # flotte (support 0 %)
    ]
    return pal


def test_verification_detecte_les_violations():
    pal = palette_manuelle()
    res = Resultat([pal], [], Parametres())
    codes = {m.code for m in verifier(res)}
    assert {"lourd_sur_leger", "iata", "support"} <= codes
    assert "iata_acces" not in codes        # C et D ne sont masqués d'aucun côté


def test_verification_colis_iata_masque_des_deux_cotes():
    t = Parametres().types_palettes()[0]
    pal = Palette(1, t)
    pal.placements = [
        Placement(Colis("M", 50, 50, 50, 10, classe="3"), 50, 100, 0, 50, 50, 50),    # colis dangereux
        Placement(Colis("P", 30, 100, 50, 10), 60, 0, 0, 30, 100, 50),                # devant (y plus petit)
        Placement(Colis("Q", 30, 70, 50, 10), 60, 150, 0, 30, 70, 50),                # derrière (y plus grand)
    ]
    msgs = [m for m in verifier(Resultat([pal], [], Parametres())) if m.code == "iata_acces"]
    assert len(msgs) == 1 and msgs[0].niveau == "erreur" and msgs[0].colis == ["M"]
    # sans Q, le colis reste visible côté droit : plus d'erreur
    pal.placements.pop()
    assert not [m for m in verifier(Resultat([pal], [], Parametres())) if m.code == "iata_acces"]


def test_verification_plan_valide_sans_erreur():
    colis = [Colis(f"c{i}", 60, 40, 40, 50) for i in range(12)]
    res = optimiser(colis, Parametres(duree_recherche=0.3))
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []


def test_dsatur_colorie_correctement():
    adj = {0: {1, 2}, 1: {0, 2}, 2: {0, 1}, 3: {0}}
    col = dsatur(adj)
    for u, vs in adj.items():
        for v in vs:
            assert col[u] != col[v]
    assert max(col.values()) == 2  # triangle : 3 couleurs


def test_coloriage_carte_voisins_differents():
    colis = [Colis(f"c{i}", 60, 40, 40, 50, classe="3" if i % 3 == 0 else "") for i in range(30)]
    res = optimiser(colis, Parametres(duree_recherche=0.3))
    for pal in res.palettes:
        for seulement_speciaux in (False, True):
            couleurs = coloriage_carte(pal, seulement_speciaux=seulement_speciaux)
            adj = graphe_contact(pal, seulement_speciaux=seulement_speciaux)
            for i, voisins in adj.items():
                for j in voisins:
                    a, b = pal.placements[i].colis.numero, pal.placements[j].colis.numero
                    assert couleurs[a] != couleurs[b]
            assert len(couleurs) == sum(1 for p in pal.placements if p.colis.special or not seulement_speciaux)


def test_positions_selon_passagers():
    assert avion.positions_disponibles(0) == 9
    assert avion.positions_disponibles(54) == 9
    assert avion.positions_disponibles(55) == 7
    assert avion.positions_disponibles(100) == 3
    assert avion.positions_disponibles(116) == 2
    assert avion.positions_disponibles(117) == 0


def test_bilan_masses():
    p = Parametres(passagers=10, duree_recherche=0.2)
    res = optimiser([Colis("a", 50, 50, 50, 100)], p)
    b = avion.bilan(res)
    assert b["masse_passagers"] == 10 * 130
    assert b["masse_totale_brute"] == 100 + 130 + 1300
    assert b["positions_disponibles"] == 9
