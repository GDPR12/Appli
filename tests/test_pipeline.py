"""Tests des phases du pipeline (fusion, tassement vertical, recentrage et tassement horizontal, zones) et du résultat complet."""

import json

import pytest

from palettiseur import api
from palettiseur import optimisation as opt
from palettiseur.iata import regles
from palettiseur.modeles import Colis, Parametres
from palettiseur.optimisation import _Boite, _Chargement, _charge_valide
from palettiseur.verification import verifier

R = regles()


def params(**kw):
    kw.setdefault("derogation_couloir", True)      # largeur 274 cm, plus simple à raisonner
    return Parametres(**kw)


def charge(p, *boites, zone="soute"):
    """Chargement 10k avec des boîtes (colis, x, y, z, dx, dy, dz)."""
    ch = _Chargement(p.types_palettes()[0], zone)
    for colis, x, y, z, dx, dy, dz in boites:
        ch.boites.append(_Boite(colis, x, y, z, dx, dy, dz))
    return ch


def colis(num, masse=50.0, classe="", l=50, w=50, h=50, **kw):
    return Colis(str(num), l, w, h, masse, classe=classe, **kw)


# --------------------------------------------------------------------------- validité

def test_charge_valide_detecte_les_violations():
    p = params()
    ok = charge(p, (colis(1, 100), 0, 0, 0, 50, 50, 50), (colis(2, 40), 0, 0, 50, 50, 50, 50))
    assert _charge_valide(ok, p, R)
    lourd_sur_leger = charge(p, (colis(1, 20), 0, 0, 0, 50, 50, 50), (colis(2, 40), 0, 0, 50, 50, 50, 50))
    assert not _charge_valide(lourd_sur_leger, p, R)
    sur_fragile = charge(p, (colis(1, 100, fragile=True), 0, 0, 0, 50, 50, 50), (colis(2, 40), 0, 0, 50, 50, 50, 50))
    assert not _charge_valide(sur_fragile, p, R)
    chevauche = charge(p, (colis(1), 0, 0, 0, 50, 50, 50), (colis(2), 25, 0, 0, 50, 50, 50))
    assert not _charge_valide(chevauche, p, R)
    incompatibles = charge(p, (colis(1, classe="3"), 0, 0, 0, 50, 50, 50), (colis(2, classe="5.1"), 60, 0, 0, 50, 50, 50))
    assert not _charge_valide(incompatibles, p, R)
    en_l_air = charge(p, (colis(1), 0, 0, 0, 50, 50, 50), (colis(2), 30, 0, 50, 50, 50, 50))   # 40 % de support
    assert not _charge_valide(en_l_air, p, R)


def test_un_colis_leger_ne_se_glisse_pas_sous_le_porte_a_faux_d_un_colis_lourd():
    """Un colis déjà posé en porte-à-faux (92 % de support) reposerait sur le nouveau colis s'il était inséré dessous."""
    p = params()
    support = colis(1, 500, l=100, w=100, h=50)
    lourd = colis(2, 300, l=100, w=100, h=50)
    ch = charge(p, (support, 0, 0, 0, 100, 100, 50), (lourd, 8, 0, 50, 100, 100, 50))
    assert _charge_valide(ch, p, R)
    leger = colis(3, 5, l=20, w=100, h=50)
    assert not opt._position_valide(ch, leger, 100, 0, 0, 20, 100, 50, p)          # sous le porte-à-faux
    assert opt._position_valide(ch, leger, 120, 0, 0, 20, 100, 50, p)              # à côté : sans danger
    lourd2 = colis(4, 600, l=20, w=100, h=50)
    assert opt._position_valide(ch, lourd2, 100, 0, 0, 20, 100, 50, p)             # assez lourd pour porter : autorisé


def test_construction_ne_cree_jamais_de_lourd_sur_leger_par_porte_a_faux():
    import random
    rng = random.Random(2)
    lot = [Colis(f"C{i}", rng.randint(25, 100), rng.randint(25, 80), rng.randint(15, 70), rng.randint(3, 120),
                 emballage=rng.choice(["", "bois", "carton"]), classe=rng.choice(["", "", "", "", "", "3", "5.1", "8", "ARME", "SECU"]),
                 fragile=rng.random() < 0.04) for i in range(300)]
    res = opt.optimiser(lot, Parametres(duree_recherche=1.0, graine=2, positions_soute=20))
    # 300 colis dépassent les 9 emplacements de l'avion : on ne juge ici que les règles de palettisation
    assert [m for m in verifier(res) if m.niveau == "erreur" and m.code not in ("positions", "capacite")] == []


