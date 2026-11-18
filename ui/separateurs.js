/* ===================== Séparateurs redimensionnables =====================
 * Deux sortes de séparateurs, repérés dans la page par la classe `separateur` :
 *  - `data-colonne="g"` ou `"d"` : sépare les colonnes de la disposition ; on fait varier la largeur de la colonne de
 *    gauche ou de droite (variables CSS --col-g et --col-d de `#disposition`), la colonne du centre prend le reste ;
 *  - `data-cible="id"` : sépare deux blocs empilés ; on fait varier la hauteur du bloc `id`, situé avant le séparateur
 *    (ou après lui avec `data-sens="apres"`).
 * Souris, doigt et clavier (flèches) fonctionnent ; un double-clic (ou Début) rétablit la taille par défaut. Les
 * tailles choisies sont mémorisées dans le navigateur.
 */

const CLE = 'palettiseur.disposition.v1';
const MIN_COLONNE = { g: 300, d: 280 };
const MIN_CENTRE = 380;
const MIN_BLOC = 70;
const MIN_RESTE_BLOC = 150;      // hauteur laissée au bloc qui suit un séparateur (colonnes de gauche et de droite)
const MIN_VUE_3D = 320;          // hauteur laissée à la vue 3D quand on agrandit le panneau du bas
const PAS_CLAVIER = 24;

function lireMemoire() {
  try { return JSON.parse(localStorage.getItem(CLE)) || {}; } catch (e) { return {}; }
}
function ecrireMemoire(memo) {
  try { localStorage.setItem(CLE, JSON.stringify(memo)); } catch (e) { /* stockage indisponible */ }
}

