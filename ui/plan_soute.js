/* ===================== Plan de la soute de l'A400M =====================
 * Vue de dessus schématique : le nez à gauche, la rampe à droite ; 7 emplacements en soute (1 à 7) puis 2 sur la rampe
 * (8 et 9), de caractéristiques différentes. Chaque emplacement est libre, indisponible (déclaré à la main), pris par
 * les sièges des passagers ou, après un calcul, occupé par une palette.
 */

const NS = 'http://www.w3.org/2000/svg';

const ETATS = {
  libre: 'libre',
  indisponible: 'indisponible (cliquer pour le libérer)',
  passagers: 'pris par les sièges des passagers',
};

/** Nombre maximal d'emplacements avec ce nombre de passagers (table des versions de l'A400M). */
export function limitePassagers(versions, passagers, total) {
  for (const v of versions) if (passagers <= v.passagers_max) return Math.min(total, v.palettes);
  return 0;
}

/**
 * Emplacements et leur état, d'après les paramètres (même règle que le serveur) : `libre`, `indisponible` ou `passagers`.
 * `palettes` (facultatif) : palettes d'un calcul, pour indiquer l'occupation.
 */
export function emplacementsPlan(p, versions, palettes = null) {
  const nSoute = p.positions_soute, nRampe = p.positions_rampe, total = nSoute + nRampe;
  const marques = new Set((p.emplacements_indisponibles || []).map(Number));
  const numeros = Array.from({ length: total }, (_, k) => k + 1);
  const libres = numeros.filter((n) => !marques.has(n));
  const exces = libres.length - limitePassagers(versions, Number(p.passagers) || 0, total);
  const sieges = new Set(exces > 0 ? libres.slice(0, exces) : []);
  const occupe = {};
  for (const pal of palettes || []) for (const n of pal.emplacements || []) occupe[n] = pal;
  return numeros.map((n) => {
    const rampe = n > nSoute;
    return {
      numero: n, zone: rampe ? 'rampe' : 'soute',
      etat: marques.has(n) ? 'indisponible' : sieges.has(n) ? 'passagers' : 'libre',
      masse_max: Number(rampe ? p.masse_max_rampe : p.masse_max_soute) - (Number(p.tare_palette) || 0),
      palette: occupe[n] ? occupe[n].numero : null,
      masse: occupe[n] ? occupe[n].masse : null,
    };
  });
}

function el(nom, attrs = {}, texte = null) {
  const e = document.createElementNS(NS, nom);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (texte !== null) e.textContent = texte;
  return e;
}

/**
 * Dessine le plan dans `conteneur`.
 * `emplacements` : résultat de `emplacementsPlan` (ou `bilan.plan_soute` du serveur).
 * `surClic(emplacement)` : appelée au clic (ou à la touche Entrée / Espace) sur un emplacement.
 * `compact` : version réduite (numéros et palettes en gros, sans les charges) pour la colonne des résultats.
 */
export function dessinerPlanSoute(conteneur, emplacements, { surClic = null, titre = '', compact = false } = {}) {
  const nSoute = emplacements.filter((e) => e.zone === 'soute').length;
  const L = 78, H = 92, ESPACE = 8, X0 = 92;                 // un emplacement : 78 x 92 (le sens de l'avion est horizontal)
  const largeur = X0 + emplacements.length * (L + ESPACE) + 40;
  const svg = el('svg', { viewBox: `0 0 ${largeur} 188`, class: 'plan-soute' + (compact ? ' compact' : ''), role: 'group',
    'aria-label': titre || 'Plan de la soute et de la rampe' });
  // Fuselage et rampe
  const xRampe = X0 + nSoute * (L + ESPACE) - ESPACE / 2;
  const droite = largeur - 8;
  svg.appendChild(el('path', { class: 'fuselage',
    d: `M 8 94 Q 10 24 ${X0 - 8} 24 L ${xRampe} 24 L ${droite} 38 L ${droite} 150 L ${xRampe} 164 L ${X0 - 8} 164 Q 10 164 8 94 Z` }));
  svg.appendChild(el('path', { class: 'bande-rampe', d: `M ${xRampe} 24 L ${droite} 38 L ${droite} 150 L ${xRampe} 164 Z` }));
  svg.appendChild(el('text', { class: 'legende-plan', x: 44, y: 98, 'text-anchor': 'middle' }, 'AVANT'));
  svg.appendChild(el('text', { class: 'legende-plan', x: (xRampe + droite) / 2, y: 182, 'text-anchor': 'middle' }, 'RAMPE'));
  svg.appendChild(el('text', { class: 'legende-plan', x: X0 + (nSoute * (L + ESPACE)) / 2, y: 182, 'text-anchor': 'middle' }, 'SOUTE'));
  emplacements.forEach((e, k) => {
    const x = X0 + k * (L + ESPACE), y = 36;
    const g = el('g', { class: `emplacement ${e.palette ? 'occupe' : e.etat} ${e.zone}`, 'data-numero': e.numero,
      transform: `translate(${x} ${y})` });
    const libelle = e.palette
      ? `Emplacement ${e.numero} (${e.zone}) : palette ${e.palette}, ${Math.round(e.masse).toLocaleString('fr-FR')} kg sur ${Math.round(e.masse_max).toLocaleString('fr-FR')} kg`
      : `Emplacement ${e.numero} (${e.zone}, ${Math.round(e.masse_max).toLocaleString('fr-FR')} kg) : ${ETATS[e.etat] || e.etat}`;
    g.setAttribute('aria-label', libelle);
    g.appendChild(el('title', {}, libelle));
    g.appendChild(el('rect', { class: 'cadre', width: L, height: H, rx: 5 }));
    g.appendChild(el('text', { class: 'numero', x: 8, y: 17 }, String(e.numero)));
    if (e.palette) {
      g.appendChild(el('text', { class: 'palette', x: L / 2, y: 52, 'text-anchor': 'middle' }, `P${e.palette}`));
      g.appendChild(el('text', { class: 'masse', x: L / 2, y: 70, 'text-anchor': 'middle' }, `${Math.round(e.masse).toLocaleString('fr-FR')} kg`));
    } else if (e.etat === 'indisponible') {
      g.appendChild(el('path', { class: 'croix', d: `M 22 34 L ${L - 22} ${H - 26} M ${L - 22} 34 L 22 ${H - 26}` }));
    } else if (e.etat === 'passagers') {
      g.appendChild(el('text', { class: 'sieges', x: L / 2, y: 52, 'text-anchor': 'middle' }, 'sièges'));
      g.appendChild(el('text', { class: 'sieges', x: L / 2, y: 66, 'text-anchor': 'middle' }, 'passagers'));
    }
    g.appendChild(el('text', { class: 'charge', x: L / 2, y: H - 8, 'text-anchor': 'middle' },
      `${Math.round(e.masse_max).toLocaleString('fr-FR')} kg max`));
    if (surClic) {
      g.setAttribute('tabindex', '0');
      g.setAttribute('role', 'button');
      g.classList.add('cliquable');
      g.addEventListener('click', () => surClic(e));
      g.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); surClic(e); }
      });
    }
    svg.appendChild(g);
  });
  conteneur.replaceChildren(svg);
}
