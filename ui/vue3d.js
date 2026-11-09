/* ===================== Vue 3D des palettes (Three.js) =====================
 * Repère du notebook : X = 274 cm, Y = 224 cm (BIPAL : 448 cm), Z = vertical. Les côtés (couloir de 14 cm de chaque côté,
 * colis IATA posés contre l'un des deux bords) sont les extrémités de l'axe `axe_cotes` : Y comme le notebook, ou X.
 * L'origine est le coin de la zone utile ; le plateau physique déborde du couloir. Unités : mètres dans la scène.
 */
import * as THREE from './vendor/three.module.min.js';
import { OrbitControls } from './vendor/OrbitControls.js';

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

const CM = 0.01;
const COULEUR_ORDINAIRE = '#b8c2cc';
const DEGRADE_POIDS = ['#ffffcc', '#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a', '#e31a1c', '#b10026'];

export function couleurPoids(t) {
  t = Math.max(0, Math.min(1, isFinite(t) ? t : 0));
  const n = DEGRADE_POIDS.length - 1;
  const i = Math.min(n - 1, Math.floor(t * n));
  const a = new THREE.Color(DEGRADE_POIDS[i]);
  const b = new THREE.Color(DEGRADE_POIDS[i + 1]);
  return '#' + a.lerp(b, t * n - i).getHexString();
}

function spriteTexte(texte, { taille = 48, fond = 'rgba(255,255,255,0.88)', couleur = '#111827', gras = true, devant = false } = {}) {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const police = `${gras ? 'bold ' : ''}${taille}px "Segoe UI", Arial, sans-serif`;
  ctx.font = police;
  const largeur = Math.ceil(ctx.measureText(texte).width) + taille * 0.8;
  const hauteur = Math.ceil(taille * 1.5);
  canvas.width = largeur; canvas.height = hauteur;
  ctx.font = police;
  ctx.fillStyle = fond;
  const r = taille * 0.35;
  ctx.beginPath();
  ctx.roundRect(1, 1, largeur - 2, hauteur - 2, r);
  ctx.fill();
  ctx.strokeStyle = 'rgba(0,0,0,0.25)'; ctx.lineWidth = 2; ctx.stroke();
  ctx.fillStyle = couleur; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(texte, largeur / 2, hauteur / 2);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const materiau = new THREE.SpriteMaterial({ map: texture, depthTest: !devant, transparent: true });
  const sprite = new THREE.Sprite(materiau);
  sprite.userData.ratio = largeur / hauteur;
  return sprite;
}