export function initialiserSeparateurs() {
  const disposition = document.querySelector('#disposition');
  if (!disposition) return;
  const memo = lireMemoire();
  memo.colonnes = memo.colonnes || {};
  memo.hauteurs = memo.hauteurs || {};

  // ------------------------------------------------------------------ colonnes
  const variable = { g: '--col-g', d: '--col-d' };
  const largeurColonne = (cle) => parseFloat(getComputedStyle(disposition).getPropertyValue(variable[cle])) ||
    document.querySelector(cle === 'g' ? '.panneau.gauche' : '.panneau.droite').getBoundingClientRect().width;

  function borner(cle, valeur) {
    const autre = cle === 'g' ? 'd' : 'g';
    const poignees = 2 * 10 + 24;                                   // deux séparateurs et les marges de la disposition
    const maxi = disposition.clientWidth - poignees - MIN_CENTRE - largeurColonne(autre);
    return Math.round(Math.max(MIN_COLONNE[cle], Math.min(maxi, valeur)));
  }
  function fixerColonne(cle, valeur, memoriser = true) {
    const v = borner(cle, valeur);
    disposition.style.setProperty(variable[cle], `${v}px`);
    if (memoriser) memo.colonnes[cle] = v;
    return v;
  }
  function retablirColonne(cle) {
    disposition.style.removeProperty(variable[cle]);
    delete memo.colonnes[cle];
  }
  function appliquerMemoire() {
    for (const cle of ['g', 'd']) if (memo.colonnes[cle]) fixerColonne(cle, memo.colonnes[cle], false);
    for (const [id, h] of Object.entries(memo.hauteurs)) {
      const bloc = document.getElementById(id);
      if (bloc) fixerHauteur(bloc, h, false);
    }
  }

  // ------------------------------------------------------------------ hauteurs
  function maxiHauteur(bloc, apres) {
    const parent = bloc.parentElement;
    if (!parent) return 2000;
    if (apres) return Math.max(MIN_BLOC, parent.clientHeight - MIN_VUE_3D);                // panneau du bas du centre
    if (parent.classList.contains('panneau') && parent.classList.contains('gauche')) {
      return Math.max(MIN_BLOC, parent.clientHeight - MIN_RESTE_BLOC - 10);
    }
    return 1600;                                                                           // colonne de droite : elle défile
  }
  function fixerHauteur(bloc, valeur, memoriser = true, apres = false) {
    const h = Math.round(Math.max(MIN_BLOC, Math.min(maxiHauteur(bloc, apres), valeur)));
    bloc.style.flex = `0 0 ${h}px`;
    bloc.style.height = `${h}px`;
    bloc.style.minHeight = '0';
    bloc.style.overflow = 'auto';
    if (memoriser) memo.hauteurs[bloc.id] = h;
    return h;
  }
  function retablirHauteur(bloc) {
    for (const p of ['flex', 'height', 'minHeight', 'overflow']) bloc.style[p] = '';
    delete memo.hauteurs[bloc.id];
  }

  // ------------------------------------------------------------------ interaction
  function surSeparateur(sep) {
    const colonne = sep.dataset.colonne;
    const bloc = sep.dataset.cible ? document.getElementById(sep.dataset.cible) : null;
    const apres = sep.dataset.sens === 'apres';
    if (!colonne && !bloc) return;
    const vertical = !!colonne;
    sep.setAttribute('role', 'separator');
    sep.setAttribute('aria-orientation', vertical ? 'vertical' : 'horizontal');
    sep.tabIndex = 0;
    if (!sep.title) sep.title = 'Glisser pour redimensionner (double-clic : taille d\'origine)';

    const taille = () => (vertical ? largeurColonne(colonne) : bloc.getBoundingClientRect().height);
    const appliquer = (valeur, memoriser = true) => (vertical
      ? fixerColonne(colonne, valeur, memoriser) : fixerHauteur(bloc, valeur, memoriser, apres));
    // Le sens du glissement : vers la droite agrandit la colonne de gauche et réduit celle de droite ; vers le bas
    // agrandit le bloc situé avant le séparateur et réduit celui d'après.
    const signe = (colonne === 'd' || apres) ? -1 : 1;

    sep.addEventListener('pointerdown', (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      e.preventDefault();
      const depart = vertical ? e.clientX : e.clientY;
      const tailleDepart = taille();
      sep.setPointerCapture(e.pointerId);
      sep.classList.add('actif');
      document.body.classList.add('redimensionnement');
      const deplacer = (ev) => appliquer(tailleDepart + signe * ((vertical ? ev.clientX : ev.clientY) - depart), false);
      const finir = () => {
        sep.removeEventListener('pointermove', deplacer);
        sep.removeEventListener('pointerup', finir);
        sep.removeEventListener('pointercancel', finir);
        sep.classList.remove('actif');
        document.body.classList.remove('redimensionnement');
        appliquer(taille(), true);
        ecrireMemoire(memo);
      };
      sep.addEventListener('pointermove', deplacer);
      sep.addEventListener('pointerup', finir);
      sep.addEventListener('pointercancel', finir);
    });
    sep.addEventListener('dblclick', () => {
      if (vertical) retablirColonne(colonne); else retablirHauteur(bloc);
      ecrireMemoire(memo);
    });
    sep.addEventListener('keydown', (e) => {
      const avant = vertical ? 'ArrowLeft' : 'ArrowUp';
      const arriere = vertical ? 'ArrowRight' : 'ArrowDown';
      if (e.key === 'Home') {
        e.preventDefault();
        if (vertical) retablirColonne(colonne); else retablirHauteur(bloc);
        ecrireMemoire(memo);
      } else if (e.key === avant || e.key === arriere) {
        e.preventDefault();
        const pas = PAS_CLAVIER * (e.shiftKey ? 4 : 1) * (e.key === avant ? -1 : 1);
        appliquer(taille() + signe * pas, true);
        ecrireMemoire(memo);
      }
    });
  }

  document.querySelectorAll('.separateur').forEach(surSeparateur);
  appliquerMemoire();
  window.addEventListener('resize', () => {
    for (const cle of ['g', 'd']) if (memo.colonnes[cle]) fixerColonne(cle, memo.colonnes[cle], false);
  });
}
