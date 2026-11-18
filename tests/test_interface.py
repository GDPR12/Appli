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


def texte_svg(page, selecteur):
    """Texte d'un élément SVG (``inner_text`` ne les accepte pas), espaces insécables ramenés à des espaces simples."""
    return " ".join(page.locator(selecteur).first.text_content().split())


def capturer(page, nom, zone=None):
    """Capture d'écran du README si PALETTISEUR_CAPTURES désigne un dossier."""
    dossier = os.environ.get("PALETTISEUR_CAPTURES")
    if dossier:
        Path(dossier).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(400)
        (zone or page).screenshot(path=str(Path(dossier) / f"{nom}.png"))


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
    assert params["moteur"] == "notebook" and params["reduction_couloir"] == 0 and params["iata_au_bord"] is False
    assert params["regrouper_par_dat"] is True and params["support_minimal"] == 0.8 and params["temps_limite"] == 180
    assert params["longueur_palette"] == 224 and params["largeur_palette"] == 274 and params["axe_cotes"] == "y"
    assert params["masse_max_soute"] == 4536 and params["masse_max_rampe"] == 3000 and params["facteur_masse"] == 0
    page.evaluate("window.palettiseur.etat.parametres.temps_limite = 12")           # budget de la VND (notebook : 180 s)

    # --- calcul : l'avancement s'affiche pendant le calcul, qui tourne en arrière-plan
    page.click("#btn-optimiser")
    page.wait_for_selector("#chargement:not([hidden])")
    page.wait_for_function("document.querySelector('#chargement-phase').textContent.includes('Phase')", timeout=20000)
    assert page.is_disabled("#btn-optimiser")
    page.wait_for_function("window.palettiseur.etat.resultat !== null", timeout=120000)
    res = page.evaluate("({n: window.palettiseur.etat.resultat.palettes.length, err: window.palettiseur.etat.resultat.verification.filter(m => m.niveau === 'erreur').length, moteur: window.palettiseur.etat.resultat.moteur, h: window.palettiseur.etat.resultat.palettes.map(p => Math.round(p.hauteur)).sort()})")
    assert res["n"] in (2, 3) and res["err"] == 0 and res["moteur"] == "notebook"      # 3 palettes avant la VND, 2 après 30 s
    assert page.is_hidden("#chargement") and page.is_enabled("#btn-optimiser") and page.is_hidden("#bilan-perime")

    # --- qualité des palettes (indicateurs de la visualisation du notebook) et plan de la soute
    assert page.is_visible("#panneau-qualite")
    entetes = page.locator("#table-qualite thead th").all_inner_texts()
    assert any("Compacité" in e for e in entetes) and any("Remplissage volumique" in e for e in entetes)
    assert any("Décalage horizontal" in e for e in entetes) and any("Centre de masse x" in e for e in entetes)
    assert page.locator("#table-qualite tbody tr").count() == res["n"]
    resume = page.inner_text("#qualite-resume")
    assert f"Palettes : {res['n']}" in resume and "Hauteur maximale" in resume and "Somme des hauteurs" in resume
    assert page.locator("#qualite-etapes tbody tr").count() == 3                 # heuristique, après la VND, solution finale
    assert page.locator("#plan-bilan g.emplacement").count() == 9 and page.locator("#plan-bilan g.occupe").count() == res["n"]
    separateur = page.locator("#separateur-qualite")
    separateur.focus()
    for _ in range(5):
        separateur.press("ArrowUp")                                            # agrandit le tableau de qualité (24 px par flèche)
    capture("qualite")
    separateur.dblclick()                                                      # taille d'origine pour la suite
    # --- étapes et journal
    assert page.locator("button.etape").count() == 3                       # heuristique, après la VND, solution finale
    assert page.locator("#liste-journal li").count() == 4
    assert "Cinq essais du notebook" in page.inner_text("#liste-journal")
    page.click("button.etape >> nth=0")
    assert page.evaluate("window.palettiseur.etat.etape") == "heuristique"
    assert "heuristique" in page.inner_text("#qualite-etape").lower()                    # le tableau suit l'étape affichée
    assert page.locator("#table-qualite tbody tr").count() == len(page.evaluate("window.palettiseur.etat.resultat.etapes[0].palettes"))
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
    assert "étiquette visible" in panneau and "côté" in panneau and "Emballage" in panneau

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
    assert page.locator("[data-param=duree_recherche]").count() == 0             # un seul paramètre de temps : temps_limite
    assert page.is_visible("[data-param=regrouper_par_dat]") and page.is_visible("[data-param=marge_hauteur]")
    assert page.locator("[data-param=temps_limite_scip]").count() == 0 and page.locator("[data-param=mode_notebook]").count() == 0
    assert [o.get_attribute("value") for o in page.locator("#select-moteur option").all()] == ["notebook", "heuristique"]
    assert "CDP_5" in page.inner_text("#aide-moteur")
    # la matière d'emballage n'est plus un paramètre : elle ne change pas le placement
    assert page.locator("[data-param=charge_max_carton]").count() == 0 and page.locator("[data-param=emballages_ecrasables]").count() == 0
    assert page.locator("[data-param=derogation_couloir]").count() == 0
    assert page.is_visible("[data-param=reduction_couloir]") and page.is_visible("[data-param=axe_cotes]")
    assert "274 × 224" in page.inner_text("#aide-largeur-utile") and "sans couloir" in page.inner_text("#aide-largeur-utile")
    page.fill("[data-param=reduction_couloir]", "14")
    page.select_option("[data-param=axe_cotes]", "x")
    assert "246 × 224" in page.inner_text("#aide-largeur-utile")            # couloir sur l'axe x : 274 - 2 x 14
    page.select_option("[data-param=axe_cotes]", "y")
    assert "274 × 196" in page.inner_text("#aide-largeur-utile")            # couloir sur l'axe y : 224 - 2 x 14
    page.fill("[data-param=reduction_couloir]", "0")
    # plan de la soute : 7 emplacements en soute (4 536 kg) et 2 sur la rampe (3 000 kg), cliquables
    assert page.locator("#plan-parametres g.emplacement").count() == 9
    assert page.locator("#plan-parametres g.emplacement.soute").count() == 7 and page.locator("#plan-parametres g.emplacement.rampe").count() == 2
    assert "4 536 kg max" in texte_svg(page, "#plan-parametres g.emplacement.soute")
    assert "3 000 kg max" in texte_svg(page, "#plan-parametres g.emplacement.rampe")
    assert "9 emplacement(s) libre(s)" in page.inner_text("#aide-plan")
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
    assert "5725" in resume and "température est ignorée" in resume and "27" in resume and "DAT" in resume
    assert "écraser" not in resume and "ne change pas le placement" in resume              # la matière n'est qu'une information
    capture("import_excel")
    page.click("#btn-import-remplacer")
    page.wait_for_function("window.palettiseur.etat.colis.length === 54")
    colis = page.evaluate("window.palettiseur.etat.colis")
    assert colis[0]["numero"] == "0836" and colis[0]["reference"].endswith("0836") and "renversable" not in colis[0]
    assert colis[0]["dat"] == "DAT12301531890"                           # « Cargo Nat Ref 1 » complet
    assert sum(c["classe"] == "ARME" for c in colis) == 14 and sum(c["classe"] == "SECU" for c in colis) == 10
    assert sum(c["emballage"] in ("carton", "fibre") for c in colis) == 28
    # règles du projet en plus de celles du notebook : couloir de 14 cm, IATA sur le bord, lourd jamais sur léger
    page.evaluate("window.palettiseur.etat.parametres = {...window.palettiseur.etat.config.parametres, temps_limite: 8, reduction_couloir: 14, iata_au_bord: true, facteur_masse: 1}")
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


