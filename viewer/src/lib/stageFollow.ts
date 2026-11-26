// Where the room's stand goes (components/HallStage.tsx): with the body. Hand gestures pan, zoom
// and turn the model's own group (AnatomicalModel), so a stand fixed at the origin was left empty
// when the body moved (user report, 2026-10-18). The stand takes the model's pan and its gesture
// zoom (the group's scale over the auto-fit), never its turn: the pedestal is round and the orbit
// ring faces the camera. Tipped forward or back, the body no longer stands on anything, so the
// stand fades out, and comes back as the body is turned upright.

export const TILT_START = 0.45;  // rad of forward / back tilt the stand still holds...
export const TILT_FADE = 0.3;    // ...then it fades out over this much more

export interface ModelPose { x: number; y: number; scale: number; fit: number; rotX: number }

export function stageFollow(m: ModelPose): { x: number; y: number; scale: number; opacity: number } {
  const zoom = m.fit > 0 ? m.scale / m.fit : 1;
  const opacity = Math.min(1, Math.max(0, 1 - (Math.abs(m.rotX) - TILT_START) / TILT_FADE));
  return { x: m.x, y: m.y, scale: zoom, opacity };
}
