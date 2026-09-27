import { useLayerStore } from '../store/layerStore';

/**
 * Compact left-side layer panel: one toggle per group in the open study's manifest
 * (refs/INTEGRATION.md §4.2). A group's GLB is fetched the first time it is shown, so each
 * toggle carries its own loading state.
 */
export default function LayerPanel() {
  const order = useLayerStore((s) => s.order);
  const groups = useLayerStore((s) => s.groups);
  const toggle = useLayerStore((s) => s.toggle);

  if (order.length === 0) return null;

  return (
    <aside className="layer-panel hud-interactive" aria-label="Layer Controls">
      <div className="panel-label">LAYERS</div>

      {order.map((id) => {
        const g = groups[id];
        if (!g) return null;
        const loading = g.visible && !g.loaded;
        return (
          <button
            key={id}
            className={`layer-btn ${g.visible ? 'layer-btn--on' : 'layer-btn--off'}`}
            style={{ '--sys-color': g.color } as React.CSSProperties}
            onClick={() => toggle(id)}
            title={`${g.visible ? 'Hide' : 'Show'} ${g.label}`}
            id={`layer-toggle-${id}`}
            aria-pressed={g.visible}
          >
            <span className="layer-btn__swatch" aria-hidden="true" />
            <span className="layer-btn__label">{g.label}</span>
            <span className="layer-btn__count">{loading ? '···' : g.structureCount || ''}</span>
            <span className="layer-btn__indicator" aria-hidden="true" />
          </button>
        );
      })}
    </aside>
  );
}
