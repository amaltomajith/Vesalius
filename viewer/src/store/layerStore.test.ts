import { describe, expect, it } from 'vitest';
import { GROUP_COLOURS } from './layerStore';

// Every group id the pipeline emits (s8_segment/structures_params.py GROUP_MAP_V1 + "other", and
// "surface" for bone / skin results) and the atlas ships (assets/atlas/atlas.json).
const PIPELINE = ['skeletal', 'cardiovascular', 'visceral', 'respiratory', 'muscular', 'nervous', 'endocrine', 'other', 'surface'];
const ATLAS = ['skeletal', 'cardiovascular', 'visceral', 'muscular', 'nervous', 'lymphoid', 'joints', 'regions'];

describe('the anatomy palette', () => {
  it('has a colour for every group the pipeline and the atlas use', () => {
    for (const id of [...PIPELINE, ...ATLAS]) expect(GROUP_COLOURS[id], id).toBeDefined();
  });

  it('gives every system that can be on screen together its own colour', () => {
    for (const set of [PIPELINE, ATLAS]) {
      const colours = set.filter((id) => id !== 'surface').map((id) => GROUP_COLOURS[id].color.toLowerCase());
      expect(new Set(colours).size, set.join()).toBe(colours.length);
    }
  });

  it('glows in its own colour: no neon emissive twin', () => {
    for (const [id, c] of Object.entries(GROUP_COLOURS)) expect(c.emissive, id).toBe(c.color);
  });
});
