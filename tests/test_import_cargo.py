"""Import du manifeste Excel (format CARGO), lecteur .xlsx sans dépendance et table des emballages."""

import base64
import collections
import zipfile

import pytest

from palettiseur import api, import_cargo
from palettiseur.emballages import emballages
from palettiseur.lecteur_xlsx import ErreurXlsx, lire_classeur
from palettiseur.modeles import Colis
from palettiseur.ressources import chemin_donnees

openpyxl = pytest.importorskip("openpyxl")

EN_TETE = ['ATR #', 'Departure', 'Arrival', 'UN/ID No', 'Description', 'Class/Div', 'Sub Haz', 'Packing Grp', 'Quantity',
           'Type of Packing', 'Proper Shipping Name', 'Packing Instr.', 'NetQty p.p kg/L', 'NEQ p.p. (Kg)', 'FBI', 'CAO',
           'Weight (Kg)', 'Length (cm)', 'Width (cm)', 'Height (cm)', 'Cargo Nat Ref 1', 'Cargo Nat Ref 2']


def ligne(serie, nom, emballage="Box, Wood", classe="", masse=10, dims=(40, 30, 20), onu="", sous_risque="", quantite="1"):
    return ['FRA 1', 'LFOJ', 'DIAP', onu, '', classe, sous_risque, '', quantite, emballage, nom, '', '', '', None, 'N',
            masse, *dims, 'DAT1', serie]


