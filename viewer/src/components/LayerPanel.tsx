import { useLayerStore } from '../store/layerStore';

/**
 * The body systems, one switch per group in the open study's manifest (refs/INTEGRATION.md §4.2),
 * inside the room's left panel (HUDOverlay). A group's GLB is fetched the first time it is shown,
 * so each switch carries its own loading state. The swatch is the system's anatomy colour; the
 * name always beside it, so colour is never the only identity.
 */
export default function LayerPanel() {
  const order = useLayerStore((s) => s.order);
  const groups = useLayerStore((s) => s.groups);
  const toggle = useLayerStore((s) => s.toggle);

  if (order.length === 0) return null;

  return (
    <div className="layer-list" role="group" aria-label="Body systems">
      {order.map((id) => {
        const g = groups[id];
        if (!g) return null;
        const loading = g.visible && !g.loaded;
        return (
          <button
            key={id}
            type="button"
            className={`layer-row${g.visible ? ' layer-row--on' : ''}`}
            style={{ '--sys-color': g.color } as React.CSSProperties}
            onClick={() => toggle(id)}
            title={`${g.visible ? 'Hide' : 'Show'} ${g.label}`}
            id={`layer-toggle-${id}`}
            aria-pressed={g.visible}
          >
            <span className="layer-row__swatch" aria-hidden="true" />
            <span className="layer-row__label">{g.label}</span>
            <span className="layer-row__count mono">{loading ? 'loading' : g.structureCount || ''}</span>
            <span className="layer-row__switch" aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
