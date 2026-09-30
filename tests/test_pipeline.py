"""Tests des phases du pipeline (fusion, recentrage, tassement, rééquilibrage, zones) et du résultat complet."""

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
                 renversable=rng.random() < 0.5, classe=rng.choice(["", "", "", "", "", "3", "5.1", "8", "ARME", "SECU"]),
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


# --------------------------------------------------------------------------- phases 4 et 6

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


def test_recentrage_ne_decolle_pas_un_colis_iata():
    p = params()
    iata = colis(1, 50, classe="3")
    ch = charge(p, (iata, 0, 10, 0, 50, 50, 50), (colis(2, 400), 50, 10, 0, 50, 50, 50))
    opt._recentrer([ch], p)
    b = next(x for x in ch.boites if x.colis is iata)
    assert b.x1 <= p.jeu_contact + 1e-9
    opt._tasser([ch], p)
    assert b.x1 <= p.jeu_contact + 1e-9 and _charge_valide(ch, p, R)


# --------------------------------------------------------------------------- phase 5 : rééquilibrage

def test_delestage_abaisse_la_somme_des_hauteurs():
    p = params()
    a = charge(p, (colis(1, 100), 0, 0, 0, 100, 100, 50), (colis(2, 20), 0, 0, 50, 100, 100, 50))   # 100 cm
    b = charge(p, (colis(3, 100), 0, 0, 0, 100, 100, 30))                                            # 30 cm
    chs = [a, b]
    avant = sum(c.hauteur for c in chs)
    stats = opt._delester(chs, p, R, 2.0)
    apres = sum(c.hauteur for c in chs)
    assert apres < avant and stats["colis_deplaces"] >= 1
    assert all(_charge_valide(c, p, R) for c in chs)
    assert sum(len(c.boites) for c in chs) == 3          # aucun colis perdu (la palette vidée disparaît)


def test_reduire_hauteurs_ne_degrade_jamais():
    p = params(duree_recherche=1.0)
    lot = [colis(i, 10 + i, l=40 + 5 * (i % 4), w=40, h=30 + 5 * (i % 3)) for i in range(14)]
    res = opt.optimiser(lot, p)
    assert len(res.palettes) >= 1
    assert res.journal[4]["titre"] == "Minimisation des hauteurs"
    for b in res.journal[4]["bilans"]:
        assert b["apres"] <= b["avant"] + 1e-9


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
    assert [j["phase"] for j in res.journal] == [1, 2, 3, 4, 5, 6]
    assert res.journal[1]["ignoree"] is True              # pas de SCIP avec le moteur heuristique
    assert all("texte" in j and j["duree"] >= 0 for j in res.journal)
    assert res.etapes[-1].palettes == res.palettes


def test_exemple_cdp_deux_palettes_sans_erreur():
    ex = api.exemple("cdp")
    assert len(ex["colis"]) == 54 and sum(c["masse"] for c in ex["colis"]) == 2290
    p = Parametres.depuis_dict(ex["parametres"])
    p.duree_recherche = 3.0
    res = opt.optimiser([Colis.depuis_dict(c) for c in ex["colis"]], p)
    assert len(res.palettes) == 2 and res.positions_utilisees == 2
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []
    assert all(not pl.colis.renversable and pl.dz == pl.colis.hauteur for pal in res.palettes for pl in pal.placements)


def test_resultat_json_avec_etapes():
    ex = api.exemple("iata")
    d = api.lancer_optimisation(ex["colis"], {"duree_recherche": 1.0})
    json.dumps(d)
    assert [e["cle"] for e in d["etapes"]] == ["heuristique", "finale"]
    assert "palettes" in d["etapes"][0] and "palettes" not in d["etapes"][-1]
    assert d["etapes"][0]["bilan"]["colis_places"] == d["bilan"]["colis_places"]
    assert len(d["journal"]) == 6


@pytest.mark.parametrize("graine", [0, 1, 2, 3])
def test_pipeline_aleatoire_toujours_valide(graine):
    import random
    rng = random.Random(graine)
    lot = []
    for i in range(35):
        lot.append(Colis(f"C{i}", rng.randint(30, 110), rng.randint(30, 90), rng.randint(20, 80), rng.randint(5, 300),
                         renversable=rng.random() < 0.6,
                         classe=rng.choice(["", "", "", "3", "1", "5.1", "2.2", "8", "9L", "ARME", "SECU"]),
                         fragile=rng.random() < 0.06))
    res = opt.optimiser(lot, Parametres(duree_recherche=1.0, graine=graine))
    assert not res.non_places
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []
    for e in res.etapes:     # chaque état intermédiaire est lui aussi valide
        from palettiseur.modeles import Resultat
        assert [m for m in verifier(Resultat(e.palettes, [], res.parametres)) if m.niveau == "erreur" and m.code not in ("positions", "capacite")] == []
