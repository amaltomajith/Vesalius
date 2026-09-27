// Anatomy data, manifest first (refs/INTEGRATION.md §4.3):
//   1. the open study's manifest supplies the node name, display label, group and measurements
//   2. this static table optionally enriches, keyed on the segmentation label vocabulary
//      (TotalSegmentator `total`, label_set_version totalseg_total_v2)
//   3. a prettified fallback for anything unmatched
// Reference ranges are approximate adult figures from general anatomy/radiology teaching, given
// only so a measured number has context. They are not diagnostic thresholds.
import { useStudyStore, type StructureInfo } from '../store/studyStore';

export interface Reference {
  function: string;
  location: string;
  clinicalNote: string;
  /** approximate adult volume, ml */
  typicalVolumeMl?: [number, number];
  /** approximate unenhanced CT attenuation, HU */
  typicalHu?: [number, number];
}

export interface AnatomyEntry {
  node: string;
  displayName: string;
  system: string;
  function: string;
  location: string;
  clinicalNote: string;
  reference: Reference | null;
  measurements: StructureInfo | null;
  /** atlas: Z-Anatomy description (Wikipedia-derived, CC-BY-SA), when one matches */
  description: string | null;
  /** atlas: anatomical grouping, outermost first */
  path: string[];
  atlas: boolean;
}

const BONE: Omit<Reference, 'function' | 'location'> = {
  clinicalNote: 'Cortical bone measures several hundred to over a thousand HU; marrow lowers the mean.',
  typicalHu: [300, 1900],
};

