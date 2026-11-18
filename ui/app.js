/* ===================== Palettisation A400M – logique de l'interface ===================== */
import { Vue3D, couleurPoids } from './vue3d.js';
import { initialiserSeparateurs } from './separateurs.js';
import { dessinerPlanSoute, emplacementsPlan } from './plan_soute.js';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const etat = {
  config: null,          // classes, incompatibilités, paramètres par défaut…
  classes: {},           // code -> {libelle, couleur, iata}
  colis: [],             // liste saisie
  parametres: {},
  resultat: null,        // dernier plan calculé
  paletteCourante: 'ensemble',
  etape: 'finale',       // étape du pipeline affichée : 'heuristique', 'apres_vnd' ou 'finale'
  mode: 'categories',
  selection: null,       // {numero, palette}
  edition: null,         // index du colis en cours de modification
  perime: false,         // la liste ou les paramètres ont changé depuis le dernier calcul
};
let brouillonIndispo = new Set();   // emplacements déclarés indisponibles dans la boîte de dialogue des paramètres
const PARAMETRES_OBSOLETES = ['derogation_couloir', 'duree_recherche', 'emballages_ecrasables', 'charge_max_carton'];

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
    localStorage.setItem('palettiseur.parametres.v5', JSON.stringify(etat.parametres));
  } catch (e) { /* stockage indisponible : on ignore */ }
}
function chargerLocal() {
  try {
    const c = localStorage.getItem('palettiseur.colis');
    if (c) etat.colis = JSON.parse(c).map(({ renversable, ...reste }) => reste);
    const p = localStorage.getItem('palettiseur.parametres.v5');
    if (p) etat.parametres = { ...etat.parametres, ...JSON.parse(p) };
    for (const k of PARAMETRES_OBSOLETES) delete etat.parametres[k];
  } catch (e) { /* ignore */ }
}
/** Données de l'étape affichée : palettes, vérification et bilan de cette étape, reste du résultat commun. */
function donnees() {
  const res = etat.resultat;
  if (!res) return null;
  if (etat.etape === 'finale') return res;
  const e = (res.etapes || []).find((x) => x.cle === etat.etape);
  return e && e.palettes ? { ...res, palettes: e.palettes, verification: e.verification, bilan: e.bilan, indicateurs: e.indicateurs } : res;
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
  $('#f-dat').value = '';
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
    dat: $('#f-dat').value.trim(),
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
  $('#f-dat').value = c.dat || '';
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
    : 'Étiquette IATA : le colis reste visible depuis au moins un des deux côtés de la palette. ';
  txt += incomp.length ? `Jamais sur la même palette que : ${incomp.join(', ')}.` : 'Compatible avec toutes les autres classes.';
  aide.textContent = txt;
}
function surSoumission(e) {
  e.preventDefault();
  const { colis, erreurs } = lireFormulaire();
  const zoneErreur = $('#erreur-formulaire');
  if (erreurs.length) { zoneErreur.textContent = 'Corrigez : ' + erreurs.join(' ; ') + '.'; zoneErreur.hidden = false; return; }
  if (etat.edition !== null) etat.colis[etat.edition] = colis; else etat.colis.push(colis);
  sauvegarderLocal(); marquerPerime();
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
function optionsClasse(courante) {
  return etat.config.classes.map((cl) => `<option value="${echapper(cl.code)}"${cl.code === (courante || '') ? ' selected' : ''}>${echapper(cl.code || '—')}</option>`).join('');
}
function couleurClasse(code) {
  return etat.classes[code]?.couleur || etat.classes['']?.couleur || '#b8c2cc';
}
function majTotaux() {
  let masse = 0, volume = 0, nb = 0;
  for (const c of etat.colis) {
    const q = c.quantite || 1;
    masse += c.masse * q; volume += c.longueur * c.largeur * c.hauteur * q / 1e6; nb += q;
  }
  $('#compteur-colis').textContent = nb;
  $('#liste-vide').hidden = etat.colis.length > 0;
  $('#masse-totale').textContent = `${nombre(masse, 1)} kg`;
  $('#volume-total').textContent = `${nombre(volume, 2)} m³`;
}
function marquerPerime() {
  if (!etat.resultat) return;
  etat.perime = true;
  $('#bilan-perime').hidden = false;
}
function afficherListe() {
  const corps = $('#table-colis tbody');
  corps.innerHTML = '';
  etat.colis.forEach((c, i) => {
    const q = c.quantite || 1;
    const tr = document.createElement('tr');
    tr.className = 'ligne-colis' + (etat.selection && etat.selection.base === c.numero ? ' selectionnee' : '');
    tr.dataset.index = i;
    const pals = palettesDuColis(c);
    const palTxt = pals.length ? [...new Set(pals)].map((p) => (p === '—' ? '<span class="danger">non placé</span>' : `P${p}`)).join(', ') : '';
    const info = [c.commentaire, c.emballage ? `emballage : ${libelleEmballage(c.emballage)}` : '', c.reference ? `n° de série ${c.reference}` : ''].filter(Boolean).join(' · ');
    const nb = (champ, valeur, pas, min, titre) => `<td class="c-nb"><input class="edit" type="number" data-champ="${champ}" value="${valeur}" step="${pas}" min="${min}" aria-label="${titre}" title="${titre}"></td>`;
    tr.innerHTML = `
      <td class="c-num"><span class="puce" style="background:${couleurClasse(c.classe)}" title="${echapper(libelleClasse(c.classe))}"></span><input class="edit" type="text" data-champ="numero" value="${echapper(c.numero)}" aria-label="Numéro" title="${echapper(info || 'Numéro du colis')}"></td>
      <td class="c-qte"><input class="edit" type="number" data-champ="quantite" value="${q}" step="1" min="1" aria-label="Quantité" title="Quantité"></td>
      ${nb('longueur', c.longueur, 0.5, 0.1, 'Longueur (cm)')}${nb('largeur', c.largeur, 0.5, 0.1, 'Largeur (cm)')}${nb('hauteur', c.hauteur, 0.5, 0.1, 'Hauteur (cm)')}${nb('masse', c.masse, 0.1, 0.1, 'Masse (kg)')}
      <td class="c-classe"><select class="edit" data-champ="classe" aria-label="Classe" title="${echapper(libelleClasse(c.classe))}">${optionsClasse(c.classe)}</select></td>
      <td class="c-fragile"><input type="checkbox" data-champ="fragile" aria-label="Fragile" title="Fragile : rien dessus"${c.fragile ? ' checked' : ''}></td>
      <td class="c-dat"><input class="edit" type="text" data-champ="dat" value="${echapper(c.dat || '')}" placeholder="—" aria-label="DAT" title="DAT"></td>
      <td class="c-palette">${palTxt}</td>
      <td class="actions-ligne"><button class="lien" data-action="editer" title="Modifier tous les champs (commentaire, emballage…)">✎</button><button class="lien danger" data-action="supprimer" title="Supprimer">🗑</button></td>`;
    corps.appendChild(tr);
  });
  majTotaux();
}
function erreurListe(message) {
  const z = $('#erreur-liste');
  z.textContent = message || '';
  z.hidden = !message;
  clearTimeout(erreurListe._t);
  if (message) erreurListe._t = setTimeout(() => { z.hidden = true; }, 6000);
}
/** Vérifie la valeur saisie dans une case de la liste ; retourne la valeur à enregistrer ou lève une erreur lisible. */
function valeurChamp(champ, el, i) {
  const brut = el.type === 'checkbox' ? el.checked : el.value.trim();
  if (champ === 'fragile') return !!brut;
  if (champ === 'classe' || champ === 'dat') return String(brut);
  if (champ === 'numero') {
    if (!brut) throw new Error('le numéro est obligatoire');
    if (etat.colis.some((x, k) => k !== i && x.numero === brut)) throw new Error(`le numéro « ${brut} » existe déjà`);
    return brut;
  }
  const v = parseFloat(String(brut).replace(',', '.'));
  const noms = { longueur: 'la longueur', largeur: 'la largeur', hauteur: 'la hauteur', masse: 'la masse', quantite: 'la quantité' };
  if (!(v > 0)) throw new Error(`${noms[champ]} doit être un nombre positif`);
  if (champ === 'quantite') {
    if (!Number.isInteger(v)) throw new Error('la quantité doit être un nombre entier');
    return v;
  }
  return v;
}
function surModifListe(e) {
  const el = e.target.closest('[data-champ]');
  const tr = e.target.closest('tr.ligne-colis');
  if (!el || !tr) return;
  const i = parseInt(tr.dataset.index, 10);
  const champ = el.dataset.champ;
  const c = etat.colis[i];
  try {
    const valeur = valeurChamp(champ, el, i);
    if (valeur === (c[champ] ?? (champ === 'quantite' ? 1 : champ === 'fragile' ? false : ''))) { erreurListe(''); return; }
    const ancienNumero = c.numero;
    c[champ] = valeur;
    erreurListe('');
    if (champ === 'classe') {
      const puce = tr.querySelector('.puce');
      puce.style.background = couleurClasse(valeur); puce.title = libelleClasse(valeur);
      el.title = libelleClasse(valeur);
    }
    if (champ === 'numero' && etat.selection && etat.selection.base === ancienNumero) etat.selection = null;
    if (etat.edition === i) remplirFormulaire(c);
    sauvegarderLocal(); majTotaux(); marquerPerime();
  } catch (erreur) {
    // valeur refusée : on remet l'ancienne et on le signale un instant
    const ancien = el.type === 'checkbox' ? !el.checked : (c[champ] ?? '');
    if (el.type === 'checkbox') el.checked = ancien; else el.value = ancien;
    el.classList.add('invalide');
    setTimeout(() => el.classList.remove('invalide'), 1600);
    erreurListe(`Colis ${c.numero} : ${erreur.message}.`);
  }
}
function surToucheListe(e) {
  const el = e.target.closest('input.edit');
  const tr = e.target.closest('tr.ligne-colis');
  if (!el || !tr) return;
  if (e.key === 'Enter') {
    e.preventDefault();
    const suivante = (e.shiftKey ? tr.previousElementSibling : tr.nextElementSibling)?.querySelector(`[data-champ="${el.dataset.champ}"]`);
    el.blur();                                  // valide la case (événement change), puis passe au colis suivant
    if (suivante) { suivante.focus(); suivante.select?.(); }
  } else if (e.key === 'Escape') {
    e.stopPropagation();
    const c = etat.colis[parseInt(tr.dataset.index, 10)];
    el.value = c[el.dataset.champ] ?? (el.dataset.champ === 'quantite' ? 1 : '');
    el.blur();
  }
}
function surClicListe(e) {
  if (e.target.closest('input, select')) return;              // on édite une case : pas de sélection dans la vue 3D
  const tr = e.target.closest('tr.ligne-colis');
  if (!tr) return;
  const i = parseInt(tr.dataset.index, 10);
  const action = e.target.dataset.action;
  if (action === 'supprimer') {
    etat.colis.splice(i, 1);
    if (etat.edition === i) reinitialiserFormulaire();
    sauvegarderLocal(); marquerPerime(); afficherListe(); return;
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
  brouillonIndispo = new Set((etat.parametres.emplacements_indisponibles || []).map(Number));
  majAidePassagers();
  majAideMoteur();
  majAideLargeurUtile();
  majPlanParametres();
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
  p.emplacements_indisponibles = [...brouillonIndispo].sort((a, b) => a - b);
  return p;
}
function majAideLargeurUtile() {
  const val = (k) => parseFloat($(`[data-param=${k}]`).value) || 0;
  const axe = $('[data-param=axe_cotes]').value === 'x' ? 'x' : 'y';
  const couloir = val('reduction_couloir');
  const r = 2 * couloir;
  const ux = val('largeur_palette') - (axe === 'x' ? r : 0), uy = val('longueur_palette') - (axe === 'y' ? r : 0);
  $('#aide-largeur-utile').textContent = couloir > 0
    ? `Zone utile d'une palette de soute : ${nombre(ux)} × ${nombre(uy)} cm (x × y) : couloir de ${nombre(couloir)} cm de chaque côté, sur l'axe ${axe}.`
    : `Zone utile d'une palette de soute : ${nombre(ux)} × ${nombre(uy)} cm (x × y), sans couloir (comme le notebook).`;
}
function majAidePassagers() {
  const pax = parseInt($('[data-param=passagers]').value || '0', 10);
  let positions = 0;
  for (const v of etat.config.versions_avion) { if (pax <= v.passagers_max) { positions = v.palettes; break; } }
  $('#aide-passagers').textContent = pax > 116 ? 'Plus de 116 passagers : aucun emplacement de palette (version tout passagers).'
    : `Version A400M : ${positions} emplacement(s) de palette au plus pour ${pax} passager(s) ; les sièges prennent les emplacements de l'avant.`;
}
function majAideMoteur() {
  const code = $('#select-moteur').value;
  const m = etat.config.moteurs.find((x) => x.code === code);
  $('#aide-moteur').textContent = !m ? '' : m.code === 'notebook'
    ? 'Le code de CDP_5 exécuté tel quel : cinq essais d\'heuristique, puis la VND (transferts, replacements, échanges) pendant le budget indiqué, puis recentrage et tassement horizontal, avec un contrôle indépendant après chaque phase. Indisponible avec BIPAL / TRIPAL ou au-delà de 150 colis : le moteur rapide prend alors le relais.'
    : 'Réimplémentation rapide de l\'application : heuristique multi-départ, fusion, tassement vertical, recentrage et tassement horizontal. Chaque phase de recherche peut utiliser le budget de calcul, mais s\'arrête dès que plus rien ne s\'améliore ; pas de regroupement par DAT.';
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
/** Plan de la soute dans la boîte de dialogue : reflète les valeurs en cours de saisie. */
function majPlanParametres() {
  const p = lireParametres();
  const emps = emplacementsPlan(p, etat.config.versions_avion);
  dessinerPlanSoute($('#plan-parametres'), emps, { surClic: basculerEmplacement });
  const libres = emps.filter((e) => e.etat === 'libre');
  const nS = libres.filter((e) => e.zone === 'soute').length, nR = libres.filter((e) => e.zone === 'rampe').length;
  const indispo = emps.filter((e) => e.etat === 'indisponible').length, sieges = emps.filter((e) => e.etat === 'passagers').length;
  $('#aide-plan').textContent = `${libres.length} emplacement(s) libre(s) : ${nS} en soute (jusqu'à ${nombre(Number(p.masse_max_soute) - (Number(p.tare_palette) || 0))} kg) et ${nR} sur la rampe (jusqu'à ${nombre(Number(p.masse_max_rampe) - (Number(p.tare_palette) || 0))} kg).`
    + (indispo ? ` ${indispo} indisponible(s).` : '') + (sieges ? ` ${sieges} pris par les sièges des passagers.` : '');
}
function basculerEmplacement(e) {
  if (e.etat === 'passagers') { notifier('Cet emplacement est pris par les sièges : réduisez le nombre de passagers pour le libérer.'); return; }
  if (brouillonIndispo.has(e.numero)) brouillonIndispo.delete(e.numero); else brouillonIndispo.add(e.numero);
  majPlanParametres();
}
function appliquerReglesProjet() {
  const champ = (k) => $(`[data-param=${k}]`);
  champ('reduction_couloir').value = 14;
  champ('iata_au_bord').checked = true;
  champ('facteur_masse').value = 1;
  majAideLargeurUtile();
}

// ---------------------------------------------------------------- optimisation
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function duree(s) {
  s = Math.max(0, Math.round(s));
  return s >= 60 ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s` : `${s} s`;
}
function afficherAvancement(e) {
  const barre = $('#chargement-barre');
  const phase = $('#chargement-phase');
  const temps = $('#chargement-temps');
  phase.innerHTML = `<strong>${echapper(e.libelle)}</strong>`;
  if (e.phase === 'vnd' && e.budget_phase) {
    const part = Math.min(1, e.ecoule_phase / e.budget_phase);
    barre.className = ''; barre.style.width = `${Math.round(part * 100)}%`;
    temps.textContent = `VND : ${duree(e.ecoule_phase)} sur ${duree(e.budget_phase)} de budget (elle s'arrête avant si plus rien ne s'améliore) · temps total ${duree(e.ecoule)}`;
  } else {
    barre.className = 'indeterminee'; barre.style.width = '';
    temps.textContent = `Temps écoulé : ${duree(e.ecoule)} · budget de la VND : ${duree(e.budget)}`;
  }
}
/** Interroge le serveur jusqu'à la fin du calcul, en affichant la phase en cours. */
async function attendreCalcul(id) {
  let echecs = 0;
  for (;;) {
    await pause(500);
    let e;
    try { e = await api(`/api/optimiser_etat?id=${encodeURIComponent(id)}`); echecs = 0; } catch (erreur) {
      if (++echecs >= 4) throw erreur;
      continue;
    }
    if (e.etat === 'termine') return e.resultat;
    if (e.etat === 'erreur') throw new Error(e.erreur);
    afficherAvancement(e);
  }
}
async function optimiser() {
  if (!etat.colis.length) { notifier('Ajoutez d\'abord des colis.', true); return; }
  $('#chargement').hidden = false;
  $('#btn-optimiser').disabled = true;
  afficherAvancement({ libelle: 'Préparation des colis', phase: 'preparation', ecoule: 0, ecoule_phase: 0, budget: etat.parametres.temps_limite });
  try {
    const dem = await api('/api/optimiser_demarrer', { colis: etat.colis, parametres: etat.parametres });
    const res = await attendreCalcul(dem.id);
    res._indexColis = {};
    for (const pal of res.palettes) for (const p of pal.placements) res._indexColis[p.colis.numero] = pal.numero;
    etat.resultat = res;
    etat.perime = false;
    $('#bilan-perime').hidden = true;
    etat.selection = null;
    etat.etape = 'finale';
    etat.paletteCourante = res.palettes.length === 1 ? 1 : 'ensemble';
    afficherEtapes();
    afficherJournal();
    afficherOnglets();
    afficherVue();
    afficherBilan();
    afficherQualite();
    afficherMessages();
    afficherListe();
    afficherInfoColis(null);
    const nbErr = res.verification.filter((m) => m.niveau === 'erreur').length;
    notifier(`${res.palettes.length} palette(s), ${res.bilan.positions_utilisees} emplacement(s), ${res.bilan.colis_places} colis placés`
      + (res.non_places.length ? `, ${res.non_places.length} non placé(s)` : '') + ` — calcul en ${duree(res.duree)}`
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
  afficherEtapes(); afficherOnglets(); afficherVue(); afficherBilan(); afficherQualite(); afficherMessages(); afficherInfoColis(null);
}
function afficherJournal() {
  const res = etat.resultat;
  const carte = $('#carte-journal');
  const ol = $('#liste-journal');
  ol.innerHTML = '';
  carte.hidden = !res || !(res.journal && res.journal.length);
  $('#separateur-journal').hidden = carte.hidden;
  if (carte.hidden) return;
  const cibles = { 1: 'heuristique', 2: 'apres_vnd', 4: 'finale' };
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
    b.addEventListener('click', () => { etat.paletteCourante = it.id === 'ensemble' ? 'ensemble' : parseInt(it.id, 10); afficherOnglets(); afficherVue(); surlignerQualite(); });
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
    ligne('Calcul', `${res.moteur === 'notebook' ? `${res.iterations} essais d'heuristique` : `${res.iterations} constructions`} · ${duree(res.duree)} · ${res.moteur === 'notebook' ? 'code du notebook CDP_5' : 'moteur rapide'}`),
  ].join('');
  $('#boutons-export').hidden = false;
  $('#info-export').hidden = true;
  const plan = $('#plan-bilan');
  plan.hidden = false;
  dessinerPlanSoute(plan, b.plan_soute, {
    titre: 'Occupation des emplacements de l\'avion', compact: true,
    surClic: (e) => { if (e.palette) { etat.paletteCourante = e.palette; afficherOnglets(); afficherVue(); surlignerQualite(); } },
  });
}
const formatSigne = (v, dec = 3) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toLocaleString('fr-FR', { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
const formatM = (v, dec = 3) => v.toLocaleString('fr-FR', { minimumFractionDigits: dec, maximumFractionDigits: dec });
const formatPct = (v) => (100 * v).toLocaleString('fr-FR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
/** Tableau de qualité des palettes de l'étape affichée : les indicateurs de la visualisation du notebook. */
function afficherQualite() {
  const panneau = $('#panneau-qualite');
  const sep = $('#separateur-qualite');
  const res = donnees();
  const visible = !!(res && res.palettes.length && res.indicateurs);
  panneau.hidden = sep.hidden = !visible;
  if (!visible) return;
  const ind = res.indicateurs;
  const etape = (etat.resultat.etapes || []).find((e) => e.cle === etat.etape);
  $('#qualite-etape').textContent = etape ? `· ${etape.titre}` : '';
  $('#qualite-resume').textContent = `Palettes : ${ind.nombre_palettes} | Hauteur maximale : ${formatM(ind.hauteur_maximale)} m | Somme des hauteurs : ${formatM(ind.somme_hauteurs)} m`;
  const corps = $('#table-qualite tbody');
  corps.innerHTML = '';
  for (const m of ind.palettes) {
    const pal = res.palettes.find((p) => p.numero === m.palette);
    const tr = document.createElement('tr');
    tr.dataset.palette = m.palette;
    tr.title = 'Afficher cette palette';
    const barre = (v) => `<span class="mini-barre"><i style="width:${Math.min(100, 100 * v)}%"></i></span>`;
    tr.innerHTML = `<td><strong>${m.palette}</strong> <span class="aide">${pal ? (pal.zone === 'rampe' ? 'rampe' : 'soute') : ''}</span></td>
      <td>${pal && pal.emplacements.length ? pal.emplacements.join(', ') : '—'}</td>
      <td>${m.nombre_colis}</td>
      <td>${nombre(m.masse, 1)} <span class="aide">/ ${nombre(m.masse_max)}</span>${barre(m.taux_masse)}</td>
      <td>${formatM(m.hauteur)}</td>
      <td>${formatPct(m.compacite)}${barre(m.compacite)}</td>
      <td>${formatPct(m.remplissage_volumique)}${barre(m.remplissage_volumique)}</td>
      <td>${formatPct(m.taux_surface_sol)}${barre(m.taux_surface_sol)}</td>
      <td>${formatSigne(m.centre_masse_x)}</td><td>${formatSigne(m.centre_masse_y)}</td>
      <td>${formatM(m.decalage_horizontal)}</td>`;
    tr.addEventListener('click', () => { etat.paletteCourante = m.palette; afficherOnglets(); afficherVue(); surlignerQualite(); });
    corps.appendChild(tr);
  }
  // Comparaison des états du calcul (heuristique, après la VND, solution finale)
  const etapes = (etat.resultat.etapes || []).filter((e) => e.indicateurs);
  const zone = $('#qualite-etapes');
  if (etapes.length < 2) { zone.innerHTML = ''; } else {
    zone.innerHTML = `<table><thead><tr><th>État du calcul</th><th>Palettes</th><th>Hauteur maximale (m)</th><th>Somme des hauteurs (m)</th><th>Remplissage volumique moyen (%)</th></tr></thead><tbody>`
      + etapes.map((e) => `<tr class="${e.cle === etat.etape ? 'actif' : ''}" data-etape="${e.cle}" style="cursor:pointer" title="Afficher cet état"><td>${echapper(e.titre)}</td><td>${e.indicateurs.nombre_palettes}</td><td>${formatM(e.indicateurs.hauteur_maximale)}</td><td>${formatM(e.indicateurs.somme_hauteurs)}</td><td>${formatPct(e.indicateurs.remplissage_moyen)}</td></tr>`).join('')
      + '</tbody></table>';
    zone.querySelectorAll('tr[data-etape]').forEach((tr) => tr.addEventListener('click', () => changerEtape(tr.dataset.etape)));
  }
  surlignerQualite();
}
function surlignerQualite() {
  $$('#table-qualite tbody tr').forEach((tr) => tr.classList.toggle('selectionnee', String(tr.dataset.palette) === String(etat.paletteCourante)));
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
  const m = p.indicateurs;
  zone.innerHTML = `<div class="fiche-info"><dl>
    <dt>Type</dt><dd>${echapper(p.libelle_type)} · ${p.zone === 'rampe' ? '<span class="chip">rampe</span>' : 'soute'}${p.emplacements.length ? ` · emplacement ${p.emplacements.join(', ')}` : ' · <span class="danger">sans emplacement libre</span>'}</dd>
    <dt>Dimensions utiles</dt><dd>${nombre(p.largeur)} × ${nombre(p.longueur)} × ${nombre(p.hauteur_max)} cm${p.marge_x || p.marge_y ? ` <span class="aide">(plateau ${nombre(p.largeur_physique)} × ${nombre(p.longueur_physique)} cm, couloir de ${nombre(p.axe_cotes === 'x' ? p.marge_x : p.marge_y, 1)} cm de chaque côté, sur l'axe ${p.axe_cotes})</span>` : ''}</dd>
    <dt>Charge</dt><dd>${nombre(p.masse, 1)} / ${nombre(p.masse_max)} kg<div class="barre-jauge"><span class="${tauxMasse > 1 ? 'mauvais' : ''}" style="width:${Math.min(100, tauxMasse * 100)}%"></span></div></dd>
    ${p.masse_brute !== p.masse ? `<dt>Masse brute estimée</dt><dd>${nombre(p.masse_brute, 1)} kg <span class="aide">(avec ${p.positions} palette(s) vide(s))</span></dd>` : ''}
    <dt>Colis</dt><dd>${p.nombre_colis}</dd>
    <dt>Hauteur</dt><dd>${formatM(m.hauteur)} m</dd>
    <dt>Compacité</dt><dd>${formatPct(m.compacite)} % <span class="aide">(volume des colis / enveloppe du tas)</span></dd>
    <dt>Remplissage volumique</dt><dd>${formatPct(m.remplissage_volumique)} % <span class="aide">(volume des colis / volume de la palette)</span></dd>
    <dt>Surface au sol</dt><dd>${formatPct(m.taux_surface_sol)} % du plateau · ${formatM(m.surface_sol, 2)} m²</dd>
    <dt>Centre de masse</dt><dd>x = ${formatSigne(m.centre_masse_x)} m, y = ${formatSigne(m.centre_masse_y)} m <span class="aide">(depuis le centre du plateau)</span></dd>
    <dt>Décalage horizontal</dt><dd>${formatM(m.decalage_horizontal)} m <span class="aide">(0 = centré)</span></dd>
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
  const avecCouloir = (palette.marge_x || 0) > 0 || (palette.marge_y || 0) > 0;
  const nomCote = avecCouloir ? { gauche: 'couloir gauche', droite: 'couloir droit' } : { gauche: 'côté gauche', droite: 'côté droit' };
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
    <dt>Emballage</dt><dd>${echapper(libelleEmballage(c.emballage))}${c.fragile ? ' · <strong>fragile</strong>' : ''}</dd>
    ${c.reference ? `<dt>N° de série</dt><dd>${echapper(c.reference)}</dd>` : ''}
    ${c.dat ? `<dt>DAT</dt><dd>${echapper(c.dat)}</dd>` : ''}
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
  if (rep.parametres) {
    // projet enregistré par une version précédente : l'ancienne dérogation au couloir devient « pas de couloir »
    const anciens = { ...rep.parametres };
    if (anciens.derogation_couloir) anciens.reduction_couloir = 0;
    for (const k of PARAMETRES_OBSOLETES) delete anciens[k];
    etat.parametres = { ...etat.parametres, ...anciens };
  }
  sauvegarderLocal(); marquerPerime(); afficherListe();
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
        ${r.dat ? pastille(r.dat, 'DAT différents (les colis d\'un même DAT voyagent ensemble)') : ''}
      </ul>
      ${rep.avertissements.length ? `<p class="aide"><strong>À vérifier</strong></p><ul class="avertissements-import">${rep.avertissements.map((a) => `<li>${echapper(a)}</li>`).join('')}</ul>` : ''}
      <p class="aide">La température est ignorée. L'emballage est conservé à titre d'information : il ne change pas le placement. Le caractère fragile n'est pas déduit du fichier : cochez-le dans la liste sur les colis concernés. Vous pourrez corriger chaque colis dans la liste avant de lancer le calcul.</p>`;
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
  sauvegarderLocal(); marquerPerime(); afficherListe(); reinitialiserFormulaire();
  notifier(`${nouveaux.length} colis importés depuis ${att.nom}` + (att.colis.length > nouveaux.length ? ` (${att.colis.length - nouveaux.length} déjà présents, ignorés)` : '') + '. Corrigez-les si besoin dans la liste, puis cliquez sur « Confectionner les palettes ».');
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
  const lignes = [['palette', 'type_palette', 'zone', 'numero_colis', 'classe', 'masse_kg', 'x_cm', 'y_cm', 'z_cm', 'dim_x_cm', 'dim_y_cm', 'dim_z_cm', 'emballage', 'fragile', 'reference', 'dat', 'commentaire']];
  for (const pal of res.palettes) {
    const tri = [...pal.placements].sort((a, b) => a.z - b.z || a.y - b.y || a.x - b.x);
    for (const p of tri) lignes.push([pal.numero, pal.type, pal.zone, p.colis.numero, p.colis.classe, p.colis.masse, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.colis.emballage || '', p.colis.fragile ? 'oui' : 'non', p.colis.reference || '', p.colis.dat || '', p.colis.commentaire]);
  }
  for (const n of res.non_places) lignes.push(['', '', 'NON PLACÉ', n.colis.numero, n.colis.classe, n.colis.masse, '', '', '', n.colis.longueur, n.colis.largeur, n.colis.hauteur, n.colis.emballage || '', n.colis.fragile ? 'oui' : 'non', n.colis.reference || '', n.colis.dat || '', n.motif]);
  return lignes.map((l) => l.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(';')).join('\n');
}

// ---------------------------------------------------------------- fiche palette
function plan2D(p) {
  // Vue de dessus SVG : rectangles des colis (les plus hauts par-dessus), numéro au centre, couloirs en gris.
  const physX = p.largeur_physique || p.largeur, physY = p.longueur_physique || p.longueur;
  const W = 360, ech = W / physX, H = physY * ech;
  const dx = (p.marge_x || 0) * ech, dy = (p.marge_y || 0) * ech;
  const tri = [...p.placements].sort((a, b) => (a.z + a.dz) - (b.z + b.dz));
  const rects = tri.map((pl) => {
    const coul = etat.classes[pl.colis.classe]?.couleur || etat.classes['']?.couleur || '#b8c2cc';
    const x = dx + pl.x * ech, y = H - dy - (pl.y + pl.dy) * ech;
    return `<rect x="${x}" y="${y}" width="${pl.dx * ech}" height="${pl.dy * ech}" fill="${coul}" stroke="#1f2937" stroke-width="1"/>
      <text x="${x + pl.dx * ech / 2}" y="${y + pl.dy * ech / 2 + 4}" font-size="10" text-anchor="middle" font-family="Arial">${echapper(pl.colis.numero)}</text>`;
  }).join('');
  const nom = (dy > 0 || dx > 0) ? 'COULOIR' : 'CÔTÉ';
  const bandes = p.axe_cotes === 'x'
    ? `<rect x="0" y="0" width="${dx}" height="${H}" fill="#9aa3ad"/><rect x="${W - dx}" y="0" width="${dx}" height="${H}" fill="#9aa3ad"/>
       <text transform="translate(-6 ${H / 2}) rotate(-90)" font-size="9" text-anchor="middle" font-family="Arial">${nom} GAUCHE (x = 0)</text>
       <text transform="translate(${W + 12} ${H / 2}) rotate(90)" font-size="9" text-anchor="middle" font-family="Arial">${nom} DROIT (x maximal)</text>`
    : `<rect x="0" y="0" width="${W}" height="${dy}" fill="#9aa3ad"/><rect x="0" y="${H - dy}" width="${W}" height="${dy}" fill="#9aa3ad"/>
       <text x="${W / 2}" y="${H + 9}" font-size="9" text-anchor="middle" font-family="Arial">${nom} GAUCHE (y = 0)</text>
       <text x="${W / 2}" y="-3" font-size="9" text-anchor="middle" font-family="Arial">${nom} DROIT (y maximal)</text>`;
  return `<svg class="plan-2d" width="${W + 40}" height="${H + 20}" viewBox="-20 -10 ${W + 40} ${H + 20}" xmlns="http://www.w3.org/2000/svg">
    <rect x="0" y="0" width="${W}" height="${H}" fill="#eef1f5" stroke="#14213d" stroke-width="2"/>${bandes}${rects}
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
        <div><div class="cle">Compacité (%)</div><div class="valeur">${formatPct(p.indicateurs.compacite)}</div></div>
        <div><div class="cle">Remplissage volumique (%)</div><div class="valeur">${formatPct(p.indicateurs.remplissage_volumique)}</div></div>
        <div><div class="cle">Décalage du centre de masse (m)</div><div class="valeur">${formatM(p.indicateurs.decalage_horizontal)}</div></div>
        <div><div class="cle">Emplacement(s)</div><div class="valeur">${p.emplacements.length ? p.emplacements.join(', ') : '—'}</div></div>
      </div>
      <p><strong>Matières dangereuses :</strong> ${iata.length ? iata.map((c) => `${echapper(c)} (${echapper(libelleClasse(c))})`).join(', ') + ' — étiquette visible depuis un côté' : 'aucune'}${p.classes.some((c) => ['ARME', 'SECU'].includes(c)) ? ' · <strong>armement</strong>' : ''}</p>
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
    o.value = e.code; o.textContent = e.libelle;
    selEmb.appendChild(o);
  }
  remplirSelectMoteur();
  if (!etat.config.moteurs.find((m) => m.code === etat.parametres.moteur && m.disponible)) etat.parametres.moteur = 'notebook';

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

  initialiserSeparateurs();

  // Formulaire
  reinitialiserFormulaire();
  afficherListe();
  $('#formulaire-colis').addEventListener('submit', surSoumission);
  $('#btn-annuler-edition').addEventListener('click', reinitialiserFormulaire);
  $('#f-classe').addEventListener('change', majAideClasse);
  $('#table-colis').addEventListener('click', surClicListe);
  $('#table-colis').addEventListener('change', surModifListe);
  $('#table-colis').addEventListener('keydown', surToucheListe);
  $('#btn-vider').addEventListener('click', () => {
    if (!etat.colis.length || confirm('Supprimer tous les colis de la liste ?')) {
      etat.colis = []; etat.resultat = null; etat.selection = null; sauvegarderLocal(); afficherListe(); afficherOnglets();
      $('#message-vue').hidden = false; vue.vider(); vue.demanderRendu(); afficherMessages(); $('#grille-bilan').innerHTML = '<p class="vide">Lancez un calcul pour obtenir le bilan.</p>'; $('#boutons-export').hidden = true;
      afficherInfoColis(null); afficherInfoPalette(null); $('#legende').innerHTML = ''; afficherEtapes(); afficherJournal(); reinitialiserFormulaire();
      afficherQualite(); $('#plan-bilan').hidden = true; $('#bilan-perime').hidden = true; etat.perime = false;
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
    const garde = { moteur: etat.parametres.moteur, temps_limite: etat.parametres.temps_limite };
    etat.parametres = { ...etat.config.parametres, ...garde, ...(rep.parametres || {}) };
    etat.colis = rep.colis; sauvegarderLocal(); marquerPerime(); afficherListe(); reinitialiserFormulaire();
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
  $('#btn-reduire-qualite').addEventListener('click', () => {
    const p = $('#panneau-qualite');
    const reduit = p.classList.toggle('reduit');
    $('#btn-reduire-qualite').textContent = reduit ? 'Agrandir ▴' : 'Réduire ▾';
  });
  $('#btn-plan-csv').addEventListener('click', () => exporter('plan_csv', { contenu: planCsv() }));
  $('#btn-plan-json').addEventListener('click', () => exporter('plan_json', { resultat: etat.resultat }));
  $('#btn-fiche').addEventListener('click', ouvrirFiche);
  $('#btn-fiche-fermer').addEventListener('click', () => $('#dlg-fiche').close());
  $('#btn-fiche-imprimer').addEventListener('click', () => window.print());

  // Paramètres
  $('#btn-parametres').addEventListener('click', ouvrirParametres);
  $('#btn-params-annuler').addEventListener('click', () => $('#dlg-parametres').close());
  $('#btn-params-defaut').addEventListener('click', () => { etat.parametres = { ...etat.config.parametres }; ouvrirParametres(); });
  $('#btn-regles-projet').addEventListener('click', appliquerReglesProjet);
  $('#form-parametres').addEventListener('input', () => { majAidePassagers(); majAideLargeurUtile(); majPlanParametres(); });
  $('#select-moteur').addEventListener('change', majAideMoteur);
  $('#form-parametres').addEventListener('submit', (e) => {
    e.preventDefault();
    etat.parametres = lireParametres();
    sauvegarderLocal();
    marquerPerime();
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
