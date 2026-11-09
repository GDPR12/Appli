/* ===================== Palettisation A400M – logique de l'interface ===================== */
import { Vue3D, couleurPoids } from './vue3d.js';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const etat = {
  config: null,          // classes, incompatibilités, paramètres par défaut…
  classes: {},           // code -> {libelle, couleur, iata}
  colis: [],             // liste saisie
  parametres: {},
  resultat: null,        // dernier plan calculé
  paletteCourante: 'ensemble',
  etape: 'finale',       // étape du pipeline affichée : 'heuristique', 'scip' ou 'finale'
  mode: 'categories',
  selection: null,       // {numero, palette}
  edition: null,         // index du colis en cours de modification
};

let vue = null;

// ---------------------------------------------------------------- utilitaires
function notifier(message, erreur = false, html = false) {
  const n = $('#notification');
  if (html) n.innerHTML = message; else n.textContent = message;
  n.className = 'notification' + (erreur ? ' erreur' : '');
  n.hidden = false;
  clearTimeout(notifier._t);
  notifier._t = setTimeout(() => { n.hidden = true; }, erreur ? 9000 : 6000);
}
function echapper(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function nombre(v, dec = 0) {
  return Number(v).toLocaleString('fr-FR', { maximumFractionDigits: dec, minimumFractionDigits: 0 });
}
async function api(route, corps) {
  const rep = await fetch(route, corps === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corps),
  });
  const data = await rep.json();
  if (!rep.ok || data.erreur) throw new Error(data.erreur || `Erreur HTTP ${rep.status}`);
  return data;
}
function sauvegarderLocal() {
  try {
    localStorage.setItem('palettiseur.colis', JSON.stringify(etat.colis));
    localStorage.setItem('palettiseur.parametres.v3', JSON.stringify(etat.parametres));
  } catch (e) { /* stockage indisponible : on ignore */ }
}
function chargerLocal() {
  try {
    const c = localStorage.getItem('palettiseur.colis');
    if (c) etat.colis = JSON.parse(c).map(({ renversable, ...reste }) => reste);
    const p = localStorage.getItem('palettiseur.parametres.v3');
    if (p) etat.parametres = { ...etat.parametres, ...JSON.parse(p) };
  } catch (e) { /* ignore */ }
}
/** Données de l'étape affichée : palettes, vérification et bilan de cette étape, reste du résultat commun. */
function donnees() {
  const res = etat.resultat;
  if (!res) return null;
  if (etat.etape === 'finale') return res;
  const e = (res.etapes || []).find((x) => x.cle === etat.etape);
  return e && e.palettes ? { ...res, palettes: e.palettes, verification: e.verification, bilan: e.bilan } : res;
}
function chipClasse(code) {
  const cl = etat.classes[code];
  if (!code || !cl) return '<span class="chip" title="Colis ordinaire"><span class="puce" style="background:' + (etat.classes['']?.couleur || '#b8c2cc') + '"></span></span>';
  return `<span class="chip" title="${echapper(cl.libelle)}"><span class="puce" style="background:${cl.couleur}"></span>${echapper(code)}</span>`;
}
function libelleEmballage(code) {
  const e = (etat.config.emballages || []).find((x) => x.code === (code || ''));
  return e ? e.libelle : (code || 'Non précisée');
}
function libelleEmballageCourt(code) {
  return code ? libelleEmballage(code).replace(/ \(.*$/, '') : '—';
}
function libelleClasse(code) {
  const cl = etat.classes[code];
  return cl ? cl.libelle : (code || 'Aucun (colis ordinaire)');
}

// ---------------------------------------------------------------- formulaire colis
function numeroSuivant() {
  let n = etat.colis.length + 1;
  const existants = new Set(etat.colis.map((c) => c.numero));
  while (existants.has(`C-${String(n).padStart(3, '0')}`)) n++;
  return `C-${String(n).padStart(3, '0')}`;
}
function reinitialiserFormulaire() {
  etat.edition = null;
  $('#titre-formulaire').textContent = 'Nouveau colis';
  $('#btn-ajouter').textContent = '＋ Ajouter le colis';
  $('#btn-annuler-edition').hidden = true;
  $('#formulaire-colis').reset();
  $('#f-numero').value = numeroSuivant();
  $('#f-quantite').value = 1;
  $('#f-emballage').value = '';
  $('#erreur-formulaire').hidden = true;
  majAideClasse();
}
function lireFormulaire() {
  const c = {
    numero: $('#f-numero').value.trim(),
    longueur: parseFloat($('#f-longueur').value),
    largeur: parseFloat($('#f-largeur').value),
    hauteur: parseFloat($('#f-hauteur').value),
    masse: parseFloat($('#f-masse').value),
    fragile: $('#f-fragile').checked,
    emballage: $('#f-emballage').value,
    classe: $('#f-classe').value,
    commentaire: $('#f-commentaire').value.trim(),
    reference: etat.edition !== null ? (etat.colis[etat.edition].reference || '') : '',
    quantite: Math.max(1, parseInt($('#f-quantite').value || '1', 10)),
  };
  const erreurs = [];
  if (!c.numero) erreurs.push('le numéro est obligatoire');
  for (const [k, lib] of [['longueur', 'la longueur'], ['largeur', 'la largeur'], ['hauteur', 'la hauteur'], ['masse', 'la masse']]) {
    if (!(c[k] > 0)) erreurs.push(`${lib} doit être un nombre positif`);
  }
  const doublon = etat.colis.findIndex((x, i) => x.numero === c.numero && i !== etat.edition);
  if (doublon >= 0) erreurs.push(`le numéro « ${c.numero} » existe déjà`);
  return { colis: c, erreurs };
}
function remplirFormulaire(c) {
  $('#f-numero').value = c.numero;
  $('#f-longueur').value = c.longueur;
  $('#f-largeur').value = c.largeur;
  $('#f-hauteur').value = c.hauteur;
  $('#f-masse').value = c.masse;
  $('#f-emballage').value = c.emballage || '';
  $('#f-fragile').checked = !!c.fragile;
  $('#f-classe').value = c.classe || '';
  $('#f-commentaire').value = c.commentaire || '';
  $('#f-quantite').value = c.quantite || 1;
  majAideClasse();
}
function majAideClasse() {
  const code = $('#f-classe').value;
  const cl = etat.classes[code];
  const aide = $('#aide-classe');
  if (!code || !cl) { aide.textContent = 'Colis ordinaire : aucune contrainte de séparation.'; return; }
  const incomp = (etat.config.incompatibilites[code] || []);
  let txt = cl.description ? cl.description + ' ' : '';
  if (cl.iata) txt += etat.parametres.iata_au_bord
    ? 'Étiquette IATA : le colis est posé contre l\'un des deux bords côté couloir et reste visible depuis ce bord. '
    : 'Étiquette IATA : le colis reste visible depuis au moins un des deux côtés du couloir. ';
  txt += incomp.length ? `Jamais sur la même palette que : ${incomp.join(', ')}.` : 'Compatible avec toutes les autres classes.';
  aide.textContent = txt;
}
function surSoumission(e) {
  e.preventDefault();
  const { colis, erreurs } = lireFormulaire();
  const zoneErreur = $('#erreur-formulaire');
  if (erreurs.length) { zoneErreur.textContent = 'Corrigez : ' + erreurs.join(' ; ') + '.'; zoneErreur.hidden = false; return; }
  if (etat.edition !== null) etat.colis[etat.edition] = colis; else etat.colis.push(colis);
  sauvegarderLocal();
  afficherListe();
  reinitialiserFormulaire();
  $('#f-longueur').focus();
}

// ---------------------------------------------------------------- liste des colis
function palettesDuColis(c) {
  if (!etat.resultat) return [];
  const index = etat.resultat._indexColis;
  const nums = c.quantite > 1 ? Array.from({ length: c.quantite }, (_, k) => `${c.numero}-${k + 1}`) : [c.numero];
  return nums.map((n) => index[n] ?? '—');
}
function afficherListe() {
  const corps = $('#table-colis tbody');
  corps.innerHTML = '';
  let masse = 0, volume = 0, nb = 0;
  etat.colis.forEach((c, i) => {
    const q = c.quantite || 1;
    masse += c.masse * q; volume += c.longueur * c.largeur * c.hauteur * q / 1e6; nb += q;
    const tr = document.createElement('tr');
    tr.className = 'ligne-colis' + (etat.selection && etat.selection.base === c.numero ? ' selectionnee' : '');
    tr.dataset.index = i;
    const pals = palettesDuColis(c);
    const palTxt = pals.length ? [...new Set(pals)].map((p) => (p === '—' ? '<span class="danger">non placé</span>' : `P${p}`)).join(', ') : '';
    tr.innerHTML = `
      <td><span class="num">${echapper(c.numero)}</span>${q > 1 ? `<span class="qte" title="Quantité">×${q}</span>` : ''}${c.commentaire ? `<span class="commentaire">${echapper(c.commentaire)}</span>` : ''}</td>
      <td>${nombre(c.longueur, 1)}×${nombre(c.largeur, 1)}×${nombre(c.hauteur, 1)}</td>
      <td>${nombre(c.masse, 1)}</td>
      <td title="${echapper(libelleEmballage(c.emballage))}">${echapper(libelleEmballageCourt(c.emballage))}${c.fragile ? ' <span title="Fragile : rien dessus">🥚</span>' : ''}</td>
      <td>${chipClasse(c.classe)}</td>
      <td>${palTxt}</td>
      <td class="actions-ligne"><button class="lien" data-action="editer" title="Modifier">✎</button><button class="lien danger" data-action="supprimer" title="Supprimer">🗑</button></td>`;
    corps.appendChild(tr);
  });
  $('#compteur-colis').textContent = nb;
  $('#liste-vide').hidden = etat.colis.length > 0;
  $('#masse-totale').textContent = `${nombre(masse, 1)} kg`;
  $('#volume-total').textContent = `${nombre(volume, 2)} m³`;
}
function surClicListe(e) {
  const tr = e.target.closest('tr.ligne-colis');
  if (!tr) return;
  const i = parseInt(tr.dataset.index, 10);
  const action = e.target.dataset.action;
  if (action === 'supprimer') {
    etat.colis.splice(i, 1);
    if (etat.edition === i) reinitialiserFormulaire();
    sauvegarderLocal(); afficherListe(); return;
  }
  if (action === 'editer') {
    etat.edition = i;
    remplirFormulaire(etat.colis[i]);
    $('#titre-formulaire').textContent = `Modifier le colis ${etat.colis[i].numero}`;
    $('#btn-ajouter').textContent = '✔ Enregistrer les modifications';
    $('#btn-annuler-edition').hidden = false;
    $('#f-longueur').focus();
    return;
  }
  // Sélection dans la vue 3D
  const c = etat.colis[i];
  const numero = (c.quantite || 1) > 1 ? `${c.numero}-1` : c.numero;
  if (etat.resultat && etat.etape !== 'finale') changerEtape('finale');
  selectionnerColis(numero, null, true);
}

// ---------------------------------------------------------------- paramètres
function ouvrirParametres() {
  const form = $('#form-parametres');
  for (const el of form.querySelectorAll('[data-param]')) {
    const k = el.dataset.param; const v = etat.parametres[k];
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.dataset.pourcent) el.value = Math.round(v * 100);
    else el.value = v;
  }
  majAidePassagers();
  majAideMoteur();
  majAideLargeurUtile();
  $('#dlg-parametres').showModal();
}
function lireParametres() {
  const p = { ...etat.parametres };
  for (const el of $('#form-parametres').querySelectorAll('[data-param]')) {
    const k = el.dataset.param;
    if (el.type === 'checkbox') p[k] = el.checked;
    else if (el.tagName === 'SELECT' || el.type === 'text') p[k] = el.value.trim();
    else if (el.dataset.pourcent) p[k] = parseFloat(el.value) / 100;
    else p[k] = parseFloat(el.value);
  }
  return p;
}
function majAideLargeurUtile() {
  const val = (k) => parseFloat($(`[data-param=${k}]`).value) || 0;
  const derog = $('[data-param=derogation_couloir]').checked;
  const utile = derog ? val('largeur_palette') : Math.max(0, val('largeur_palette') - 2 * val('reduction_couloir'));
  $('#aide-largeur-utile').textContent = derog
    ? `Largeur utile : ${nombre(utile)} cm (pas de couloir).`
    : `Largeur utile : ${nombre(utile)} cm, soit ${nombre(val('largeur_palette'))} cm moins ${nombre(val('reduction_couloir'))} cm de couloir de chaque côté.`;
}
function majAidePassagers() {
  const pax = parseInt($('[data-param=passagers]').value || '0', 10);
  let positions = 0;
  for (const v of etat.config.versions_avion) { if (pax <= v.passagers_max) { positions = v.palettes; break; } }
  $('#aide-passagers').textContent = pax > 116 ? 'Plus de 116 passagers : aucun emplacement de palette (version tout passagers).'
    : `Version A400M : ${positions} emplacement(s) de palette disponibles pour ${pax} passager(s).`;
}
function majAideMoteur() {
  const code = $('#select-moteur').value;
  const m = etat.config.moteurs.find((x) => x.code === code);
  $('#aide-moteur').textContent = !m ? '' : m.code === 'scip'
    ? 'Pipeline du notebook CDP : l\'heuristique sert d\'amorce à SCIP (réduction du nombre d\'emplacements) puis fusion, tassement vertical et horizontal sans solveur. Plusieurs minutes possibles ; réservé aux listes de moins de 60 colis.'
    : 'Cinq phases sans solveur : multi-départ, fusion des palettes, tassement vertical, recentrage et tassement horizontal. Quelques secondes.';
}
function remplirSelectMoteur() {
  const sel = $('#select-moteur');
  sel.innerHTML = '';
  for (const m of etat.config.moteurs) {
    const o = document.createElement('option');
    o.value = m.code; o.textContent = m.libelle + (m.disponible ? '' : ' — non installé');
    o.disabled = !m.disponible;
    sel.appendChild(o);
  }
}

