"""Parcours complet de l'interface dans Chromium (ignoré si Playwright ou le navigateur sont absents).

Si la variable d'environnement PALETTISEUR_CAPTURES désigne un dossier, les captures d'écran du README y sont écrites.
"""

import os
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from palettiseur.serveur import Serveur  # noqa: E402

CHROMIUM = "/opt/pw-browsers/chromium"


@pytest.fixture(scope="module")
def app():
    serveur = Serveur().demarrer()
    with sync_api.sync_playwright() as pw:
        options = {"args": ["--no-sandbox", "--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"]}
        if os.path.exists(CHROMIUM):
            options["executable_path"] = CHROMIUM
        try:
            navigateur = pw.chromium.launch(**options)
        except Exception as e:  # navigateur non installé
            serveur.arreter()
            pytest.skip(f"Chromium indisponible : {e}")
        page = navigateur.new_page(viewport={"width": 1480, "height": 920})
        page.on("dialog", lambda d: d.accept())
        yield page, serveur
        navigateur.close()
    serveur.arreter()


def test_parcours_complet(app):
    page, serveur = app
    dossier = os.environ.get("PALETTISEUR_CAPTURES")
    if dossier:
        Path(dossier).mkdir(parents=True, exist_ok=True)

    def capture(nom):
        if dossier:
            page.wait_for_timeout(400)
            page.screenshot(path=str(Path(dossier) / f"{nom}.png"))

    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(serveur.url)
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")

    # --- saisie manuelle d'un colis spécial
    page.fill("#f-longueur", "80"); page.fill("#f-largeur", "60"); page.fill("#f-hauteur", "50")
    page.fill("#f-masse", "120")
    page.select_option("#f-classe", "3")
    assert page.locator("#f-renversable").count() == 0                         # plus de notion de renversement
    page.select_option("#f-emballage", "carton")
    page.check("#f-fragile")
    page.fill("#f-commentaire", "Carburant")
    page.click("#btn-ajouter")
    colis = page.evaluate("window.palettiseur.etat.colis")
    assert len(colis) == 1 and colis[0]["classe"] == "3" and colis[0]["emballage"] == "carton" and colis[0]["fragile"] is True
    assert "renversable" not in colis[0]

    # --- numéro en double refusé par la validation de l'application
    page.fill("#f-numero", colis[0]["numero"])
    page.fill("#f-longueur", "10"); page.fill("#f-largeur", "10"); page.fill("#f-hauteur", "10"); page.fill("#f-masse", "5")
    page.click("#btn-ajouter")
    assert page.is_visible("#erreur-formulaire") and "existe déjà" in page.inner_text("#erreur-formulaire")
    assert len(page.evaluate("window.palettiseur.etat.colis")) == 1

    # --- exemple du notebook : 54 colis et ses paramètres
    page.select_option("#sel-exemple", "cdp")
    page.wait_for_function("window.palettiseur.etat.colis.length === 54")
    params = page.evaluate("window.palettiseur.etat.parametres")
    # l'exemple du notebook en reprend les paramètres : 274 x 224 cm sans couloir, ni règles propres à l'application
    assert params["moteur"] == "notebook" and params["derogation_couloir"] is True and params["iata_au_bord"] is False
    assert params["regrouper_par_dat"] is True and params["support_minimal"] == 0.8
    assert params["longueur_palette"] == 224 and params["largeur_palette"] == 274 and params["axe_cotes"] == "y"
    assert params["reduction_couloir"] == 14
    page.evaluate("window.palettiseur.etat.parametres.temps_limite = 12")           # budget de la VND (notebook : 180 s)

    # --- calcul
    page.click("#btn-optimiser")
    page.wait_for_function("window.palettiseur.etat.resultat !== null", timeout=120000)
    res = page.evaluate("({n: window.palettiseur.etat.resultat.palettes.length, err: window.palettiseur.etat.resultat.verification.filter(m => m.niveau === 'erreur').length, moteur: window.palettiseur.etat.resultat.moteur, h: window.palettiseur.etat.resultat.palettes.map(p => Math.round(p.hauteur)).sort()})")
    assert res["n"] in (2, 3) and res["err"] == 0 and res["moteur"] == "notebook"      # 3 palettes avant la VND, 2 après 30 s

    # --- étapes et journal
    assert page.locator("button.etape").count() == 3                       # heuristique, après la VND, solution finale
    assert page.locator("#liste-journal li").count() == 4
    assert "Cinq essais du notebook" in page.inner_text("#liste-journal")
    page.click("button.etape >> nth=0")
    assert page.evaluate("window.palettiseur.etat.etape") == "heuristique"
    assert page.locator("button.onglet").count() == 1 + len(page.evaluate("window.palettiseur.etat.resultat.etapes[0].palettes"))
    capture("etape_heuristique")
    page.click("button.etape >> nth=1")
    assert page.evaluate("window.palettiseur.etat.etape") == "apres_vnd"
    page.click("button.etape >> nth=2")
    assert page.evaluate("window.palettiseur.etat.etape") == "finale"
    capture("vue_ensemble")

    # --- palette 1 et modes de couleur
    page.click(".onglet[data-palette='1']")
    page.wait_for_timeout(500)
    for mode, nom in (("carte_tous", "carte_tous"), ("carte_speciaux", None), ("poids", "heat_map"), ("categories", None)):
        page.click(f"button.mode[data-mode='{mode}']")
        if nom:
            capture(nom)

    # --- clic sur un colis de la vue 3D
    point = page.evaluate("""() => {
      const v = window.palettiseur.vue();
      const b = [...v.boites.values()].find(x => x.placement.dz > 40);
      const pos = b.mesh.getWorldPosition(new b.mesh.position.constructor()); pos.project(v.camera);
      const r = v.renderer.domElement.getBoundingClientRect();
      const x = r.left + (pos.x + 1) / 2 * r.width, y = r.top + (1 - pos.y) / 2 * r.height;
      const hit = v.intersection({clientX: x, clientY: y});
      return {x, y, numero: hit ? hit.placement.colis.numero : null};
    }""")
    assert point["numero"] is not None
    page.mouse.click(point["x"], point["y"])
    page.wait_for_timeout(300)
    assert page.evaluate("window.palettiseur.etat.selection.numero") == point["numero"]
    texte = page.inner_text("#contenu-colis")
    assert point["numero"] in texte and "cm" in texte and "kg" in texte
    capture("palette_selection")

    if dossier:
        page.locator("#carte-journal").screenshot(path=str(Path(dossier) / "journal.png"))

    # --- colis dangereux : l'accès IATA est affiché
    page.evaluate("window.palettiseur.selectionnerColis('5487')")              # colis 5487 : classe IATA 3
    panneau = page.inner_text("#contenu-colis")
    assert "étiquette visible" in panneau and "couloir" in panneau and "Emballage" in panneau

    # --- fiche palette et paramètres
    page.click("#btn-fiche")
    assert page.locator("#contenu-fiche .page-fiche").count() == 1
    fiche = page.inner_text("#contenu-fiche")
    assert "CÔTÉ GAUCHE (y = 0)" in fiche and "CÔTÉ DROIT (y maximal)" in fiche and "Renversable" not in fiche   # pas de couloir
    page.evaluate("document.querySelector('#dlg-fiche').scrollTop = 0")
    capture("fiche_palette")
    page.click("#btn-fiche-fermer")
    page.click("#btn-parametres")
    assert page.is_visible("[data-param=temps_limite]") and page.is_visible("[data-param=iata_au_bord]")
    assert page.is_visible("[data-param=regrouper_par_dat]") and page.is_visible("[data-param=marge_hauteur]")
    assert page.locator("[data-param=temps_limite_scip]").count() == 0 and page.locator("[data-param=mode_notebook]").count() == 0
    assert [o.get_attribute("value") for o in page.locator("#select-moteur option").all()] == ["notebook", "heuristique"]
    assert "CDP_5" in page.inner_text("#aide-moteur")
    assert page.is_visible("[data-param=charge_max_carton]") and page.is_visible("[data-param=emballages_ecrasables]")
    assert page.is_visible("[data-param=reduction_couloir]") and page.is_visible("[data-param=axe_cotes]")
    assert "274 × 224" in page.inner_text("#aide-largeur-utile")
    page.select_option("[data-param=axe_cotes]", "x")
    page.uncheck("[data-param=derogation_couloir]")
    assert "246 × 224" in page.inner_text("#aide-largeur-utile")            # couloir sur l'axe x : 274 - 2 x 14
    page.select_option("[data-param=axe_cotes]", "y")
    assert "274 × 196" in page.inner_text("#aide-largeur-utile")            # couloir sur l'axe y : 224 - 2 x 14
    capture("parametres")
    page.click("#btn-params-annuler")

    # --- export du plan
    page.click("#btn-plan-csv")
    page.wait_for_selector("#info-export a")
    assert "plan.csv" in page.inner_text("#info-export")

    # --- import d'un manifeste Excel : résumé, puis remplacement de la liste
    page.set_input_files("#fichier-import", str(Path(__file__).resolve().parent.parent / "data" / "exemple_cargo.xlsx"))   # un seul bouton : format reconnu
    page.wait_for_selector("#dlg-import[open]")
    resume = page.inner_text("#contenu-import")
    assert "54" in resume and "14" in resume and "armes" in resume and "pièces de sécurité" in resume
    assert "5725" in resume and "température est ignorée" in resume
    capture("import_excel")
    page.click("#btn-import-remplacer")
    page.wait_for_function("window.palettiseur.etat.colis.length === 54")
    colis = page.evaluate("window.palettiseur.etat.colis")
    assert colis[0]["numero"] == "0836" and colis[0]["reference"].endswith("0836") and "renversable" not in colis[0]
    assert colis[0]["dat"] == "DAT12301531890"                           # « Cargo Nat Ref 1 » complet
    assert sum(c["classe"] == "ARME" for c in colis) == 14 and sum(c["classe"] == "SECU" for c in colis) == 10
    assert sum(c["emballage"] in ("carton", "fibre") for c in colis) == 28
    # paramètres par défaut de l'application (couloir de 14 cm, IATA sur le bord, cartons non écrasés)
    page.evaluate("window.palettiseur.etat.parametres = {...window.palettiseur.etat.config.parametres, temps_limite: 8}")
    page.evaluate("window.palettiseur.etat.resultat = null")           # le résultat précédent contient les mêmes numéros
    page.click("#btn-optimiser")
    page.wait_for_function("window.palettiseur.etat.resultat !== null", timeout=120000)
    res = page.evaluate("({err: window.palettiseur.etat.resultat.verification.filter(m => m.niveau === 'erreur').length, non: window.palettiseur.etat.resultat.non_places.length})")
    assert res == {"err": 0, "non": 0}
    page.click(".onglet[data-palette='ensemble']")
    capture("cargo_ensemble")
    # chaque colis IATA est posé contre un bord et visible
    iata = page.evaluate("""() => window.palettiseur.etat.resultat.palettes.flatMap(p => p.placements)
        .filter(q => window.palettiseur.etat.classes[q.colis.classe]?.iata).map(q => [q.acces_iata.length, q.bord_iata.length])""")
    assert len(iata) == 5 and all(a > 0 and b > 0 for a, b in iata)
    # détail d'un colis dangereux et de sa palette : bord, côté d'accès, couloir sur l'axe y du notebook
    page.evaluate("window.palettiseur.selectionnerColis('0836')")
    panneau = page.inner_text("#contenu-colis")
    assert "étiquette visible" in panneau and "posé contre le bord" in panneau and "N° de série" in panneau
    assert "DAT12301531890" in panneau
    info_palette = page.inner_text("#contenu-palette")
    assert "couloir de 14" in info_palette and "sur l'axe y" in info_palette and "274 × 196" in info_palette
    page.click("#btn-fiche")
    fiche = page.inner_text("#contenu-fiche")
    assert "COULOIR GAUCHE (y = 0)" in fiche and "COULOIR DROIT (y maximal)" in fiche      # couloir de 14 cm de chaque côté
    page.click("#btn-fiche-fermer")
    assert erreurs == []