# --------------------------------------------------------------------------- phase 3 : fusion

def test_fusion_vide_une_palette():
    p = params()
    a = charge(p, (colis(1, l=100, w=100, h=100), 0, 0, 0, 100, 100, 100))
    b = charge(p, (colis(2), 0, 0, 0, 50, 50, 50))
    chs = [a, b]
    stats = opt._fusionner(chs, p, R, 2.0)
    assert len(chs) == 1 and stats["fusions_reussies"] == 1 and stats["colis_transferes"] == 1
    assert len(chs[0].boites) == 2 and _charge_valide(chs[0], p, R)


def test_fusion_respecte_la_separation_iata():
    p = params()
    a = charge(p, (colis(1, classe="3"), 0, 0, 0, 50, 50, 50))
    b = charge(p, (colis(2, classe="5.1"), 0, 0, 0, 50, 50, 50))
    chs = [a, b]
    opt._fusionner(chs, p, R, 1.0)
    assert len(chs) == 2


def test_fusion_respecte_la_masse_maximale():
    p = params()
    a = charge(p, (colis(1, 4000), 0, 0, 0, 50, 50, 50))
    b = charge(p, (colis(2, 1000), 0, 0, 0, 50, 50, 50))     # 5 000 kg > 4 406 kg net
    chs = [a, b]
    opt._fusionner(chs, p, R, 1.0)
    assert len(chs) == 2


# --------------------------------------------------------------------------- phase 5 : recentrage et tassement horizontal

def test_tassement_rapproche_du_centre_sans_rien_casser():
    p = params()
    ch = charge(p, (colis(1, 100), 0, 0, 0, 50, 50, 50), (colis(2, 100), 50, 0, 0, 50, 50, 50),
                (colis(3, 100), 0, 50, 0, 50, 50, 50), (colis(4, 80), 0, 0, 50, 50, 50, 50))
    cm_avant = sum(b.colis.masse * (b.x1 + b.x2) / 2 for b in ch.boites) / ch.masse
    stats = opt._tasser([ch], p)
    assert _charge_valide(ch, p, R)
    cm_apres = sum(b.colis.masse * (b.x1 + b.x2) / 2 for b in ch.boites) / ch.masse
    assert abs(cm_apres - 137) < abs(cm_avant - 137) and stats["deplacement_total"] > 0


def test_tassement_ne_glisse_pas_un_colis_lourd_sur_un_plus_leger():
    p = params()
    base = colis(1, 500, l=100, w=100, h=50)
    leger = colis(2, 10, l=100, w=100, h=50)
    lourd = colis(3, 300, l=60, w=60, h=50)
    ch = charge(p, (base, 0, 0, 0, 100, 100, 50), (leger, 100, 0, 0, 100, 100, 50), (lourd, 20, 20, 50, 60, 60, 50))
    opt._tasser([ch], p)
    h = next(x for x in ch.boites if x.colis is lourd)
    ll = next(x for x in ch.boites if x.colis is leger)
    # le tas est ensuite translaté d'un bloc : on teste les positions relatives, pas absolues
    assert opt._aire_commune(h, ll) == 0.0 and _charge_valide(ch, p, R)


def poser(axe, colis_, s, t, z, ds, dt, dz):
    """Boîte dont ``s`` / ``ds`` sont la position et la taille sur l'axe des côtés, ``t`` / ``dt`` sur l'autre axe."""
    return (colis_, s, t, z, ds, dt, dz) if axe == "x" else (colis_, t, s, z, dt, ds, dz)


