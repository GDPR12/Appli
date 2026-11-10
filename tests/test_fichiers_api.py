import json

from palettiseur import api, fichiers
from palettiseur.modeles import Colis


def test_csv_aller_retour():
    colis = [Colis("A-1", 80, 60, 50, 120.5, classe="3", commentaire="jerricans ; x", fragile=True, emballage="carton", reference="1234-5678", dat="DAT123")]
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
    res = api.lancer_optimisation(liste, {"moteur": "heuristique", "duree_recherche": 0.3})
    json.dumps(res)  # sérialisable
    assert res["bilan"]["colis_places"] == 3
    assert res["palettes"][0]["carte_tous"]
    assert res["heat_map"] == {"min": 10, "max": 10}


def test_configuration():
    cfg = api.configuration()
    assert any(c["code"] == "3" for c in cfg["classes"])
    assert "1" in cfg["incompatibilites"]["3"]
    assert cfg["parametres"]["largeur_palette"] == 274
    assert cfg["parametres"]["longueur_palette"] == 224
    assert cfg["parametres"]["reduction_couloir"] == 14
    assert cfg["parametres"]["iata_au_bord"] is True
    assert {"carton", "fibre", "bois"} <= {e["code"] for e in cfg["emballages"]}
    assert [e["cle"] for e in cfg["exemples"]] == ["cargo", "cdp", "iata"]
    assert [m["code"] for m in cfg["moteurs"]] == ["notebook", "heuristique"]      # plus de SCIP
    assert cfg["parametres"]["moteur"] == "notebook" and cfg["parametres"]["support_minimal"] == 0.8
    assert cfg["parametres"]["regrouper_par_dat"] is True and cfg["parametres"]["marge_hauteur"] == 5
    assert cfg["parametres"]["temps_limite"] == 60
    assert next(c for c in cfg["classes"] if c["code"] == "ARME")["visible"] is True        # catégorie A du notebook
    assert next(c for c in cfg["classes"] if c["code"] == "ARME")["iata"] is False