def test_liste_de_colis_modifiable_avant_le_calcul(app):
    """Après un import, on corrige les colis directement dans la liste : valeurs vérifiées, résultat marqué périmé."""
    page, serveur = app
    page.goto(serveur.url)
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")
    page.evaluate("localStorage.clear()")
    page.set_input_files("#fichier-import", str(Path(__file__).resolve().parent.parent / "data" / "exemple_cargo.xlsx"))
    page.wait_for_selector("#dlg-import[open]")
    page.click("#btn-import-remplacer")
    page.wait_for_function("window.palettiseur.etat.colis.length === 54")
    ligne = lambda i: page.locator("#table-colis tbody tr").nth(i)
    assert ligne(0).locator("[data-champ=masse]").input_value() == "15"
    avant = page.evaluate("window.palettiseur.etat.colis[0]")

    # masse : Entrée valide la case, enregistre et passe au colis suivant
    ligne(0).locator("[data-champ=masse]").fill("18.5")
    ligne(0).locator("[data-champ=masse]").press("Enter")
    assert page.evaluate("window.palettiseur.etat.colis[0].masse") == 18.5
    assert page.evaluate("document.activeElement.dataset.champ") == "masse"
    assert page.evaluate("document.activeElement.closest('tr').dataset.index") == "1"
    assert "kg" in page.inner_text("#masse-totale") and "2" in page.inner_text("#masse-totale")

    # dimensions, quantité, classe, fragile et DAT
    ligne(0).locator("[data-champ=longueur]").fill("50")
    ligne(0).locator("[data-champ=longueur]").press("Tab")
    ligne(0).locator("[data-champ=quantite]").fill("3")
    ligne(0).locator("[data-champ=quantite]").press("Tab")
    ligne(0).locator("[data-champ=fragile]").check()
    ligne(0).locator("[data-champ=dat]").fill("DAT-TEST")
    ligne(0).locator("[data-champ=dat]").press("Tab")
    ligne(0).locator("[data-champ=classe]").select_option("ARME")
    c = page.evaluate("window.palettiseur.etat.colis[0]")
    assert (c["longueur"], c["quantite"], c["fragile"], c["dat"], c["classe"]) == (50, 3, True, "DAT-TEST", "ARME")
    assert (c["largeur"], c["hauteur"], c["numero"], c["reference"]) == (avant["largeur"], avant["hauteur"], avant["numero"], avant["reference"])
    assert "×3" not in ligne(0).inner_text()          # la quantité est une case de la liste
    page.evaluate("document.querySelector('#table-colis').closest('.table-defilante').scrollLeft = 0")
    page.locator("#table-colis").focus()
    capturer(page, "liste_modifiable")

    # valeur refusée : l'ancienne est conservée, l'erreur est expliquée
    ligne(0).locator("[data-champ=hauteur]").fill("-5")
    ligne(0).locator("[data-champ=hauteur]").press("Tab")
    assert page.evaluate("window.palettiseur.etat.colis[0].hauteur") == avant["hauteur"]
    assert ligne(0).locator("[data-champ=hauteur]").input_value() == str(avant["hauteur"])
    assert "la hauteur doit être un nombre positif" in page.inner_text("#erreur-liste")
    ligne(1).locator("[data-champ=numero]").fill(avant["numero"])               # numéro déjà pris
    ligne(1).locator("[data-champ=numero]").press("Tab")
    assert "existe déjà" in page.inner_text("#erreur-liste")
    ligne(0).locator("[data-champ=quantite]").fill("2.5")
    ligne(0).locator("[data-champ=quantite]").press("Tab")
    assert page.evaluate("window.palettiseur.etat.colis[0].quantite") == 3 and "entier" in page.inner_text("#erreur-liste")

    # Échap annule une saisie en cours
    ligne(2).locator("[data-champ=masse]").fill("999")
    ligne(2).locator("[data-champ=masse]").press("Escape")
    assert page.evaluate("window.palettiseur.etat.colis[2].masse") != 999
    # les modifications sont mémorisées
    assert page.evaluate("JSON.parse(localStorage.getItem('palettiseur.colis'))[0].masse") == 18.5

    # après un calcul, une modification marque le résultat comme périmé
    page.evaluate("window.palettiseur.etat.parametres = {...window.palettiseur.etat.config.parametres, moteur: 'heuristique', temps_limite: 1}")
    page.evaluate("window.palettiseur.etat.colis = window.palettiseur.etat.colis.slice(0, 12).map(c => ({...c, quantite: 1}))")
    page.click("#btn-optimiser")
    page.wait_for_function("window.palettiseur.etat.resultat !== null", timeout=60000)
    assert page.is_hidden("#bilan-perime")
    ligne(0).locator("[data-champ=masse]").fill("19")
    ligne(0).locator("[data-champ=masse]").press("Tab")
    assert page.is_visible("#bilan-perime") and "relancez" in page.inner_text("#bilan-perime")