def test_import_unifie_reconnait_le_format(app, tmp_path):
    """Un seul bouton « Importer… » : le .xlsx n'est plus lu comme du texte, un CSV Excel en Windows-1252 garde ses accents."""
    page, serveur = app
    page.on("dialog", lambda d: d.accept())
    page.goto(serveur.url)
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")
    assert page.locator("#btn-importer-excel").count() == 0 and page.locator("#fichier-excel").count() == 0
    page.evaluate("window.palettiseur.etat.colis = []")
    exemple = Path(__file__).resolve().parent.parent / "data" / "exemple_cargo.xlsx"

    # .xlsx dont l'extension a été changée : reconnu au contenu (signature « PK »), pas lu comme du texte
    copie = tmp_path / "manifeste.dat"
    copie.write_bytes(exemple.read_bytes())
    page.set_input_files("#fichier-import", str(copie))
    page.wait_for_selector("#dlg-import[open]")
    assert "54" in page.inner_text("#contenu-import")
    page.click("#btn-import-annuler")

    # CSV enregistré par Excel en français (Windows-1252, séparateur point-virgule)
    csv = tmp_path / "liste.csv"
    csv.write_bytes("numero;longueur;largeur;hauteur;masse;classe;emballage;commentaire\nA-1;80;60;50;12;;carton;Caisse à café\n".encode("cp1252"))
    page.set_input_files("#fichier-import", str(csv))
    page.wait_for_function("window.palettiseur.etat.colis.length === 1")
    c = page.evaluate("window.palettiseur.etat.colis[0]")
    assert c["numero"] == "A-1" and c["emballage"] == "carton" and c["commentaire"] == "Caisse à café"

    # ancien format .xls : message clair, aucune erreur technique
    xls = tmp_path / "ancien.xls"
    xls.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64)
    page.set_input_files("#fichier-import", str(xls))
    page.wait_for_selector("#notification:not([hidden])")
    assert "Enregistrer sous" in page.inner_text("#notification") and "xlsx" in page.inner_text("#notification")
