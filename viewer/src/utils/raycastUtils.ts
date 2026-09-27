import * as THREE from 'three';

/**
 * Converts 0-1 normalized coordinates (mirror-corrected relative to screen)
 * to Normalized Device Coordinates (-1 to 1) and configures a Three.js Raycaster.
 */
export function getFingerRay(
  fingertipX: number,
  fingertipY: number,
  camera: THREE.Camera,
  _size?: { width: number; height: number }
): THREE.Raycaster {
  // Translate to NDC: x is [-1, 1], y is [-1, 1] (y inverted: Three.js Y is up, MediaPipe Y is down)
  const ndcX = fingertipX * 2 - 1;
  const ndcY = -fingertipY * 2 + 1;

  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(new THREE.Vector2(ndcX, ndcY), camera);
  return raycaster;
}
