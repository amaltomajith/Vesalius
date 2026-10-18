// An atlas description, split for the part card: the first paragraph is shown, the rest behind
// "Read more" (the 3D room redesign: no more wall of text beside the anatomy).

export interface SplitDescription { lead: string; rest: string[] }

export function splitDescription(text: string): SplitDescription {
  const paras = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  if (paras.length === 0) return { lead: '', rest: [] };
  // a lead too short to say anything ("The sternum.") takes the next paragraph with it
  const leadCount = paras[0].length < 80 && paras.length > 1 ? 2 : 1;
  return { lead: paras.slice(0, leadCount).join(' '), rest: paras.slice(leadCount) };
}