export class Vue3D {
  constructor(conteneur, { surSelection = () => {}, surSurvol = () => {} } = {}) {
    this.conteneur = conteneur;
    this.surSelection = surSelection;
    this.surSurvol = surSurvol;
    this.mode = 'categories';
    this.opacite = 1;
    this.etiquettes = true;
    this.options = { classes: {}, couleursCarte: [], heat: { min: 0, max: 0 }, largeurPhysique: 274 };
    this.boites = new Map();      // clé "palette|numero" -> {mesh, edges, sprite, placement, palette}
    this.selection = null;
    this.palettes = [];

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 200);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(4, -5, 3.5);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    conteneur.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.02;
    this.controls.addEventListener('change', () => this.demanderRendu());

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x9aa5b1, 0.95));
    const soleil = new THREE.DirectionalLight(0xffffff, 1.1);
    soleil.position.set(3, -4, 8);
    this.scene.add(soleil);
    const contre = new THREE.DirectionalLight(0xffffff, 0.35);
    contre.position.set(-4, 5, 3);
    this.scene.add(contre);

    this.contenu = new THREE.Group();
    this.scene.add(this.contenu);
    this.halo = null;

    this.raycaster = new THREE.Raycaster();
    this.pointeur = new THREE.Vector2();
    this.depart = null;
    const el = this.renderer.domElement;
    el.addEventListener('pointerdown', (e) => { this.depart = { x: e.clientX, y: e.clientY }; });
    el.addEventListener('pointerup', (e) => this.surClic(e));
    el.addEventListener('pointermove', (e) => this.surMouvement(e));
    el.addEventListener('pointerleave', () => { this.surSurvol(null); el.style.cursor = 'default'; });

    this.observateur = new ResizeObserver(() => this.redimensionner());
    this.observateur.observe(conteneur);
    this.redimensionner();

    this.renduDemande = false;
    this.animer = this.animer.bind(this);
    requestAnimationFrame(this.animer);
  }

  // ------------------------------------------------------------------ rendu
  redimensionner() {
    const w = this.conteneur.clientWidth || 1, h = this.conteneur.clientHeight || 1;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    this.demanderRendu();
  }
  demanderRendu() { this.renduDemande = true; }
  animer() {
    requestAnimationFrame(this.animer);
    const bouge = this.controls.update();
    if (this.renduDemande || bouge) {
      this.renduDemande = false;
      this.renderer.render(this.scene, this.camera);
    }
  }

  // ------------------------------------------------------------------ construction de la scène
  vider() {
    this.contenu.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) {
        if (o.material.map) o.material.map.dispose();
        o.material.dispose();
      }
    });
    this.contenu.clear();
    this.boites.clear();
    this.halo = null;
    this.selection = null;
  }

  /** Affiche une ou plusieurs palettes (plusieurs = vue d'ensemble alignée selon Y). */
  afficherPalettes(palettes, options = {}) {
    Object.assign(this.options, options);
    this.vider();
    this.palettes = palettes;
    let decalageY = 0;
    const ecart = 0.6;
    palettes.forEach((pal, index) => {
      const groupe = new THREE.Group();
      const physY = (pal.longueur_physique || pal.longueur) * CM;
      groupe.position.set(0, decalageY + ((physY - pal.longueur * CM) / 2), 0);
      this.construirePalette(groupe, pal, palettes.length > 1);
      this.contenu.add(groupe);
      decalageY += physY + ecart;
    });
    this.appliquerCouleurs();
    this.recadrer();
  }

  construirePalette(groupe, pal, ensemble) {
    const L = pal.largeur * CM, W = pal.longueur * CM, H = pal.hauteur_max * CM;
    const Lphys = Math.max(L, (pal.largeur_physique || this.options.largeurPhysique || pal.largeur) * CM);
    const Wphys = Math.max(W, (pal.longueur_physique || pal.longueur) * CM);
    const margeX = (Lphys - L) / 2, margeY = (Wphys - W) / 2;
    const epaisseur = 0.08;

    // Plateau physique (aluminium) puis zone utile un peu plus claire.
    const plateau = new THREE.Mesh(new THREE.BoxGeometry(Lphys, Wphys, epaisseur),
      new THREE.MeshLambertMaterial({ color: 0x8b95a1 }));
    plateau.position.set(L / 2, W / 2, -epaisseur / 2);
    plateau.userData.plateau = true;
    groupe.add(plateau);
    const utile = new THREE.Mesh(new THREE.PlaneGeometry(L, W), new THREE.MeshLambertMaterial({ color: 0xc9d1da }));
    utile.position.set(L / 2, W / 2, 0.001);
    groupe.add(utile);
    // Bandes réservées au couloir, de chaque côté de la zone utile (couleur sombre), sur l'axe des côtés.
    const matBande = () => new THREE.MeshLambertMaterial({ color: 0x6b7480 });
    if (margeX > 0.005) {
      for (const x of [-margeX / 2, L + margeX / 2]) {
        const bande = new THREE.Mesh(new THREE.PlaneGeometry(margeX, Wphys), matBande());
        bande.position.set(x, W / 2, 0.0015);
        groupe.add(bande);
      }
    }
    if (margeY > 0.005) {
      for (const y of [-margeY / 2, W + margeY / 2]) {
        const bande = new THREE.Mesh(new THREE.PlaneGeometry(Lphys, margeY), matBande());
        bande.position.set(L / 2, y, 0.0015);
        groupe.add(bande);
      }
    }
    // Séparation des palettes accouplées (BIPAL / TRIPAL) : tous les 224 cm du plateau physique
    if (pal.positions > 1) {
      const unite = Wphys / pal.positions;
      for (let k = 1; k < pal.positions; k++) {
        const y = k * unite - margeY;
        const ligne = new THREE.Line(new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(-margeX, y, 0.003), new THREE.Vector3(L + margeX, y, 0.003)]),
          new THREE.LineBasicMaterial({ color: 0x1f2937 }));
        groupe.add(ligne);
      }
    }
    // Enveloppe de hauteur maximale en pointillés
    const env = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(L, W, H)),
      new THREE.LineDashedMaterial({ color: 0x64748b, dashSize: 0.08, gapSize: 0.05 }));
    env.position.set(L / 2, W / 2, H / 2);
    env.computeLineDistances();
    groupe.add(env);
    // Couloirs : un de chaque côté (seulement s'il y a un espace réservé : pas de couloir avec la dérogation).
    // Les colis IATA sont posés contre ces bords.
    const lateral = (pal.axe_cotes || 'y') === 'x';
    const positions = (lateral ? margeX : margeY) <= 0.005 ? []
      : lateral ? [[-margeX - 0.22, W / 2], [L + margeX + 0.22, W / 2]]
        : [[L / 2, -margeY - 0.22], [L / 2, W + margeY + 0.22]];
    for (const [x, y] of positions) {
      const s = spriteTexte('COULOIR', { taille: 40, fond: 'rgba(20,33,61,0.85)', couleur: '#ffffff', devant: true });
      s.position.set(x, y, 0.05);
      s.scale.set(0.18 * s.userData.ratio, 0.18, 1);
      groupe.add(s);
    }
    // Étiquette de la palette
    const zone = pal.zone === 'rampe' ? ' · RAMPE' : '';
    const titre = spriteTexte(`Palette ${pal.numero} · ${pal.type}${zone} · ${Math.round(pal.masse)} kg · ${Math.round(pal.hauteur)} cm`,
      { taille: 40, fond: 'rgba(20,33,61,0.9)', couleur: '#ffffff' });
    titre.position.set(L / 2, W / 2, H + 0.25);
    titre.scale.set(0.28 * titre.userData.ratio, 0.28, 1);
    titre.userData.titre = true;
    groupe.add(titre);

    // Colis
    for (const p of pal.placements) {
      const dx = p.dx * CM, dy = p.dy * CM, dz = p.dz * CM;
      const geo = new THREE.BoxGeometry(dx, dy, dz);
      const mat = new THREE.MeshLambertMaterial({ color: 0xffffff, transparent: true, opacity: this.opacite });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(p.x * CM + dx / 2, p.y * CM + dy / 2, p.z * CM + dz / 2);
      const cle = `${pal.numero}|${p.colis.numero}`;
      mesh.userData = { cle, numero: p.colis.numero, palette: pal.numero };
      groupe.add(mesh);
      // Contour rouge pour les colis dangereux (IATA), comme dans la visualisation de CDP_2
      const dangereux = !!((this.options.classes || {})[p.colis.classe] || {}).iata;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: dangereux ? 0xdc2626 : 0x1f2937 }));
      edges.position.copy(mesh.position);
      groupe.add(edges);
      const sprite = spriteTexte(String(p.colis.numero), { taille: 44 });
      const taille = Math.min(0.26, Math.max(0.12, Math.min(dx, dy) * 0.45));
      sprite.scale.set(taille * sprite.userData.ratio, taille, 1);
      sprite.position.set(mesh.position.x, mesh.position.y, p.z * CM + dz + 0.02);
      sprite.visible = this.etiquettes;
      groupe.add(sprite);
      this.boites.set(cle, { mesh, edges, sprite, placement: p, palette: pal, groupe });
    }
  }

  // ------------------------------------------------------------------ couleurs
  couleurDe(placement, pal) {
    const c = placement.colis;
    const classes = this.options.classes || {};
    const carte = this.options.couleursCarte || [];
    switch (this.mode) {
      case 'poids': {
        const { min, max } = this.options.heat || { min: 0, max: 0 };
        const t = max > min ? (c.masse - min) / (max - min) : 0.5;
        return couleurPoids(t);
      }
      case 'carte_tous': {
        const i = (pal.carte_tous || {})[c.numero];
        return i === undefined ? COULEUR_ORDINAIRE : carte[i % carte.length];
      }
      case 'carte_speciaux': {
        if (!c.classe) return COULEUR_ORDINAIRE;
        const i = (pal.carte_speciaux || {})[c.numero];
        return i === undefined ? COULEUR_ORDINAIRE : carte[i % carte.length];
      }
      default:
        return (classes[c.classe] && classes[c.classe].couleur) || (classes[''] && classes[''].couleur) || COULEUR_ORDINAIRE;
    }
  }
  appliquerCouleurs() {
    for (const b of this.boites.values()) {
      b.mesh.material.color.set(this.couleurDe(b.placement, b.palette));
      b.mesh.material.opacity = this.opacite;
      b.mesh.material.transparent = this.opacite < 1;
      b.mesh.material.depthWrite = this.opacite >= 0.99;
      b.mesh.material.needsUpdate = true;
    }
    this.demanderRendu();
  }
  setMode(mode) { this.mode = mode; this.appliquerCouleurs(); }
  setOpacite(v) { this.opacite = v; this.appliquerCouleurs(); }
  setEtiquettes(visible) {
    this.etiquettes = visible;
    for (const b of this.boites.values()) b.sprite.visible = visible;
    this.demanderRendu();
  }

  // ------------------------------------------------------------------ sélection
  selectionner(numero, palette = null, notifier = false) {
    if (this.halo) { this.contenu.remove(this.halo); this.halo.geometry.dispose(); this.halo.material.dispose(); this.halo = null; }
    this.selection = null;
    if (numero === null || numero === undefined) { this.demanderRendu(); return; }
    let boite = null;
    for (const b of this.boites.values()) {
      if (b.placement.colis.numero === numero && (palette === null || b.palette.numero === palette)) { boite = b; break; }
    }
    if (!boite) { this.demanderRendu(); return; }
    this.selection = boite;
    const p = boite.placement;
    const geo = new THREE.BoxGeometry(p.dx * CM + 0.03, p.dy * CM + 0.03, p.dz * CM + 0.03);
    this.halo = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: 0xfacc15 }));
    boite.mesh.getWorldPosition(this.halo.position);
    this.contenu.add(this.halo);
    this.demanderRendu();
    if (notifier) this.surSelection(boite.placement, boite.palette);
  }
  intersection(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointeur.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointeur, this.camera);
    const meshes = [];
    for (const b of this.boites.values()) meshes.push(b.mesh);
    const hits = this.raycaster.intersectObjects(meshes, false);
    return hits.length ? this.boites.get(hits[0].object.userData.cle) : null;
  }
  surClic(e) {
    if (!this.depart) return;
    const d = Math.hypot(e.clientX - this.depart.x, e.clientY - this.depart.y);
    this.depart = null;
    if (d > 6) return; // c'était une rotation / un déplacement
    const boite = this.intersection(e);
    if (boite) this.selectionner(boite.placement.colis.numero, boite.palette.numero, true);
    else { this.selectionner(null); this.surSelection(null, null); }
  }
  surMouvement(e) {
    if (this.depart) return;
    const boite = this.intersection(e);
    this.renderer.domElement.style.cursor = boite ? 'pointer' : 'default';
    this.surSurvol(boite ? { placement: boite.placement, palette: boite.palette, x: e.clientX, y: e.clientY } : null);
  }

  // ------------------------------------------------------------------ caméra
  recadrer(dessus = false) {
    const boite = new THREE.Box3().setFromObject(this.contenu);
    if (boite.isEmpty()) { boite.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(2.7, 2.2, 2)); }
    const centre = boite.getCenter(new THREE.Vector3());
    const taille = boite.getSize(new THREE.Vector3());
    const rayon = Math.max(taille.x, taille.y, taille.z * 1.3) / 2;
    const dist = rayon / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 1.15;
    const direction = dessus ? new THREE.Vector3(0.001, -0.001, 1) : new THREE.Vector3(0.7, -1.0, 0.62);
    direction.normalize().multiplyScalar(dist);
    this.camera.position.copy(centre).add(direction);
    this.controls.target.copy(centre);
    this.controls.update();
    this.demanderRendu();
  }
  capturerImage() {
    this.renderer.render(this.scene, this.camera);
    return this.renderer.domElement.toDataURL('image/png');
  }
}