// ---------------------------------------------------------------- optimisation
async function optimiser() {
  if (!etat.colis.length) { notifier('Ajoutez d\'abord des colis.', true); return; }
  $('#chargement').hidden = false;
  $('#btn-optimiser').disabled = true;
  try {
    const res = await api('/api/optimiser', { colis: etat.colis, parametres: etat.parametres });
    res._indexColis = {};
    for (const pal of res.palettes) for (const p of pal.placements) res._indexColis[p.colis.numero] = pal.numero;
    etat.resultat = res;
    etat.selection = null;
    etat.etape = 'finale';
    etat.paletteCourante = res.palettes.length === 1 ? 1 : 'ensemble';
    afficherEtapes();
    afficherJournal();
    afficherOnglets();
    afficherVue();
    afficherBilan();
    afficherMessages();
    afficherListe();
    afficherInfoColis(null);
    const nbErr = res.verification.filter((m) => m.niveau === 'erreur').length;
    notifier(`${res.palettes.length} palette(s), ${res.bilan.positions_utilisees} emplacement(s), ${res.bilan.colis_places} colis placés`
      + (res.non_places.length ? `, ${res.non_places.length} non placé(s)` : '') + ` — ${res.iterations} construction(s) en ${res.duree} s`
      + (nbErr ? ` — ${nbErr} erreur(s) à corriger` : ''), nbErr > 0);
  } catch (e) {
    notifier('Échec du calcul : ' + e.message, true);
  } finally {
    $('#chargement').hidden = true;
    $('#btn-optimiser').disabled = false;
  }
}

