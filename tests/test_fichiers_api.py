import json

from palettiseur import api, fichiers
from palettiseur.modeles import Colis


def test_csv_aller_retour():
    colis = [Colis("A-1", 80, 60, 50, 120.5, renversable=False, classe="3", commentaire="jerricans ; x", fragile=True)]
    texte = fichiers.ecrire_csv(colis)
    lus, avert = fichiers.lire_csv(texte)
    assert avert == []
    assert lus[0] == colis[0]


def test_csv_separateur_virgule_et_alias():
    texte = "Référence,Long,Larg,Haut,Poids,Type\nX1,80,60,50,\"12,5\",1\nX2,0,60,50,10,\n"
    lus, avert = fichiers.lire_csv(texte)
    assert len(lus) == 1 and lus[0].masse == 12.5 and lus[0].classe == "1"
    assert len(avert) == 1  # ligne X2 invalide


def test_csv_colonnes_manquantes():
    lus, avert = fichiers.lire_csv("a;b\n1;2\n")
    assert lus == [] and "introuvables" in avert[0]


def test_import_json_projet():
    rep = api.importer_texte(json.dumps({"colis": [{"numero": "J", "longueur": 10, "largeur": 10, "hauteur": 10, "masse": 1}],
                                         "parametres": {"passagers": 12}}))
    assert rep["colis"][0]["numero"] == "J" and rep["parametres"]["passagers"] == 12


def test_quantite_developpee_et_resultat_json():
    liste = [{"numero": "Q", "longueur": 40, "largeur": 40, "hauteur": 40, "masse": 10, "quantite": 3}]
    colis = api.colis_depuis_liste(liste)
    assert [c.numero for c in colis] == ["Q-1", "Q-2", "Q-3"]
    res = api.lancer_optimisation(liste, {"duree_recherche": 0.3})
    json.dumps(res)  # sérialisable
    assert res["bilan"]["colis_places"] == 3
    assert res["palettes"][0]["carte_tous"]
    assert res["heat_map"] == {"min": 10, "max": 10}


def test_configuration():
    cfg = api.configuration()
    assert any(c["code"] == "3" for c in cfg["classes"])
    assert "1" in cfg["incompatibilites"]["3"]
    assert cfg["parametres"]["largeur_palette"] == 274
    assert cfg["moteurs"][0]["code"] == "heuristique"
