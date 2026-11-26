import { describe, expect, it } from 'vitest';
import { splitDescription } from './description';

/** a paragraph long enough to stand alone as the lead */
const long = (s: string) => s + 'x'.repeat(90);

describe('splitting an atlas description for the part card', () => {
  it('shows the first paragraph and keeps the rest for "Read more"', () => {
    const d = splitDescription(`${long('First.')}\n\nSecond paragraph.\n\nThird paragraph.`);
    expect(d.lead).toBe(long('First.'));
    expect(d.rest).toEqual(['Second paragraph.', 'Third paragraph.']);
  });

  it('a lead too short to stand alone takes the next paragraph with it', () => {
    const d = splitDescription('The body of the sternum.\n\nIt is flat.\n\nIt has three ridges.');
    expect(d.lead).toBe('The body of the sternum. It is flat.');
    expect(d.rest).toEqual(['It has three ridges.']);
  });

  it('one paragraph has nothing more to read; empty text is empty', () => {
    expect(splitDescription('Only this.')).toEqual({ lead: 'Only this.', rest: [] });
    expect(splitDescription('  \n\n ')).toEqual({ lead: '', rest: [] });
  });

  it('tolerates blank lines with spaces between paragraphs', () => {
    expect(splitDescription(`${long('A')}\n  \nB`).rest).toEqual(['B']);
  });
});