// ---------------------------------------------------------------- affichage du résultat
function palettesAffichees() {
  const d = donnees();
  if (!d) return [];
  if (etat.paletteCourante === 'ensemble') return d.palettes;
  return d.palettes.filter((p) => p.numero === etat.paletteCourante);
}
function afficherEtapes() {
  const zone = $('#barre-etapes');
  const res = etat.resultat;
  zone.innerHTML = '';
  zone.hidden = !res || !(res.etapes && res.etapes.length > 1);
  if (zone.hidden) return;
  zone.insertAdjacentHTML('beforeend', '<span class="lib">Étape du calcul :</span>');
  for (const e of res.etapes) {
    const b = document.createElement('button');
    b.className = 'etape' + (e.cle === etat.etape ? ' actif' : '');
    b.title = e.description;
    b.innerHTML = `${echapper(e.titre)}<small>${e.nombre_palettes} pal. · ${nombre(e.somme_hauteurs)} cm</small>`;
    b.addEventListener('click', () => changerEtape(e.cle));
    zone.appendChild(b);
  }
}
function changerEtape(cle) {
  etat.etape = cle;
  const d = donnees();
  if (etat.paletteCourante !== 'ensemble' && !d.palettes.some((p) => p.numero === etat.paletteCourante)) etat.paletteCourante = 'ensemble';
  etat.selection = null;
  afficherEtapes(); afficherOnglets(); afficherVue(); afficherBilan(); afficherMessages(); afficherInfoColis(null);
}
function afficherJournal() {
  const res = etat.resultat;
  const carte = $('#carte-journal');
  const ol = $('#liste-journal');
  ol.innerHTML = '';
  carte.hidden = !res || !(res.journal && res.journal.length);
  if (carte.hidden) return;
  const cibles = { 1: 'heuristique', 2: 'scip', 5: 'finale' };
  for (const j of res.journal) {
    const li = document.createElement('li');
    li.className = j.ignoree ? 'ignoree' : '';
    const bilan = j.palettes !== undefined ? `<span class="bilan">${j.palettes} pal. · ${nombre(j.hauteurs)} cm</span>` : '';
    li.innerHTML = `<div class="tete"><strong>Phase ${j.phase} — ${echapper(j.titre)}</strong><span class="duree">${j.duree.toFixed(2)} s</span></div>
      ${bilan}<div class="texte">${echapper(j.texte)}</div>`;
    const cle = cibles[j.phase];
    if (cle && !j.ignoree && (res.etapes || []).some((e) => e.cle === cle)) {
      li.style.cursor = 'pointer';
      li.title = 'Afficher cet état dans la vue 3D';
      li.addEventListener('click', () => changerEtape(cle));
    }
    ol.appendChild(li);
  }
}
function afficherOnglets() {
  const zone = $('#onglets');
  zone.innerHTML = '';
  const res = donnees();
  if (!res) return;
  const items = [{ id: 'ensemble', txt: `Vue d'ensemble <span class="type">${res.palettes.length} palette(s)</span>` }];
  for (const p of res.palettes) {
    items.push({ id: p.numero, txt: `Palette ${p.numero} <span class="type ${p.zone === 'rampe' ? 'rampe' : ''}">${p.type}${p.zone === 'rampe' ? ' · rampe' : ''}</span>` });
  }
  for (const it of items) {
    const b = document.createElement('button');
    b.className = 'onglet' + (String(etat.paletteCourante) === String(it.id) ? ' actif' : '');
    b.dataset.palette = it.id;
    b.innerHTML = it.txt;
    b.addEventListener('click', () => { etat.paletteCourante = it.id === 'ensemble' ? 'ensemble' : parseInt(it.id, 10); afficherOnglets(); afficherVue(); });
    zone.appendChild(b);
  }
}
function afficherVue() {
  const res = donnees();
  $('#message-vue').hidden = !!(res && res.palettes.length);
  if (!res) return;
  vue.afficherPalettes(palettesAffichees(), {
    classes: etat.classes, couleursCarte: res.couleurs_carte, heat: res.heat_map,
    largeurPhysique: res.parametres.largeur_palette,
  });
  vue.setMode(etat.mode);
  if (etat.selection) vue.selectionner(etat.selection.numero, etat.selection.palette);
  afficherLegende();
  afficherInfoPalette(etat.paletteCourante === 'ensemble' ? null : etat.paletteCourante);
}
function afficherLegende() {
  const zone = $('#legende');
  const res = donnees();
  if (!res) { zone.innerHTML = ''; return; }
  const pals = palettesAffichees();
  if (etat.mode === 'poids') {
    const { min, max } = res.heat_map;
    const stops = [0, 0.25, 0.5, 0.75, 1].map((t) => couleurPoids(t)).join(',');
    zone.innerHTML = `<span class="item"><strong>Heat map de masse</strong></span><span class="item">${nombre(min)} kg</span>
      <span class="degrade" style="background:linear-gradient(90deg,${stops})"></span><span class="item">${nombre(max)} kg</span>
      <span class="item aide">Couleur proportionnelle à la masse du colis (échelle commune à toutes les palettes).</span>`;
    return;
  }
  if (etat.mode === 'carte_tous' || etat.mode === 'carte_speciaux') {
    const n = etat.mode === 'carte_tous' ? 'tous les colis' : 'les colis spéciaux';
    zone.innerHTML = `<span class="item"><strong>Coloriage de carte</strong></span><span class="item aide">Deux colis en contact n'ont jamais la même couleur (algorithme DSatur sur le graphe de contact, appliqué à ${n}).</span>`
      + (etat.mode === 'carte_speciaux' ? `<span class="item"><span class="puce" style="background:#b8c2cc"></span>ordinaire</span>` : '');
    return;
  }
  const present = new Set();
  for (const p of pals) for (const pl of p.placements) present.add(pl.colis.classe || '');
  const items = [...present].sort().map((code) => {
    const cl = etat.classes[code] || { libelle: code, couleur: '#ff00ff' };
    return `<span class="item"><span class="puce" style="background:${cl.couleur}"></span>${echapper(code ? `${code} – ${cl.libelle}` : 'Colis ordinaire')}</span>`;
  });
  zone.innerHTML = `<span class="item"><strong>Catégories</strong></span>` + items.join('');
}
function afficherBilan() {
  const res = donnees();
  const b = res.bilan;
  const zone = $('#grille-bilan');
  const ligne = (cle, valeur, classe = '') => `<div class="cle">${cle}</div><div class="valeur ${classe}">${valeur}</div>`;
  const posOk = b.positions_utilisees <= b.positions_disponibles;
  const masseOk = b.masse_totale_brute <= b.capacite_avion;
  zone.innerHTML = [
    ligne('Palettes', `${b.nombre_palettes} <span class="aide">(${b.palettes_soute} soute, ${b.palettes_rampe} rampe)</span>`),
    ligne('Emplacements', `${b.positions_utilisees} / ${b.positions_disponibles}`, posOk ? 'bon' : 'mauvais'),
    ligne('Colis placés', `${b.colis_places}${b.colis_non_places ? ` <span class="mauvais">(+${b.colis_non_places} non placés)</span>` : ''}`, b.colis_non_places ? 'mauvais' : 'bon'),
    ligne('Fret (net)', `${nombre(b.masse_fret)} kg`),
    ligne('Palettes (tare)', `${nombre(b.masse_palettes)} kg`),
    ligne(`Passagers (${b.passagers})`, `${nombre(b.masse_passagers)} kg`),
    ligne('Charge totale', `${nombre(b.masse_totale_brute)} / ${nombre(b.capacite_avion)} kg`, masseOk ? 'bon' : 'mauvais'),
    ligne('Hauteur globale', `${nombre(b.hauteur_globale)} cm`),
    ligne('Somme des hauteurs', `${nombre(b.somme_hauteurs)} cm`),
    ligne('Calcul', `${res.iterations} constructions · ${res.duree} s · ${res.moteur === 'scip' ? 'SCIP' : 'heuristique'}`),
  ].join('');
  $('#boutons-export').hidden = false;
  $('#info-export').hidden = true;
}
function afficherInfoPalette(numero) {
  const zone = $('#contenu-palette');
  const res = donnees();
  if (!res || numero === null) {
    if (res && res.palettes.length) {
      zone.innerHTML = `<p class="aide">Vue d'ensemble : ${res.palettes.length} palette(s) alignées dans l'ordre de confection (l'ordre réel dans l'avion est décidé par le mécanicien navigant). Cliquez sur un onglet pour le détail d'une palette.</p>`;
    } else zone.innerHTML = '<p class="vide">Sélectionnez une palette dans les onglets.</p>';
    return;
  }
  const p = res.palettes.find((x) => x.numero === numero);
  if (!p) return;
  const tauxMasse = p.masse / p.masse_max;
  const chips = p.classes.length ? p.classes.map(chipClasse).join(' ') : '<span class="aide">aucune matière dangereuse</span>';
  const [cx, cy] = p.centre_de_masse;
  zone.innerHTML = `<div class="fiche-info"><dl>
    <dt>Type</dt><dd>${echapper(p.libelle_type)} · ${p.zone === 'rampe' ? '<span class="chip">rampe</span>' : 'soute'}</dd>
    <dt>Dimensions utiles</dt><dd>${nombre(p.largeur)} × ${nombre(p.longueur)} × ${nombre(p.hauteur_max)} cm <span class="aide">(plateau ${nombre(p.largeur_physique)} cm, couloir de ${nombre(p.marge_couloir, 1)} cm de chaque côté)</span></dd>
    <dt>Masse nette</dt><dd>${nombre(p.masse, 1)} / ${nombre(p.masse_max)} kg<div class="barre-jauge"><span class="${tauxMasse > 1 ? 'mauvais' : ''}" style="width:${Math.min(100, tauxMasse * 100)}%"></span></div></dd>
    <dt>Masse brute estimée</dt><dd>${nombre(p.masse_brute, 1)} kg <span class="aide">(avec ${p.positions} palette(s) vide(s))</span></dd>
    <dt>Hauteur</dt><dd>${nombre(p.hauteur, 1)} cm</dd>
    <dt>Colis</dt><dd>${p.nombre_colis}</dd>
    <dt>Remplissage</dt><dd>${Math.round(p.taux_volume * 100)} % du volume utilisé · ${Math.round(p.taux_sol * 100)} % du sol couvert</dd>
    <dt>Centre de masse</dt><dd>x = ${nombre(cx)} cm (centre ${nombre(p.largeur / 2)}), y = ${nombre(cy)} cm (centre ${nombre(p.longueur / 2)})</dd>
    <dt>Classes présentes</dt><dd class="chips">${chips}</dd>
  </dl></div>`;
}
function voisins(placement, palette) {
  const dessous = [], dessus = [];
  const jeu = etat.resultat.parametres.jeu_contact || 0.5;
  const rec = (a, b) => Math.max(0, Math.min(a.x + a.dx, b.x + b.dx) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.dy, b.y + b.dy) - Math.max(a.y, b.y));
  let aire = 0;
  for (const q of palette.placements) {
    if (q === placement) continue;
    const a = rec(placement, q);
    if (a <= 0) continue;
    if (Math.abs(q.z + q.dz - placement.z) <= jeu) { dessous.push(q.colis.numero); aire += a; }
    if (Math.abs(placement.z + placement.dz - q.z) <= jeu) dessus.push(q.colis.numero);
  }
  const support = placement.z <= jeu ? 1 : aire / (placement.dx * placement.dy);
  return { dessous, dessus, support };
}
function afficherInfoColis(placement, palette) {
  const zone = $('#contenu-colis');
  if (!placement) { zone.innerHTML = '<p class="vide">Cliquez sur un colis dans la vue 3D ou dans la liste.</p>'; return; }
  const c = placement.colis;
  const v = voisins(placement, palette);
  const orientationTxt = (placement.dx === c.longueur && placement.dy === c.largeur && placement.dz === c.hauteur) ? 'sens de base'
    : 'tourné à plat (hauteur conservée)';
  const L = palette.largeur;
  const acces = placement.acces_iata || [];
  const bord = placement.bord_iata || [];
  const nomCote = { gauche: 'couloir gauche', droite: 'couloir droit' };
  const iataTxt = (c.classe && etat.classes[c.classe]?.iata)
    ? ((acces.length ? ` · <span style="color:var(--ok)">étiquette visible depuis le ${acces.map((a) => nomCote[a] || a).join(' et le ')}</span>` : ' · <span style="color:var(--danger)">étiquette masquée des deux côtés</span>')
      + (etat.resultat.parametres.iata_au_bord ? (bord.length ? ` · <span style="color:var(--ok)">posé contre le bord (${bord.map((a) => nomCote[a] || a).join(' et ')})</span>` : ' · <span style="color:var(--danger)">pas posé sur le bord</span>') : ''))
    : '';
  zone.innerHTML = `<div class="fiche-info"><dl>
    <dt>Numéro</dt><dd><strong>${echapper(c.numero)}</strong> · palette ${palette.numero}</dd>
    <dt>Dimensions saisies</dt><dd>${nombre(c.longueur, 1)} × ${nombre(c.largeur, 1)} × ${nombre(c.hauteur, 1)} cm</dd>
    <dt>Posé (X × Y × Z)</dt><dd>${nombre(placement.dx, 1)} × ${nombre(placement.dy, 1)} × ${nombre(placement.dz, 1)} cm · ${orientationTxt}</dd>
    <dt>Masse</dt><dd>${nombre(c.masse, 1)} kg</dd>
    <dt>Classe</dt><dd>${chipClasse(c.classe)} ${echapper(libelleClasse(c.classe))}${iataTxt}</dd>
    <dt>Emballage</dt><dd>${echapper(libelleEmballage(c.emballage))}${etat.resultat.parametres.emballages_ecrasables.split(',').includes(c.emballage) && c.emballage ? ' · <strong>ne pas écraser</strong>' : ''}${c.fragile ? ' · <strong>fragile</strong>' : ''}</dd>
    ${c.reference ? `<dt>N° de série</dt><dd>${echapper(c.reference)}</dd>` : ''}
    <dt>Position</dt><dd>x = ${nombre(placement.x, 1)}, y = ${nombre(placement.y, 1)}, z = ${nombre(placement.z, 1)} cm (coin) · sommet à ${nombre(placement.z + placement.dz, 1)} cm</dd>
    <dt>Repose sur</dt><dd>${placement.z <= 0.5 ? 'la palette' : (v.dessous.length ? v.dessous.join(', ') : '—')} · support ${Math.round(v.support * 100)} %</dd>
    <dt>Porte</dt><dd>${v.dessus.length ? v.dessus.join(', ') : 'rien'}</dd>
    ${c.commentaire ? `<dt>Commentaire</dt><dd>${echapper(c.commentaire)}</dd>` : ''}
  </dl></div>`;
}
function selectionnerColis(numero, palette = null, depuisListe = false) {
  const res = donnees();
  if (!res) return;
  let cible = null;
  for (const pal of res.palettes) for (const p of pal.placements) {
    if (p.colis.numero === numero && (palette === null || pal.numero === palette)) { cible = { p, pal }; break; }
  }
  if (!cible) { notifier(`Le colis ${numero} n'est pas placé sur une palette.`, true); return; }
  etat.selection = { numero, palette: cible.pal.numero, base: numero.replace(/-\d+$/, '') };
  // Si la palette affichée ne contient pas le colis, on bascule sur sa palette.
  if (etat.paletteCourante !== 'ensemble' && etat.paletteCourante !== cible.pal.numero) {
    etat.paletteCourante = cible.pal.numero;
    afficherOnglets(); afficherVue();
  }
  vue.selectionner(numero, cible.pal.numero, false);
  afficherInfoColis(cible.p, cible.pal);
  afficherInfoPalette(cible.pal.numero);
  if (depuisListe) afficherListe();
}
function afficherMessages() {
  const res = donnees();
  const ul = $('#liste-messages');
  ul.innerHTML = '';
  const msgs = res ? res.verification : [];
  $('#compteur-messages').textContent = msgs.length;
  if (!msgs.length) { ul.innerHTML = '<li class="vide">Aucune anomalie : toutes les règles sont respectées.</li>'; return; }
  for (const m of msgs) {
    const li = document.createElement('li');
    li.className = m.niveau + (m.colis && m.colis.length ? ' cliquable' : '');
    li.innerHTML = `<strong>${m.niveau === 'erreur' ? 'Erreur' : 'Attention'}</strong> · ${echapper(m.message)}`;
    if (m.colis && m.colis.length) li.addEventListener('click', () => selectionnerColis(m.colis[0], m.palette, true));
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- import / export
function lireOctets(fichier) {
  // FileReader plutôt que Blob.arrayBuffer() : fonctionne dans tous les navigateurs et fenêtres intégrées.
  return new Promise((resolve, reject) => {
    const lecteur = new FileReader();
    lecteur.onload = () => resolve(lecteur.result);
    lecteur.onerror = () => reject(new Error(`le fichier « ${fichier.name} » n'a pas pu être lu`
      + ' (est-il ouvert dans Excel ? fermez-le ou faites-en une copie, puis réessayez)'));
    lecteur.readAsArrayBuffer(fichier);
  });
}
function decoderTexte(octets) {
  // CSV enregistrés par Excel en français : souvent en Windows-1252, pas en UTF-8.
  try { return new TextDecoder('utf-8', { fatal: true }).decode(octets); } catch (e) { return new TextDecoder('windows-1252').decode(octets); }
}
/** Point d'entrée unique : le format (Excel, CSV, JSON) est reconnu à l'extension puis au contenu. */
async function importerFichier(fichier) {
  try {
    const octets = await lireOctets(fichier);
    const entete = new Uint8Array(octets.slice(0, 4));
    const estZip = entete[0] === 0x50 && entete[1] === 0x4b;                                  // « PK » : classeur .xlsx
    const estOle = entete[0] === 0xd0 && entete[1] === 0xcf && entete[2] === 0x11 && entete[3] === 0xe0;   // ancien .xls
    if (estOle || /\.(xls|ods|numbers)$/i.test(fichier.name)) {
      notifier(`« ${fichier.name} » n'est pas au format .xlsx. Dans Excel : Fichier > Enregistrer sous > Classeur Excel (*.xlsx), puis réimportez-le.`, true);
      return;
    }
    if (estZip || /\.(xlsx|xlsm)$/i.test(fichier.name)) await importerExcel(fichier, octets);
    else await importerTexte(fichier, decoderTexte(octets));
  } catch (e) { notifier('Import impossible : ' + e.message, true); }
}
async function importerTexte(fichier, texte) {
  const rep = await api('/api/importer', { texte });
  if (!rep.colis.length) { notifier('Aucun colis lisible dans ce fichier. ' + (rep.avertissements[0] || ''), true); return; }
  const remplacer = etat.colis.length === 0 || confirm(`Remplacer les ${etat.colis.length} colis actuels par les ${rep.colis.length} colis importés ?\n(Annuler = les ajouter à la suite)`);
  const existants = new Set(remplacer ? [] : etat.colis.map((c) => c.numero));
  const nouveaux = rep.colis.map((c) => ({ ...c, quantite: 1 })).filter((c) => !existants.has(c.numero));
  etat.colis = remplacer ? nouveaux : etat.colis.concat(nouveaux);
  if (rep.parametres) etat.parametres = { ...etat.parametres, ...rep.parametres };
  sauvegarderLocal(); afficherListe();
  notifier(`${nouveaux.length} colis importés depuis ${fichier.name}` + (rep.avertissements.length ? ` (${rep.avertissements.length} ligne(s) ignorée(s))` : ''));
}
function versBase64(tampon) {
  const octets = new Uint8Array(tampon);
  let bin = '';
  for (let i = 0; i < octets.length; i += 0x8000) bin += String.fromCharCode.apply(null, octets.subarray(i, i + 0x8000));
  return btoa(bin);
}
let importEnAttente = null;   // {nom, colis} en attente de confirmation dans la boîte de dialogue
async function importerExcel(fichier, octets) {
  try {
    const nom = /\.(xlsx|xlsm)$/i.test(fichier.name) ? fichier.name : `${fichier.name}.xlsx`;   // reconnu au contenu : on le dit au serveur
    const rep = await api('/api/importer_cargo', { nom, contenu: versBase64(octets) });
    if (!rep.colis.length) { notifier('Aucun colis lisible dans ce fichier. ' + (rep.avertissements[0] || ''), true); return; }
    importEnAttente = { nom: fichier.name, colis: rep.colis.map((c) => ({ ...c, quantite: 1 })) };
    const r = rep.resume;
    const pastille = (n, lib) => `<li><strong>${n}</strong> ${lib}</li>`;
    $('#contenu-import').innerHTML = `<p><strong>${echapper(fichier.name)}</strong> · feuille « ${echapper(r.feuille)} » · numéro = 4 derniers chiffres de la colonne « ${echapper(r.colonne_serie)} »</p>
      <ul class="resume-import">
        ${pastille(r.colis, 'colis lus')}
        ${pastille(r.armes, 'armes (ARME)')}
        ${pastille(r.pieces_securite, 'pièces de sécurité d\'armes (SECU)')}
        ${pastille(r.dangereux, 'colis IATA (posés sur le bord côté couloir)')}
        ${pastille(r.carton + r.fibre, `colis en carton ou en fibre, à ne pas écraser (${r.carton} carton, ${r.fibre} fibre)`)}
      </ul>
      ${rep.avertissements.length ? `<p class="aide"><strong>À vérifier</strong></p><ul class="avertissements-import">${rep.avertissements.map((a) => `<li>${echapper(a)}</li>`).join('')}</ul>` : ''}
      <p class="aide">La température est ignorée. Le caractère fragile n'est pas déduit du fichier : cochez-le à la main sur les colis concernés.</p>`;
    $('#btn-import-ajouter').hidden = etat.colis.length === 0;
    $('#dlg-import').showModal();
  } catch (e) { notifier('Import impossible : ' + e.message, true); }
}
function validerImportExcel(remplacer) {
  const att = importEnAttente;
  $('#dlg-import').close();
  if (!att) return;
  const existants = new Set(remplacer ? [] : etat.colis.map((c) => c.numero));
  const nouveaux = att.colis.filter((c) => !existants.has(c.numero));
  etat.colis = remplacer ? nouveaux : etat.colis.concat(nouveaux);
  importEnAttente = null;
  sauvegarderLocal(); afficherListe(); reinitialiserFormulaire();
  notifier(`${nouveaux.length} colis importés depuis ${att.nom}` + (att.colis.length > nouveaux.length ? ` (${att.colis.length - nouveaux.length} déjà présents, ignorés)` : '') + '. Cliquez sur « Confectionner les palettes ».');
}
async function exporter(format, extra = {}) {
  try {
    const rep = await api('/api/exporter', { format, colis: etat.colis, parametres: etat.parametres, ...extra });
    $('#info-export').hidden = false;
    $('#info-export').innerHTML = `Fichier enregistré : <a href="${rep.url}" download>${echapper(rep.nom)}</a><br><span class="aide">${echapper(rep.chemin)}</span>`;
    notifier(`Fichier enregistré : ${rep.chemin} — <a href="${rep.url}" download>télécharger</a>`, false, true);
  } catch (e) { notifier('Export impossible : ' + e.message, true); }
}
function planCsv() {
  const res = donnees();
  const lignes = [['palette', 'type_palette', 'zone', 'numero_colis', 'classe', 'masse_kg', 'x_cm', 'y_cm', 'z_cm', 'dim_x_cm', 'dim_y_cm', 'dim_z_cm', 'emballage', 'fragile', 'reference', 'commentaire']];
  for (const pal of res.palettes) {
    const tri = [...pal.placements].sort((a, b) => a.z - b.z || a.y - b.y || a.x - b.x);
    for (const p of tri) lignes.push([pal.numero, pal.type, pal.zone, p.colis.numero, p.colis.classe, p.colis.masse, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.colis.emballage || '', p.colis.fragile ? 'oui' : 'non', p.colis.reference || '', p.colis.commentaire]);
  }
  for (const n of res.non_places) lignes.push(['', '', 'NON PLACÉ', n.colis.numero, n.colis.classe, n.colis.masse, '', '', '', n.colis.longueur, n.colis.largeur, n.colis.hauteur, n.colis.emballage || '', n.colis.fragile ? 'oui' : 'non', n.colis.reference || '', n.motif]);
  return lignes.map((l) => l.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(';')).join('\n');
}

// ---------------------------------------------------------------- fiche palette
function plan2D(p) {
  // Vue de dessus SVG : rectangles des colis (les plus hauts par-dessus), numéro au centre.
  const phys = p.largeur_physique || p.largeur, marge = (phys - p.largeur) / 2;
  const W = 360, ech = W / phys, H = p.longueur * ech, decal = marge * ech;
  const tri = [...p.placements].sort((a, b) => (a.z + a.dz) - (b.z + b.dz));
  const rects = tri.map((pl) => {
    const coul = etat.classes[pl.colis.classe]?.couleur || etat.classes['']?.couleur || '#b8c2cc';
    return `<rect x="${decal + pl.x * ech}" y="${(p.longueur - pl.y - pl.dy) * ech}" width="${pl.dx * ech}" height="${pl.dy * ech}" fill="${coul}" stroke="#1f2937" stroke-width="1"/>
      <text x="${decal + (pl.x + pl.dx / 2) * ech}" y="${(p.longueur - pl.y - pl.dy / 2) * ech + 4}" font-size="10" text-anchor="middle" font-family="Arial">${echapper(pl.colis.numero)}</text>`;
  }).join('');
  return `<svg class="plan-2d" width="${W + 40}" height="${H + 20}" viewBox="-20 -10 ${W + 40} ${H + 20}" xmlns="http://www.w3.org/2000/svg">
    <rect x="0" y="0" width="${W}" height="${H}" fill="#eef1f5" stroke="#14213d" stroke-width="2"/>
    <rect x="0" y="0" width="${decal}" height="${H}" fill="#9aa3ad"/><rect x="${W - decal}" y="0" width="${decal}" height="${H}" fill="#9aa3ad"/>${rects}
    <text transform="translate(-6 ${H / 2}) rotate(-90)" font-size="9" text-anchor="middle" font-family="Arial">COULOIR GAUCHE</text>
    <text transform="translate(${W + 12} ${H / 2}) rotate(90)" font-size="9" text-anchor="middle" font-family="Arial">COULOIR DROITE</text>
    <text x="${W / 2}" y="${H + 9}" font-size="9" text-anchor="middle" font-family="Arial">AVANT (y = 0)</text>
  </svg>`;
}
function ouvrirFiche() {
  const res = donnees();
  if (!res) return;
  const pals = etat.paletteCourante === 'ensemble' ? res.palettes : res.palettes.filter((p) => p.numero === etat.paletteCourante);
  const date = new Date().toLocaleString('fr-FR');
  const html = pals.map((p) => {
    const tri = [...p.placements].sort((a, b) => a.z - b.z || a.y - b.y || a.x - b.x);
    const lignes = tri.map((pl) => `<tr><td>${echapper(pl.colis.numero)}</td><td>${nombre(pl.colis.longueur, 1)} × ${nombre(pl.colis.largeur, 1)} × ${nombre(pl.colis.hauteur, 1)}</td>
      <td>${nombre(pl.colis.masse, 1)}</td><td>${pl.colis.classe ? `<span class="danger">${echapper(pl.colis.classe)}</span> ${echapper(libelleClasse(pl.colis.classe))}` : '—'}</td>
      <td>${echapper(libelleEmballage(pl.colis.emballage))}${pl.colis.fragile ? ' · <strong>fragile</strong>' : ''}</td>
      <td>${nombre(pl.dx)} × ${nombre(pl.dy)} × ${nombre(pl.dz)}</td><td>${nombre(pl.x)} ; ${nombre(pl.y)} ; ${nombre(pl.z)}</td><td>${echapper(pl.colis.commentaire || '')}</td></tr>`).join('');
    const avert = res.verification.filter((m) => m.palette === p.numero).map((m) => `<div class="avert">${m.niveau === 'erreur' ? '<strong>Erreur</strong>' : 'Attention'} : ${echapper(m.message)}</div>`).join('');
    const iata = p.classes.filter((c) => etat.classes[c]?.iata);
    return `<div class="page-fiche">
      <div class="entete-fiche"><div><h1>Fiche palette n° ${p.numero}</h1><div>${echapper(p.libelle_type)} · ${p.zone === 'rampe' ? 'RAMPE' : 'soute'} · établie le ${date}</div></div>
      <div style="text-align:right"><strong>Palettisation A400M</strong><br>ETAA – transit aérien</div></div>
      <div class="resume-fiche">
        <div><div class="cle">Masse nette (kg)</div><div class="valeur">${nombre(p.masse, 1)}</div></div>
        <div><div class="cle">Masse brute estimée (kg)</div><div class="valeur">${nombre(p.masse_brute, 1)}</div></div>
        <div><div class="cle">Hauteur (cm)</div><div class="valeur">${nombre(p.hauteur, 1)}</div></div>
        <div><div class="cle">Colis</div><div class="valeur">${p.nombre_colis}</div></div>
      </div>
      <p><strong>Matières dangereuses :</strong> ${iata.length ? iata.map((c) => `${echapper(c)} (${echapper(libelleClasse(c))})`).join(', ') + ' — posées contre un bord côté couloir, étiquette visible' : 'aucune'}${p.classes.some((c) => ['ARME', 'SECU'].includes(c)) ? ' · <strong>armement</strong>' : ''}</p>
      ${plan2D(p)}
      <table><thead><tr><th>N° colis</th><th>Dimensions L × l × h (cm)</th><th>kg</th><th>Classe</th><th>Emballage</th><th>Posé X × Y × Z (cm)</th><th>Position x ; y ; z (cm)</th><th>Commentaire</th></tr></thead><tbody>${lignes}</tbody></table>
      ${avert}
      <div class="signature"><div>Confectionnée par (nom, grade)</div><div>Contrôlée par (nom, grade) – signature</div></div>
    </div>`;
  }).join('<hr style="page-break-after:always;border:none">');
  $('#contenu-fiche').innerHTML = html;
  $('#dlg-fiche').showModal();
}

// ---------------------------------------------------------------- initialisation
async function initialiser() {
  etat.config = await api('/api/config');
  for (const c of etat.config.classes) etat.classes[c.code] = c;
  etat.parametres = { ...etat.config.parametres };
  chargerLocal();
  // Menu déroulant des classes
  const sel = $('#f-classe');
  for (const c of etat.config.classes) {
    const o = document.createElement('option');
    o.value = c.code; o.textContent = c.libelle;
    sel.appendChild(o);
  }
  const selEmb = $('#f-emballage');
  for (const e of etat.config.emballages) {
    const o = document.createElement('option');
    o.value = e.code; o.textContent = e.libelle + (e.ecrasable ? ' — ne pas écraser' : '');
    selEmb.appendChild(o);
  }
  remplirSelectMoteur();
  if (!etat.config.moteurs.find((m) => m.code === etat.parametres.moteur && m.disponible)) etat.parametres.moteur = 'heuristique';

  vue = new Vue3D($('#vue3d'), {
    surSelection: (placement, palette) => {
      if (placement) selectionnerColis(placement.colis.numero, palette.numero, true);
      else { etat.selection = null; afficherInfoColis(null); afficherListe(); afficherInfoPalette(etat.paletteCourante === 'ensemble' ? null : etat.paletteCourante); }
    },
    surSurvol: (info) => {
      let bulle = $('#vue3d .info-bulle');
      if (!info) { if (bulle) bulle.remove(); return; }
      if (!bulle) { bulle = document.createElement('div'); bulle.className = 'info-bulle'; $('#vue3d').appendChild(bulle); }
      const r = $('#vue3d').getBoundingClientRect();
      bulle.style.left = `${info.x - r.left}px`; bulle.style.top = `${info.y - r.top}px`;
      const c = info.placement.colis;
      bulle.textContent = `${c.numero} · ${nombre(c.masse, 1)} kg · ${nombre(info.placement.dx)}×${nombre(info.placement.dy)}×${nombre(info.placement.dz)} cm${c.classe ? ' · ' + c.classe : ''}`;
    },
  });

  // Formulaire
  reinitialiserFormulaire();
  afficherListe();
  $('#formulaire-colis').addEventListener('submit', surSoumission);
  $('#btn-annuler-edition').addEventListener('click', reinitialiserFormulaire);
  $('#f-classe').addEventListener('change', majAideClasse);
  $('#table-colis').addEventListener('click', surClicListe);
  $('#btn-vider').addEventListener('click', () => {
    if (!etat.colis.length || confirm('Supprimer tous les colis de la liste ?')) {
      etat.colis = []; etat.resultat = null; etat.selection = null; sauvegarderLocal(); afficherListe(); afficherOnglets();
      $('#message-vue').hidden = false; vue.vider(); vue.demanderRendu(); afficherMessages(); $('#grille-bilan').innerHTML = '<p class="vide">Lancez un calcul pour obtenir le bilan.</p>'; $('#boutons-export').hidden = true;
      afficherInfoColis(null); afficherInfoPalette(null); $('#legende').innerHTML = ''; afficherEtapes(); afficherJournal(); reinitialiserFormulaire();
    }
  });
  const selExemple = $('#sel-exemple');
  for (const ex of etat.config.exemples) {
    const o = document.createElement('option');
    o.value = ex.cle; o.textContent = ex.titre; o.title = ex.description;
    selExemple.appendChild(o);
  }
  selExemple.addEventListener('change', async () => {
    const nom = selExemple.value;
    selExemple.value = '';
    if (!nom) return;
    if (etat.colis.length && !confirm('Remplacer la liste actuelle par cet exemple ?')) return;
    const rep = await api('/api/exemple?nom=' + encodeURIComponent(nom));
    const garde = { moteur: etat.parametres.moteur, duree_recherche: etat.parametres.duree_recherche, temps_limite_scip: etat.parametres.temps_limite_scip };
    etat.parametres = { ...etat.config.parametres, ...garde, ...(rep.parametres || {}) };
    etat.colis = rep.colis; sauvegarderLocal(); afficherListe(); reinitialiserFormulaire();
    notifier(`${rep.titre} : ${rep.colis.length} ligne(s) chargée(s)` + (rep.parametres ? ' avec les paramètres du notebook' : '')
      + ((rep.avertissements || []).length ? ` — ${rep.avertissements[0]}` : '') + '. Cliquez sur « Confectionner les palettes ».');
  });
  $('#btn-import-remplacer').addEventListener('click', () => validerImportExcel(true));
  $('#btn-import-ajouter').addEventListener('click', () => validerImportExcel(false));
  $('#btn-import-annuler').addEventListener('click', () => { importEnAttente = null; $('#dlg-import').close(); });
  $('#btn-importer').addEventListener('click', () => $('#fichier-import').click());
  $('#fichier-import').addEventListener('change', (e) => { if (e.target.files[0]) importerFichier(e.target.files[0]); e.target.value = ''; });
  $('#btn-enregistrer').addEventListener('click', () => exporter('projet'));
  $('#btn-exporter-colis').addEventListener('click', () => exporter('colis_csv'));
  $('#btn-optimiser').addEventListener('click', optimiser);
  $('#btn-plan-csv').addEventListener('click', () => exporter('plan_csv', { contenu: planCsv() }));
  $('#btn-plan-json').addEventListener('click', () => exporter('plan_json', { resultat: etat.resultat }));
  $('#btn-fiche').addEventListener('click', ouvrirFiche);
  $('#btn-fiche-fermer').addEventListener('click', () => $('#dlg-fiche').close());
  $('#btn-fiche-imprimer').addEventListener('click', () => window.print());

  // Paramètres
  $('#btn-parametres').addEventListener('click', ouvrirParametres);
  $('#btn-params-annuler').addEventListener('click', () => $('#dlg-parametres').close());
  $('#btn-params-defaut').addEventListener('click', () => { etat.parametres = { ...etat.config.parametres }; ouvrirParametres(); });
  $('[data-param=passagers]').addEventListener('input', majAidePassagers);
  for (const k of ['largeur_palette', 'reduction_couloir', 'derogation_couloir']) $(`[data-param=${k}]`).addEventListener('input', majAideLargeurUtile);
  $('#select-moteur').addEventListener('change', majAideMoteur);
  $('#form-parametres').addEventListener('submit', (e) => {
    e.preventDefault();
    etat.parametres = lireParametres();
    sauvegarderLocal();
    majAideClasse();
    $('#dlg-parametres').close();
    notifier('Paramètres enregistrés. Relancez le calcul pour les appliquer.');
  });

  // Vue 3D
  $$('button.mode').forEach((b) => b.addEventListener('click', () => {
    $$('button.mode').forEach((x) => x.classList.toggle('actif', x === b));
    etat.mode = b.dataset.mode; vue.setMode(etat.mode); afficherLegende();
  }));
  $('#opt-etiquettes').addEventListener('change', (e) => vue.setEtiquettes(e.target.checked));
  $('#opt-opacite').addEventListener('input', (e) => vue.setOpacite(parseInt(e.target.value, 10) / 100));
  $('#btn-camera').addEventListener('click', () => vue.recadrer(false));
  $('#btn-dessus').addEventListener('click', () => vue.recadrer(true));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { etat.selection = null; vue.selectionner(null); afficherInfoColis(null); afficherListe(); }
  });

  // Accès pour les tests automatisés
  window.palettiseur = { etat, optimiser, selectionnerColis, vue: () => vue };
}

initialiser().catch((e) => { console.error(e); notifier('Initialisation impossible : ' + e.message, true); });