@pytest.mark.parametrize("axe", ["y", "x"])
@pytest.mark.parametrize("graine", range(4))
def test_tassement_ne_masque_jamais_un_colis_iata_des_deux_cotes(graine, axe):
    """Sans la règle du bord, un colis dangereux reste au moins visible depuis un côté après tassement."""
    import random
    rng = random.Random(graine)
    p = params(iata_au_bord=False, axe_cotes=axe)
    ch = _Chargement(p.types_palettes()[0], "soute")
    for k in range(9):                                       # grille de 3 x 3 colis, dangereux aux deux extrémités
        c = colis(k, 60 + rng.randint(0, 40), classe="3" if k in (0, 8) else "")
        _, x, y, z, dx, dy, dz = poser(axe, c, (k % 3) * 90, (k // 3) * 70, 0, 50, 50, 50)
        ch.boites.append(_Boite(c, x, y, z, dx, dy, dz))
    assert opt._iata_ok(ch.boites, p, ch.type)
    opt._tasser([ch], p)
    assert _charge_valide(ch, p, R)
    for b in ch.boites:
        if b.colis.classe:
            assert opt._cotes_visibles(b, ch.boites, axe), b.colis.numero


@pytest.mark.parametrize("axe", ["y", "x"])
def test_tassement_garde_un_colis_iata_contre_son_bord(axe):
    """Avec l'option « IATA sur le bord », le tassement ne décolle jamais un colis dangereux du bord."""
    p = params(axe_cotes=axe)
    etendue = p.types_palettes()[0].etendue_cotes
    iata = colis(1, 60, classe="3")
    autre = colis(2, 100, l=60, w=60, h=50)
    ch = _Chargement(p.types_palettes()[0], "soute")
    for c, x, y, z, dx, dy, dz in (poser(axe, autre, 100, 0, 0, 60, 60, 50), poser(axe, iata, 0, 100, 0, 50, 50, 50)):
        ch.boites.append(_Boite(c, x, y, z, dx, dy, dz))
    assert opt._iata_ok(ch.boites, p, ch.type)
    opt._tasser([ch], p)
    b = next(x for x in ch.boites if x.colis is iata)
    debut = b.x1 if axe == "x" else b.y1
    assert debut < 0.001 and _charge_valide(ch, p, R) and opt._iata_ok(ch.boites, p, ch.type)
    droite = _Chargement(p.types_palettes()[0], "soute")
    for c, x, y, z, dx, dy, dz in (poser(axe, autre, 100, 0, 0, 60, 60, 50),
                                   poser(axe, iata, etendue - 50, 100, 0, 50, 50, 50)):
        droite.boites.append(_Boite(c, x, y, z, dx, dy, dz))
    opt._tasser([droite], p)
    b = next(x for x in droite.boites if x.colis is iata)
    fin = b.x2 if axe == "x" else b.y2
    assert etendue - fin < 0.001 and opt._iata_ok(droite.boites, p, droite.type)


@pytest.mark.parametrize("axe", ["y", "x"])
def test_recentrage_conserve_les_positions_relatives(axe):
    p = params(axe_cotes=axe)
    iata = colis(1, 50, classe="3")
    ch = _Chargement(p.types_palettes()[0], "soute")
    for c, x, y, z, dx, dy, dz in (poser(axe, iata, 0, 10, 0, 50, 50, 50), poser(axe, colis(2, 400), 50, 10, 0, 50, 50, 50)):
        ch.boites.append(_Boite(c, x, y, z, dx, dy, dz))
    avant = [(b.x1 - ch.boites[0].x1, b.y1 - ch.boites[0].y1) for b in ch.boites]
    opt._recentrer([ch], p)
    assert [(b.x1 - ch.boites[0].x1, b.y1 - ch.boites[0].y1) for b in ch.boites] == avant
    assert (ch.boites[0].x1 if axe == "x" else ch.boites[0].y1) < 0.001      # le colis IATA reste contre le bord (à 5 µm près)
    assert opt._iata_ok(ch.boites, p, ch.type)


def test_tassement_n_isole_pas_un_colis():
    p = params()
    a, b, c = colis(1, 100), colis(2, 100), colis(3, 100)
    ch = charge(p, (a, 0, 0, 0, 50, 50, 50), (b, 50, 0, 0, 50, 50, 50), (c, 100, 0, 0, 50, 50, 50))
    opt._tasser([ch], p)
    assert not opt._isoles(ch.boites, p.jeu_contact) and _charge_valide(ch, p, R)


# --------------------------------------------------------------------------- phase 4 : tassement vertical

def test_tassement_vertical_abaisse_un_colis_perche():
    p = params()
    a = colis(1, 500, l=100, w=100, h=50)
    b = colis(2, 60, l=100, w=100, h=50)                       # posé sur a alors que le plateau est libre à côté
    ch = charge(p, (a, 0, 0, 0, 100, 100, 50), (b, 0, 0, 50, 100, 100, 50))
    stats = opt._tasser_vertical([ch], p, 5.0)
    assert stats["mouvements"] == 1 and stats["hauteur_avant"] == 100 and stats["hauteur_apres"] == 50
    assert next(x for x in ch.boites if x.colis is b).z1 == 0 and _charge_valide(ch, p, R)


def test_tassement_vertical_respecte_les_appuis_des_autres_colis():
    """Un colis qui en porte un autre sans solution de rechange ne peut pas être retiré."""
    p = params()
    socle = colis(1, 500, l=274, w=224, h=50)                 # plateau entièrement couvert
    b = colis(2, 100, l=100, w=100, h=30)
    c = colis(3, 50, l=100, w=100, h=30)                       # posé sur b, qui ne peut pas descendre plus bas
    ch = charge(p, (socle, 0, 0, 0, 274, 224, 50), (b, 0, 0, 50, 100, 100, 30), (c, 0, 0, 80, 100, 100, 30))
    stats = opt._tasser_vertical([ch], p, 5.0)
    assert _charge_valide(ch, p, R)
    assert all(x.z1 >= 50 - 1e-9 for x in ch.boites if x.colis is not socle)       # personne sous le niveau du socle
    assert stats["hauteur_apres"] <= stats["hauteur_avant"]


def test_tassement_vertical_ne_releve_jamais_un_sommet():
    import random
    rng = random.Random(7)
    lot = [Colis(f"C{i}", rng.randint(30, 100), rng.randint(30, 80), rng.randint(20, 60), rng.randint(5, 250),
                 emballage=rng.choice(["", "bois", "carton"]), classe=rng.choice(["", "", "", "3", "9", "2.2"])) for i in range(40)]
    res = opt.optimiser(lot, Parametres(duree_recherche=0.8, derogation_couloir=True))
    types = res.parametres.types_palettes()
    chs = []
    for pal in res.palettes:                                  # reconstitue des chargements modifiables
        ch = opt._Chargement(pal.type, pal.zone)
        for pl in pal.placements:
            ch.boites.append(opt._Boite(pl.colis, pl.x, pl.y, pl.z, pl.dx, pl.dy, pl.dz, pl.orientation))
        chs.append(ch)
    avant = {id(b.colis): (b.z1, b.z2) for ch in chs for b in ch.boites}
    opt._tasser_vertical(chs, res.parametres, 3.0)
    for ch in chs:
        assert _charge_valide(ch, res.parametres, R)
        for b in ch.boites:
            z1, z2 = avant[id(b.colis)]
            assert b.z1 <= z1 + 1e-9 and b.z2 <= z2 + 1e-9


def test_normaliser_zones_repasse_en_soute():
    p = params()
    ch = charge(p, (colis(1, 100), 0, 0, 0, 50, 50, 50), zone="rampe")
    chs = [ch]
    opt._normaliser_zones(chs, p)
    assert ch.zone == "soute" and ch.type.masse_max > p.masse_nette_max_rampe


# --------------------------------------------------------------------------- pipeline complet

def test_resultat_contient_etapes_et_journal():
    res = opt.optimiser([colis(i, 30, l=60, w=40, h=40) for i in range(10)], params(duree_recherche=1.0))
    assert [e.cle for e in res.etapes] == ["heuristique", "finale"]
    assert [j["phase"] for j in res.journal] == [1, 2, 3, 4, 5]
    assert [j["titre"] for j in res.journal][3:] == ["Tassement vertical", "Recentrage et tassement horizontal"]
    assert res.journal[1]["ignoree"] is True              # pas de SCIP avec le moteur heuristique
    assert "hauteur_globale" in res.journal[-1]
    assert all("texte" in j and j["duree"] >= 0 for j in res.journal)
    assert res.etapes[-1].palettes == res.palettes


def test_exemple_cdp_reproduit_le_notebook():
    """Mode notebook : avec les paramètres du notebook, l'application donne exactement ses résultats (CDP_4, 54 colis).

    Valeurs lues dans la sortie du notebook exécuté : 4 ordres de la phase 1 (2 palettes 443 cm, 2 palettes 329 cm,
    3 palettes 363 cm, 3 palettes 346 cm ; 220 116 positions testées), 14 colis déplacés par la fusion, 12 passages et
    12,584 m de tassement horizontal, et finalement 2 palettes de 218 et 141 cm (somme 359 cm).
    """
    ex = api.exemple("cdp")
    assert len(ex["colis"]) == 54 and sum(c["masse"] for c in ex["colis"]) == 2290
    p = Parametres.depuis_dict(ex["parametres"])
    assert p.mode_notebook and p.derogation_couloir and not p.iata_au_bord
    p.temps_limite_scip = 12          # le notebook utilise 180 s ; la fusion a besoin de 5 s, la phase 4 ne trouve rien
    res = opt.optimiser([Colis.depuis_dict(c) for c in ex["colis"]], p)
    assert res.moteur == "notebook" and len(res.palettes) == 2 and res.positions_utilisees == 2
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []
    assert all(abs(pl.dz - pl.colis.hauteur) < 1e-6 for pal in res.palettes for pl in pal.placements)
    phase1, phase3, phase4, phase5 = res.journal[0], res.journal[2], res.journal[3], res.journal[4]
    assert "ordre 1 → 2 palette(s), 443 cm ; ordre 2 → 2 palette(s), 329 cm ; ordre 3 → 3 palette(s), 363 cm ; " \
           "ordre 4 → 3 palette(s), 346 cm" in phase1["texte"] and phase1["positions_testees"] == 220116
    assert [round(e.somme_hauteurs if hasattr(e, "somme_hauteurs") else sum(q.hauteur for q in e.palettes))
            for e in res.etapes[:1]] == [329]
    assert phase3["colis_transferes"] == 14
    assert phase4["mouvements"] == 0 and phase4["hauteur_avant"] == pytest.approx(218.0)
    assert "12 passage(s), 12.584 m" in phase5["texte"]
    assert sorted(round(q.hauteur) for q in res.palettes) == [141, 218]
    assert round(sum(q.hauteur for q in res.palettes)) == 359


def test_mode_notebook_laisse_les_accroches_inactives():
    """Les règles de l'application ne restent pas branchées sur le code du notebook après un calcul."""
    from palettiseur import notebook_cdp
    opt.optimiser([colis(i, 30, l=60, w=40, h=40) for i in range(6)],
                  Parametres(duree_recherche=0.5, mode_notebook=True, temps_limite_scip=2))
    assert notebook_cdp.HOOKS.regles is None and notebook_cdp.HOOKS.incompatibles is None


@pytest.mark.parametrize("axe", ["y", "x"])
def test_mode_notebook_applique_les_regles_de_l_application(axe):
    """Avec le code du notebook, le plan respecte aussi les cartons, le lourd sur léger, les armes et le bord IATA."""
    import random
    from palettiseur.geometrie import cotes_bord, cotes_visibles
    from palettiseur.iata import classe as classe_de
    rng = random.Random(4)
    lot = [Colis(f"C{i}", rng.randint(30, 90), rng.randint(30, 70), rng.randint(20, 60), rng.randint(5, 150),
                 emballage=rng.choice(["", "bois", "carton"]), classe=rng.choice(["", "", "", "3", "9", "ARME", "SECU"]),
                 fragile=rng.random() < 0.05) for i in range(26)]
    prm = Parametres(duree_recherche=1.0, mode_notebook=True, temps_limite_scip=3, axe_cotes=axe)
    res = opt.optimiser(lot, prm)
    assert res.moteur == "notebook" and not res.non_places
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []
    for pal in res.palettes:
        for p in pal.placements:
            if classe_de(p.colis.classe).iata:
                assert set(cotes_visibles(p, pal.placements, axe)) & set(
                    cotes_bord(p, pal.type.etendue_cotes, prm.jeu_contact, axe)), p.colis.numero


def test_mode_notebook_cede_la_place_au_moteur_de_l_application():
    """Le notebook ne connaît pas la BIPAL : un colis de 3 m fait passer le calcul sur le moteur de l'application."""
    lot = [Colis("long", 320, 80, 60, 400), Colis("petit", 50, 50, 50, 30)]
    res = opt.optimiser(lot, Parametres(duree_recherche=0.5, mode_notebook=True))
    assert res.moteur == "heuristique" and res.palettes[0].type.code == "BIPAL" and not res.non_places
    assert "Mode notebook indisponible" in res.journal[0]["texte"] and "BIPAL" in res.journal[0]["texte"]


def test_mode_notebook_en_axe_x_est_le_miroir_du_notebook():
    """Axe x : l'application transpose le problème (x <-> y) avant d'appeler le notebook, qui garde ses côtés sur y."""
    ex = api.exemple("cdp")
    lot = [Colis.depuis_dict(c) for c in ex["colis"]]
    base = dict(derogation_couloir=True, iata_au_bord=False, facteur_masse=1000.0, emballages_ecrasables="",
                tare_palette=0, mode_notebook=True, temps_limite_scip=1, duree_recherche=1)
    suite = opt._placement_heuristique  # noqa: F841 - le calcul complet est trop long : on compare la phase 1
    from palettiseur import notebook_cdp
    a_placer = lambda p: [(c, p.types_palettes()[0]) for c in lot]  # noqa: E731
    px = Parametres(axe_cotes="x", **base)
    py = Parametres(axe_cotes="y", **base)
    ctx_x = notebook_cdp.Contexte(a_placer(px), px, regles())
    assert ctx_x.palettes_nb[0]["L"] == pytest.approx(2.24) and ctx_x.palettes_nb[0]["W"] == pytest.approx(2.74)
    ctx_y = notebook_cdp.Contexte(a_placer(py), py, regles())
    assert ctx_y.palettes_nb[0]["L"] == pytest.approx(2.74) and ctx_y.palettes_nb[0]["W"] == pytest.approx(2.24)
    # un aller-retour de conversion conserve les positions
    chs = ctx_x.vers_chargements({"palettes": [{"id": 1, "colis": [
        {"id": 1, "m": 15, "iata": "9", "x": 0.4, "y": -0.3, "z": 0.075, "l_place": 0.45, "w_place": 0.3, "h_place": 0.15}]}]})
    b = chs[0].boites[0]
    assert (round(b.x1, 6), round(b.y1, 6), round(b.dx, 6), round(b.dy, 6)) == (92, 129.5, 30, 45)
    retour = ctx_x.depuis_chargements(chs)["palettes"][0]["colis"][0]
    assert (round(retour["x"], 6), round(retour["y"], 6)) == (0.4, -0.3)


def test_resultat_json_avec_etapes():
    ex = api.exemple("iata")
    d = api.lancer_optimisation(ex["colis"], {"duree_recherche": 1.0})
    json.dumps(d)
    assert [e["cle"] for e in d["etapes"]] == ["heuristique", "finale"]
    assert "palettes" in d["etapes"][0] and "palettes" not in d["etapes"][-1]
    assert d["etapes"][0]["bilan"]["colis_places"] == d["bilan"]["colis_places"]
    assert len(d["journal"]) == 5
    assert "hauteur_globale" in d["bilan"]


@pytest.mark.parametrize("graine", [0, 1, 2, 3])
def test_pipeline_aleatoire_toujours_valide(graine):
    import random
    rng = random.Random(graine)
    lot = []
    for i in range(35):
        lot.append(Colis(f"C{i}", rng.randint(30, 110), rng.randint(30, 90), rng.randint(20, 80), rng.randint(5, 300),
                         emballage=rng.choice(["", "bois", "carton", "fibre"]),
                         classe=rng.choice(["", "", "", "3", "1", "5.1", "2.2", "8", "9L", "ARME", "SECU"]),
                         fragile=rng.random() < 0.06))
    res = opt.optimiser(lot, Parametres(duree_recherche=1.0, graine=graine))
    assert not res.non_places
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []
    for e in res.etapes:     # chaque état intermédiaire est lui aussi valide
        from palettiseur.modeles import Resultat
        assert [m for m in verifier(Resultat(e.palettes, [], res.parametres)) if m.niveau == "erreur" and m.code not in ("positions", "capacite")] == []