def test_separateurs_redimensionnables(app):
    """Souris, clavier et double-clic ; les tailles sont mémorisées et rétablies au rechargement."""
    page, serveur = app
    page.goto(serveur.url)
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")
    largeur = lambda sel: page.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().width")
    hauteur = lambda sel: page.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().height")
    assert page.locator(".separateur.vertical").count() == 2 and page.locator(".separateur[role=separator]").count() >= 6

    # colonne de gauche : glisser vers la droite l'élargit, la colonne du centre rétrécit
    g0, c0 = largeur(".panneau.gauche"), largeur("#centre")
    sep = page.locator(".separateur.vertical").nth(0)
    b = sep.bounding_box()
    page.mouse.move(b["x"] + 5, b["y"] + b["height"] / 2)
    page.mouse.down(); page.mouse.move(b["x"] + 85, b["y"] + b["height"] / 2, steps=6); page.mouse.up()
    g1 = largeur(".panneau.gauche")
    assert 70 <= g1 - g0 <= 90 and largeur("#centre") < c0
    # colonne de droite : glisser vers la gauche l'élargit
    d0 = largeur(".panneau.droite")
    sep = page.locator(".separateur.vertical").nth(1)
    b = sep.bounding_box()
    page.mouse.move(b["x"] + 5, b["y"] + b["height"] / 2)
    page.mouse.down(); page.mouse.move(b["x"] - 60, b["y"] + b["height"] / 2, steps=6); page.mouse.up()
    assert 50 <= largeur(".panneau.droite") - d0 <= 70

    # limites : la colonne du centre garde une largeur minimale, la colonne de gauche aussi
    sep = page.locator(".separateur.vertical").nth(0)
    b = sep.bounding_box()
    page.mouse.move(b["x"] + 5, b["y"] + b["height"] / 2)
    page.mouse.down(); page.mouse.move(b["x"] + 3000, b["y"] + b["height"] / 2, steps=4); page.mouse.up()
    assert largeur("#centre") >= 370
    b = sep.bounding_box()
    page.mouse.move(b["x"] + 5, b["y"] + b["height"] / 2)
    page.mouse.down(); page.mouse.move(b["x"] - 3000, b["y"] + b["height"] / 2, steps=4); page.mouse.up()
    assert largeur(".panneau.gauche") >= 299

    # entre le formulaire et la liste : la hauteur du formulaire change
    f0 = hauteur("#carte-formulaire")
    sep = page.locator(".separateur.horizontal[data-cible=carte-formulaire]")
    b = sep.bounding_box()
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + 5)
    page.mouse.down(); page.mouse.move(b["x"] + b["width"] / 2, b["y"] - 100, steps=6); page.mouse.up()
    assert 90 <= f0 - hauteur("#carte-formulaire") <= 110

    # clavier : flèches (24 px), Majuscule pour aller plus vite
    sep = page.locator(".separateur.vertical").nth(0)
    g0 = largeur(".panneau.gauche")
    sep.focus(); sep.press("ArrowRight"); sep.press("ArrowRight")
    assert round(largeur(".panneau.gauche") - g0) == 48
    sep.press("ArrowLeft")
    assert round(largeur(".panneau.gauche") - g0) == 24
    # mémorisation, puis rétablissement au rechargement de la page
    memo = page.evaluate("JSON.parse(localStorage.getItem('palettiseur.disposition.v1'))")
    assert "g" in memo["colonnes"] and "d" in memo["colonnes"] and "carte-formulaire" in memo["hauteurs"]
    g_avant = largeur(".panneau.gauche")
    page.reload()
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")
    assert abs(largeur(".panneau.gauche") - g_avant) <= 1
    # double-clic : taille d'origine
    page.locator(".separateur.vertical").nth(0).dblclick()
    assert round(largeur(".panneau.gauche")) == 560
    page.locator(".separateur.horizontal[data-cible=carte-formulaire]").dblclick()
    assert page.evaluate("document.querySelector('#carte-formulaire').style.height") == ""
    assert "g" not in page.evaluate("JSON.parse(localStorage.getItem('palettiseur.disposition.v1'))")["colonnes"]
    page.evaluate("localStorage.clear()")


