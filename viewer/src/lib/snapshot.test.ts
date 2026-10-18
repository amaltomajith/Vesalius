import { describe, expect, it } from 'vitest';
import { decideSnapshot } from './snapshot';

describe('the AI snapshot gesture', () => {
  it('never reads a patient reconstruction (hard rule 8)', () => {
    for (const selected of [null, 'liver']) {
      const d = decideSnapshot(false, selected);
      expect(d.capture).toBe(false);
      expect(d.message).toMatch(/atlas only/);
    }
  });

  it('on the atlas, needs a selected part first', () => {
    const d = decideSnapshot(true, null);
    expect(d.capture).toBe(false);
    expect(d.message).toMatch(/Select a part first/);
  });

  it('with a part selected it captures, and says whether the AI is on', () => {
    const d = decideSnapshot(true, 'Heart');
    expect(d.capture).toBe(true);
    if (!d.capture) return;
    expect(d.message(true)).toMatch(/AI will describe/);
    expect(d.message(false)).toMatch(/AI is off \(no GROQ_API_KEY/);
  });
});