// Keyed on exact labels; `prefix:` keys match lateralised / numbered families (rib_left_3, ...).
const DB: Record<string, Reference> = {
  liver: {
    function: 'Metabolism, detoxification, protein synthesis and bile production.',
    location: 'Right upper quadrant, beneath the right hemidiaphragm.',
    clinicalNote: 'Largest solid organ; volume varies widely with body size.',
    typicalVolumeMl: [1200, 1900],
    typicalHu: [54, 66],
  },
  spleen: {
    function: 'Filters blood, recycles red cells and hosts lymphoid tissue.',
    location: 'Left upper quadrant, posterior to the stomach, under ribs 9-11.',
    clinicalNote: 'Volume is the usual way to express splenomegaly on CT.',
    typicalVolumeMl: [100, 300],
    typicalHu: [40, 60],
  },
  kidney_left: {
    function: 'Filters blood, produces urine, regulates fluid balance and blood pressure.',
    location: 'Retroperitoneum, left of the spine at about T12-L3.',
    clinicalNote: 'Usually sits slightly higher than the right kidney.',
    typicalVolumeMl: [120, 220],
    typicalHu: [30, 50],
  },
  kidney_right: {
    function: 'Filters blood, produces urine, regulates fluid balance and blood pressure.',
    location: 'Retroperitoneum, right of the spine, displaced downward by the liver.',
    clinicalNote: 'Usually sits slightly lower than the left kidney.',
    typicalVolumeMl: [120, 220],
    typicalHu: [30, 50],
  },
  stomach: {
    function: 'Stores food and begins digestion with acid and enzymes.',
    location: 'Left upper quadrant, below the diaphragm.',
    clinicalNote: 'Measured volume depends mostly on how full the stomach was at scan time.',
  },
  pancreas: {
    function: 'Exocrine digestive enzymes and endocrine hormones (insulin, glucagon).',
    location: 'Retroperitoneum, lying across the spine behind the stomach.',
    clinicalNote: 'Gland volume tends to decrease with age.',
    typicalVolumeMl: [50, 100],
    typicalHu: [30, 50],
  },
  gallbladder: {
    function: 'Stores and concentrates bile.',
    location: 'Under the liver, in the gallbladder fossa.',
    clinicalNote: 'Volume depends on fasting state; bile is near water density.',
    typicalHu: [0, 20],
  },
  duodenum: {
    function: 'First part of the small intestine; receives bile and pancreatic juice.',
    location: 'C-shaped loop around the head of the pancreas.',
    clinicalNote: 'Content and wall are segmented together.',
  },
  small_bowel: {
    function: 'Digestion and nutrient absorption.',
    location: 'Central and lower abdomen.',
    clinicalNote: 'Automated segmentation of bowel loops is less reliable than for solid organs.',
  },
  colon: {
    function: 'Water and electrolyte absorption; stores faeces.',
    location: 'Frames the abdomen from the right iliac fossa to the rectum.',
    clinicalNote: 'Gas and content inside the lumen affect the mean HU.',
  },
  urinary_bladder: {
    function: 'Stores urine until voiding.',
    location: 'Pelvis, behind the pubic symphysis.',
    clinicalNote: 'Volume reflects how full the bladder was at scan time.',
    typicalHu: [0, 20],
  },
  esophagus: {
    function: 'Carries food from the pharynx to the stomach.',
    location: 'Posterior mediastinum, behind the trachea and left atrium.',
    clinicalNote: 'Often contains a little air, which lowers the mean HU.',
  },
  trachea: {
    function: 'Airway from the larynx to the carina.',
    location: 'Midline neck and superior mediastinum.',
    clinicalNote: 'The segmented volume is mostly the air column, hence strongly negative HU.',
    typicalHu: [-1000, -900],
  },
  lung_: {
    function: 'Gas exchange.',
    location: 'Thoracic cavity, either side of the mediastinum; lobes separated by fissures.',
    clinicalNote: 'Aerated lung measures roughly -900 to -700 HU; volume depends on inspiration.',
    typicalHu: [-900, -700],
  },
  heart: {
    function: 'Pumps blood through the pulmonary and systemic circulations.',
    location: 'Middle mediastinum, two-thirds left of the midline.',
    clinicalNote: 'Without contrast, blood and myocardium have similar attenuation.',
    typicalHu: [30, 60],
  },
  aorta: {
    function: 'Main systemic artery carrying blood from the left ventricle.',
    location: 'Ascends from the heart, arches, then descends along the spine.',
    clinicalNote: 'Mean HU rises sharply if intravenous contrast was given.',
  },
  inferior_vena_cava: {
    function: 'Returns blood from the lower body to the right atrium.',
    location: 'Right of the spine in the retroperitoneum.',
    clinicalNote: 'Calibre varies with breathing and fluid status.',
  },
  superior_vena_cava: {
    function: 'Returns blood from the head, neck and arms to the right atrium.',
    location: 'Right superior mediastinum.',
    clinicalNote: 'Contrast injected in an arm vein can make it very dense.',
  },
  portal_vein_and_splenic_vein: {
    function: 'Carries blood from the gut and spleen to the liver.',
    location: 'Behind the pancreas, into the liver hilum.',
    clinicalNote: 'Best seen in the portal venous contrast phase.',
  },
  pulmonary_vein: {
    function: 'Returns oxygenated blood from the lungs to the left atrium.',
    location: 'Behind the heart, entering the left atrium.',
    clinicalNote: 'Usually four veins, with common anatomical variants.',
  },
  brain: {
    function: 'Central nervous system: cognition, sensation and motor control.',
    location: 'Cranial cavity.',
    clinicalNote: 'Adult brain volume is roughly 1,100-1,500 ml; grey and white matter differ by a few HU.',
    typicalVolumeMl: [1100, 1500],
    typicalHu: [20, 45],
  },
  spinal_cord: {
    function: 'Carries signals between the brain and the body.',
    location: 'Vertebral canal, from the foramen magnum to about L1-L2.',
    clinicalNote: 'Hard to separate from CSF on CT; the segmentation is approximate.',
  },
  skull: {
    ...BONE,
    function: 'Protects the brain and supports the face.',
    location: 'Head.',
  },
  sternum: { ...BONE, function: 'Anchors the ribs and clavicles at the front of the chest.', location: 'Anterior chest wall, midline.' },
  sacrum: { ...BONE, function: 'Transfers load from the spine to the pelvis.', location: 'Base of the spine, between the hip bones.' },
  costal_cartilages: {
    function: 'Connect the ribs to the sternum and allow the chest wall to move.',
    location: 'Anterior chest wall.',
    clinicalNote: 'Often partly calcified with age, which raises the mean HU.',
  },
  vertebrae_: { ...BONE, function: 'Support the trunk and protect the spinal cord.', location: 'Vertebral column; the suffix names the level (C, T, L, S).' },
  rib_: { ...BONE, function: 'Protect the thoracic organs and support breathing.', location: 'Thoracic cage; the suffix names side and number.' },
  humerus_: { ...BONE, function: 'Upper arm bone.', location: 'Arm; often only the proximal part is in the field of view.' },
  femur_: { ...BONE, function: 'Thigh bone; the longest bone in the body.', location: 'Thigh; often only the proximal part is in the field of view.' },
  hip_: { ...BONE, function: 'Pelvic bone (ilium, ischium, pubis).', location: 'Pelvis.' },
  scapula_: { ...BONE, function: 'Shoulder blade; anchors the shoulder muscles.', location: 'Posterior upper chest wall.' },
  clavicula_: { ...BONE, function: 'Collarbone; strut between the sternum and the shoulder.', location: 'Base of the neck.' },
  iliac_artery_: {
    function: 'Supplies the pelvis and legs.',
    location: 'From the aortic bifurcation (about L4) into the pelvis.',
    clinicalNote: 'Wall calcification is common with age.',
  },
  iliac_vena_: {
    function: 'Drains the pelvis and legs into the inferior vena cava.',
    location: 'Pelvis, alongside the iliac arteries.',
    clinicalNote: '',
  },
  adrenal_gland_: {
    function: 'Hormones: cortisol, aldosterone, adrenaline.',
    location: 'On top of each kidney.',
    clinicalNote: 'Small and thin; segmentation at 3 mm resolution is coarse.',
  },
  thyroid_gland: {
    function: 'Thyroid hormones regulating metabolism.',
    location: 'Anterior neck, around the trachea.',
    clinicalNote: 'Iodine content makes it denser than muscle on unenhanced CT.',
    typicalHu: [80, 120],
  },
  autochthon_: {
    function: 'Deep back muscles that extend and stabilise the spine.',
    location: 'Either side of the spine.',
    clinicalNote: 'Muscle measures roughly 35-55 HU; fatty infiltration lowers it.',
    typicalHu: [35, 55],
  },
  iliopsoas_: {
    function: 'Flexes the hip.',
    location: 'From the lumbar spine through the pelvis to the femur.',
    clinicalNote: 'Muscle measures roughly 35-55 HU.',
    typicalHu: [35, 55],
  },
  gluteus_: {
    function: 'Extends, abducts and rotates the hip.',
    location: 'Buttock.',
    clinicalNote: 'Muscle measures roughly 35-55 HU.',
    typicalHu: [35, 55],
  },
  atrial_appendage_: {
    function: 'Small pouch of the atrium.',
    location: 'Upper border of the heart.',
    clinicalNote: '',
  },
  brachiocephalic_: {
    function: 'Great vessel of the upper chest.',
    location: 'Superior mediastinum.',
    clinicalNote: '',
  },
  subclavian_artery_: {
    function: 'Supplies the arm.',
    location: 'Beneath the clavicle.',
    clinicalNote: '',
  },
  common_carotid_artery_: {
    function: 'Supplies the head and neck.',
    location: 'Either side of the trachea in the neck.',
    clinicalNote: '',
  },
};