def test_plan_de_la_soute_et_emplacements_indisponibles(app):
    """Le plan montre 7 emplacements en soute et 2 sur la rampe ; on déclare des emplacements indisponibles, et le calcul
    n'utilise que les emplacements libres."""
    page, serveur = app
    page.goto(serveur.url)
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")
    page.click("#btn-parametres")
    emp = lambda n: page.locator(f"#plan-parametres g.emplacement[data-numero='{n}']")
    emp(1).click(); emp(9).click()
    assert "indisponible" in emp(1).get_attribute("class") and "indisponible" in emp(9).get_attribute("class")
    assert "7 emplacement(s) libre(s)" in page.inner_text("#aide-plan") and "2 indisponible(s)" in page.inner_text("#aide-plan")
    emp(9).click()                                                         # le recliquer le libère
    assert "libre" in emp(9).get_attribute("class") and "8 emplacement(s) libre(s)" in page.inner_text("#aide-plan")
    capturer(page, "plan_soute", page.locator("#fieldset-emplacements"))
    # charge maximale propre à chaque zone, visible sur le plan
    page.fill("[data-param=masse_max_rampe]", "2500")
    assert "2 500 kg max" in texte_svg(page, "#plan-parametres g.emplacement.rampe")
    # sièges des passagers : 64 passagers = 7 emplacements au plus ; il y en a 8 de libres (le 1 est indisponible), donc
    # les sièges prennent le premier d'entre eux, à l'avant (le 2)
    page.fill("[data-param=passagers]", "64")
    assert "passagers" in emp(2).get_attribute("class") and "passagers" not in emp(3).get_attribute("class")
    assert "7 emplacement(s) libre(s)" in page.inner_text("#aide-plan") and "1 pris par les sièges" in page.inner_text("#aide-plan")
    page.fill("[data-param=passagers]", "0")
    page.click("#btn-params-ok")
    assert page.evaluate("window.palettiseur.etat.parametres.emplacements_indisponibles") == [1]
    assert page.evaluate("window.palettiseur.etat.parametres.masse_max_rampe") == 2500

    # calcul : la première palette va à l'emplacement 2 (le 1 est indisponible)
    page.evaluate("""window.palettiseur.etat.parametres = {...window.palettiseur.etat.parametres, moteur: 'heuristique', temps_limite: 1};
                     window.palettiseur.etat.colis = [{numero: 'A', longueur: 200, largeur: 200, hauteur: 120, masse: 300, classe: '', quantite: 3}]""")
    page.click("#btn-optimiser")
    page.wait_for_function("window.palettiseur.etat.resultat !== null", timeout=60000)
    occupes = page.evaluate("window.palettiseur.etat.resultat.palettes.flatMap(p => p.emplacements)")
    assert occupes and 1 not in occupes and occupes[0] == 2
    assert page.locator("#plan-bilan g.emplacement.indisponible").count() == 1
    assert page.locator("#plan-bilan g.occupe").count() == len(occupes)
    # clic sur un emplacement occupé : affiche la palette
    page.locator("#plan-bilan g.occupe >> nth=0").click()
    assert page.evaluate("window.palettiseur.etat.paletteCourante") == 1
    page.evaluate("localStorage.clear()")