def classeur(tmp_path, lignes, en_tete=EN_TETE, nom="manifeste.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(en_tete)
    for l in lignes:
        ws.append(l)
    chemin = tmp_path / nom
    wb.save(chemin)
    return chemin


# --------------------------------------------------------------------------- lecteur .xlsx

def test_lecteur_xlsx_identique_a_openpyxl():
    chemin = chemin_donnees("exemple_cargo.xlsx")
    lu = lire_classeur(chemin.read_bytes())
    ws = openpyxl.load_workbook(chemin, data_only=True).active
    attendu = [list(r) for r in ws.iter_rows(values_only=True)]
    obtenu = lu[ws.title]
    assert len(obtenu) == len(attendu)
    for a, b in zip(attendu, obtenu):
        b = list(b) + [None] * (len(a) - len(b))
        assert [("" if v is None else v) for v in a] == [("" if v is None else v) for v in b[:len(a)]]


def test_lecteur_xlsx_refuse_un_fichier_qui_n_est_pas_un_classeur(tmp_path):
    with pytest.raises(ErreurXlsx):
        lire_classeur(b"ceci n'est pas un classeur")
    faux = tmp_path / "faux.zip"
    with zipfile.ZipFile(faux, "w") as z:
        z.writestr("hello.txt", "bonjour")
    with pytest.raises(ErreurXlsx):
        lire_classeur(faux.read_bytes())


def test_lecteur_xlsx_refuse_les_entites_xml(tmp_path):
    chemin = classeur(tmp_path, [ligne("1234", "x")])
    piege = tmp_path / "piege.xlsx"
    with zipfile.ZipFile(chemin) as src, zipfile.ZipFile(piege, "w") as dst:
        for nom in src.namelist():
            contenu = src.read(nom)
            if nom == "xl/worksheets/sheet1.xml":
                corps = contenu.split(b"?>", 1)[1] if contenu.lstrip().startswith(b"<?xml") else contenu
                contenu = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boum">]>' + corps
            dst.writestr(nom, contenu)
    with pytest.raises(ErreurXlsx):
        lire_classeur(piege.read_bytes())


# --------------------------------------------------------------------------- manifeste d'exemple

@pytest.fixture(scope="module")
def exemple():
    return import_cargo.lire_fichier(chemin_donnees("exemple_cargo.xlsx"))


def test_exemple_54_colis_numeros_a_quatre_chiffres(exemple):
    colis = exemple["colis"]
    assert len(colis) == 54
    numeros = [c["numero"] for c in colis]
    assert len(set(numeros)) == 54
    for c in colis:
        assert len(c["numero"]) == 4 and c["numero"].isdigit()
        assert c["reference"].endswith(c["numero"]) and len(c["reference"]) == 18        # numéro de série complet conservé
    assert numeros[0] == "0836" and colis[0]["reference"] == "636058800103070836"


def test_exemple_reconnait_les_armes_et_les_pieces_de_securite(exemple):
    compte = collections.Counter(c["classe"] for c in exemple["colis"])
    assert compte["ARME"] == 14 and compte["SECU"] == 10
    for c in exemple["colis"]:
        if c["classe"] == "ARME":
            assert "SECURITY" not in c["commentaire"].upper()
        if "WEAPONRY SECURITY PARTS" in c["commentaire"]:
            assert c["classe"] == "SECU"               # « WEAPONRY » ne doit pas faire passer une pièce de sécurité pour une arme
    assert exemple["resume"]["armes"] == 14 and exemple["resume"]["pieces_securite"] == 10


def test_exemple_matieres_dangereuses_et_emballages(exemple):
    dangereux = sorted(c["classe"] for c in exemple["colis"] if c["classe"] not in ("", "ARME", "SECU"))
    assert dangereux == ["2.2", "3", "3", "3", "9"]
    assert exemple["resume"]["dangereux"] == 5
    emballage = collections.Counter(c["emballage"] for c in exemple["colis"])
    assert emballage["carton"] == 10 and emballage["fibre"] == 18 and emballage["bois"] == 13
    assert not any("renversable" in c for c in exemple["colis"])
    assert all(c["fragile"] is False for c in exemple["colis"])          # jamais déduit du fichier


def test_exemple_colis_sans_masse_remplace_et_signale(exemple):
    zero = [c for c in exemple["colis"] if c["numero"] == "5725"]
    assert zero and zero[0]["masse"] == 0.1
    assert len(exemple["avertissements"]) == 1 and "5725" in exemple["avertissements"][0]


def test_exemple_se_charge_comme_des_colis_valides(exemple):
    colis = [Colis.depuis_dict(c) for c in exemple["colis"]]
    assert len(colis) == 54 and all(c.masse > 0 for c in colis)
    assert sum(1 for c in colis if c.emballage == "carton") == 10


# --------------------------------------------------------------------------- cas particuliers

def test_la_temperature_est_ignoree(tmp_path):
    chemin = classeur(tmp_path, [ligne("1111", "Frozen meat keep at -18 C, temperature controlled +2/+8", "Box, Cardboard")])
    r = import_cargo.lire_fichier(chemin)
    assert r["colis"][0]["classe"] == "" and r["avertissements"] == []
    assert not any(k in r["colis"][0] for k in ("temperature", "renversable"))


def test_numero_de_serie_quatre_derniers_chiffres_et_doublons(tmp_path):
    chemin = classeur(tmp_path, [ligne("636058800103070836", "A"), ligne("DAT-99-0836", "B"), ligne("12", "C")])
    r = import_cargo.lire_fichier(chemin)
    assert [c["numero"] for c in r["colis"]] == ["0836", "0836-2", "12"]
    assert [c["reference"] for c in r["colis"]] == ["636058800103070836", "DAT-99-0836", "12"]
    assert any("déjà utilisé" in a for a in r["avertissements"])


def test_classes_armes_depuis_la_designation_et_priorite_a_la_classe_du_manifeste(tmp_path):
    chemin = classeur(tmp_path, [
        ligne("0001", "WEAPONS - NO DG"), ligne("0002", "WEAPONRY SECURITY PARTS - NO DG"),
        ligne("0003", "Pièces de sécurité armement"), ligne("0004", "Munitions", classe="1.4S"),
        ligne("0005", "WEAPONS", classe="9")])
    classes = [c["classe"] for c in import_cargo.lire_fichier(chemin)["colis"]]
    assert classes == ["ARME", "SECU", "SECU", "1.4S", "9"]


def test_emballage_depuis_le_manifeste(tmp_path):
    types = ["Box, Cardboard", "Box, Fibreboard", "Box, Wood", "Box, Steel", "Box, Plastic", "Pallet", "Jerrican, Plastic", ""]
    chemin = classeur(tmp_path, [ligne(f"{i:04d}", "x", t) for i, t in enumerate(types)])
    codes = [c["emballage"] for c in import_cargo.lire_fichier(chemin)["colis"]]
    assert codes[:6] == ["carton", "fibre", "bois", "metal", "plastique", "palette"]
    assert codes[7] == ""


def test_colonnes_dans_un_autre_ordre(tmp_path):
    en_tete = ["Length (cm)", "Width (cm)", "Height (cm)", "Weight (Kg)", "Proper Shipping Name", "Type of Packing", "Serie"]
    chemin = classeur(tmp_path, [[80, 60, 50, 120, "Moteur", "Box, Wood", "AB-5678"]], en_tete=en_tete)
    c = import_cargo.lire_fichier(chemin)["colis"][0]
    assert (c["longueur"], c["largeur"], c["hauteur"], c["masse"], c["numero"]) == (80, 60, 50, 120, "5678")


def test_ligne_sans_dimensions_ignoree_et_colonnes_manquantes(tmp_path):
    r = import_cargo.lire_fichier(classeur(tmp_path, [ligne("0001", "ok"), ligne("0002", "sans dimension", dims=(0, 0, 0))]))
    assert len(r["colis"]) == 1 and r["resume"]["lignes_ignorees"] == 1
    with pytest.raises(ErreurXlsx, match="colonnes introuvables"):
        import_cargo.lire_fichier(classeur(tmp_path, [[1, 2]], en_tete=["a", "b"], nom="autre.xlsx"))


# --------------------------------------------------------------------------- API

def test_api_importer_cargo():
    contenu = base64.b64encode(chemin_donnees("exemple_cargo.xlsx").read_bytes()).decode()
    rep = api.importer_cargo("CARGO.xlsx", contenu)
    assert len(rep["colis"]) == 54 and rep["resume"]["armes"] == 14 and rep["parametres"] is None
    with pytest.raises(ValueError, match="format non pris en charge"):
        api.importer_cargo("CARGO.csv", contenu)
    with pytest.raises(ValueError):
        api.importer_cargo("CARGO.xlsx", "@@@pas du base64@@@")
    with pytest.raises(ValueError):
        api.importer_cargo("CARGO.xlsx", base64.b64encode(b"pas un classeur").decode())


def test_api_exemple_cargo():
    ex = api.exemple("cargo")
    assert len(ex["colis"]) == 54 and ex["avertissements"] and ex["resume"]["colis"] == 54


# --------------------------------------------------------------------------- table des emballages

def test_table_des_emballages():
    e = emballages()
    assert e.code_depuis_texte("Box, Cardboard") == "carton"
    assert e.code_depuis_texte("Box, Fibreboard") == "fibre"
    assert e.code_depuis_texte("BOX, WOOD") == "bois"
    assert e.code_depuis_texte("Caisse en bois") == "bois"
    assert e.code_depuis_texte("Carton ondulé") == "carton"
    assert e.code_depuis_texte("inconnu") == ""
    assert e.ecrasable("carton") and e.ecrasable("fibre") and not e.ecrasable("bois") and not e.ecrasable("")
    assert sorted(e.codes_ecrasables()) == ["carton", "fibre"]


def test_un_classeur_envoye_a_l_import_texte_donne_un_message_clair():
    """Le .xlsx envoyé par erreur à l'import CSV/JSON ne doit plus produire « new-line character seen in unquoted field »."""
    brut = chemin_donnees("exemple_cargo.xlsx").read_bytes().decode("latin-1")
    with pytest.raises(ValueError, match="binaire"):
        api.importer_texte(brut)
    with pytest.raises(ValueError, match="JSON invalide"):
        api.importer_texte("{pas du json")
    with pytest.raises(ValueError, match="illisible"):
        api.importer_texte("a;b\rc;d\n")        # retour chariot isolé au milieu d'une ligne
