// MEASUREMENTS & CONTEXT (refs/INTEGRATION.md §5): the panel explains figures the pipeline
// measured; it never infers a finding from a rendering. This is computed locally and
// deterministically: no framebuffer and no study data leave the browser, and there is no model
// to make clinical-sounding claims. Every sentence is tied to a supplied number.
import type { AnatomyEntry } from './anatomyDatabase';

export const DISCLAIMER = 'Visualisation support, not diagnosis.';

export interface ContextLine {
  key: string;
  text: string;
}

function compare(value: number, [lo, hi]: [number, number], unit: string, what: string): string {
  const range = `${lo.toLocaleString()}-${hi.toLocaleString()} ${unit}`;
  if (value < lo) return `The measured ${what} is below the approximate adult range of ${range}.`;
  if (value > hi) return `The measured ${what} is above the approximate adult range of ${range}.`;
  return `The measured ${what} is within the approximate adult range of ${range}.`;
}

export function measurementContext(info: AnatomyEntry, faceCut: boolean): ContextLine[] {
  const m = info.measurements;
  if (!m || m.volume_ml === undefined || m.hu_mean === undefined || m.hu_std === undefined || !m.bbox_mm)
    return [{ key: 'none', text: 'This study has no measurements for this structure.' }];
  const lines: ContextLine[] = [];
  const ref = info.reference;
  if (ref?.typicalVolumeMl) lines.push({ key: 'vol', text: compare(m.volume_ml, ref.typicalVolumeMl, 'ml', 'volume') });
  if (ref?.typicalHu) lines.push({ key: 'hu', text: compare(m.hu_mean, ref.typicalHu, 'HU', 'mean attenuation') });
  if (m.hu_std > 150)
    lines.push({
      key: 'std',
      text: 'The HU spread is wide, so the region mixes tissues (air, fat, bone or contrast) or includes partial-volume edges.',
    });
  const [lo, hi] = m.bbox_mm;
  const ext = hi.map((v, i) => Math.abs(v - lo[i]));
  lines.push({
    key: 'ext',
    text: `Extent: ${ext.map((e) => e.toFixed(0)).join(' x ')} mm (right-left x posterior-anterior x inferior-superior).`,
  });
  if (faceCut)
    lines.push({
      key: 'face',
      text: 'Everything inside the face region was removed by the pipeline before measuring and meshing (S7), so head structures such as the skull are intentionally incomplete.',
    });
  lines.push({
    key: 'src',
    text: 'Measured on a 3 mm TotalSegmentator mask (fast mode); small or thin structures are coarse at this resolution.',
  });
  return lines;
}