export function referenceFor(node: string): Reference | null {
  if (DB[node]) return DB[node];
  // longest matching family prefix: `rib_` for rib_left_3, `lung_` for lung_upper_lobe_left
  let best: string | null = null;
  for (const k of Object.keys(DB))
    if (k.endsWith('_') && node.startsWith(k) && (!best || k.length > best.length)) best = k;
  return best ? DB[best] : null;
}

export function prettify(node: string): string {
  const cleaned = node.replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
  return cleaned ? cleaned.charAt(0).toUpperCase() + cleaned.slice(1) : 'Anatomical structure';
}

export function getAnatomyInfo(meshName: string): AnatomyEntry {
  const state = useStudyStore.getState();
  const measurements = state.structures[meshName] ?? null;
  const atlas = state.current?.manifest.kind === 'atlas';
  if (atlas) {
    const key = measurements?.describe ?? '';
    const description = state.descriptions?.[key] ?? null;
    return {
      node: meshName,
      displayName: measurements?.label ?? prettify(meshName),
      system: measurements?.groupLabel ?? 'Atlas',
      function: description ? description.split('\n\n')[0] : 'No description for this structure.',
      location: measurements?.path?.join(' / ') ?? '',
      clinicalNote: '',
      reference: null,
      measurements,
      description,
      path: measurements?.path ?? [],
      atlas: true,
    };
  }
  const reference = referenceFor(meshName);
  return {
    node: meshName,
    displayName: measurements?.label ?? prettify(meshName),
    system: measurements?.groupLabel ?? 'Unmapped',
    function: reference?.function ?? 'No reference entry for this label.',
    location: reference?.location ?? 'See its position in the scene.',
    clinicalNote: reference?.clinicalNote ?? '',
    reference,
    measurements,
    description: null,
    path: [],
    atlas: false,
  };
}
