// What the AI snapshot gesture does, and the one line it says (screens/Hologram.tsx). The frame
// is read only for the atlas with a part selected: a patient reconstruction never leaves the
// canvas (CLAUDE.md hard rule 8), and without a selection there is nothing for the AI to describe.

export type SnapshotDecision =
  | { capture: false; message: string }
  | { capture: true; message: (aiOn: boolean) => string };

export function decideSnapshot(atlas: boolean, selected: string | null): SnapshotDecision {
  if (!atlas) return { capture: false, message: 'Snapshots are for the atlas only.' };
  if (!selected) return { capture: false, message: 'Select a part first, then take the snapshot.' };
  return {
    capture: true,
    message: (aiOn) => (aiOn
      ? 'Snapshot taken: the AI will describe this view.'
      : 'Snapshot taken, but AI is off (no GROQ_API_KEY on the server).'),
  };
}