def test_calcul_en_arriere_plan_via_l_api(app):
    """Le serveur lance le calcul dans un fil : démarrage, avancement par phase, résultat ; un seul calcul à la fois."""
    import time
    from palettiseur import api
    colis = [dict(numero=f"c{i}", longueur=60, largeur=40, hauteur=40, masse=30, quantite=1) for i in range(10)]
    dem = api.demarrer_optimisation(colis, {"moteur": "heuristique", "temps_limite": 1})
    assert dem["id"] and dem["budget"] == 1
    with pytest.raises(ValueError, match="déjà en cours"):
        api.demarrer_optimisation(colis, {"moteur": "heuristique", "temps_limite": 1})
    phases = set()
    for _ in range(200):
        e = api.etat_optimisation(dem["id"])
        phases.add(e["phase"])
        if e["etat"] != "en_cours":
            break
        time.sleep(0.1)
    assert e["etat"] == "termine" and len(e["resultat"]["palettes"]) >= 1 and e["ecoule"] > 0
    assert {"heuristique", "vnd"} <= phases or e["resultat"]["moteur"] == "heuristique"
    assert e["resultat"]["indicateurs"]["nombre_palettes"] == len(e["resultat"]["palettes"])
    with pytest.raises(ValueError, match="inconnu"):
        api.etat_optimisation("n'existe-pas")
    # une erreur de calcul est rendue à l'interface, sans planter le serveur
    dem = api.demarrer_optimisation([dict(numero="x", longueur="abc")], {"moteur": "heuristique", "temps_limite": 1})
    for _ in range(100):
        e = api.etat_optimisation(dem["id"])
        if e["etat"] != "en_cours":
            break
        time.sleep(0.1)
    assert e["etat"] in ("termine", "erreur")
