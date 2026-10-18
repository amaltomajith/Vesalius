import { useState } from 'react';
import { Table, ChartBar } from '@phosphor-icons/react';
import { SYSTEM_COLOUR, SYSTEM_LABEL, type System } from '../lib/stats';

interface Row { system: System; count: number; examples: string[] }

// under a bar there is room for a short name; the tooltip, table and aria-label say it in full
const SHORT: Partial<Record<System, string>> = { cardiovascular: 'Cardio', respiratory: 'Respir.' };

/** Structures per body system, as bars in a fixed system order (the order the palette was
 *  validated in). Every bar carries its count and name as text, so colour never carries identity
 *  alone; a table view shows the same numbers. Hover or focus a bar for a few of its structures. */
export default function SystemBars({ rows, caption }: { rows: Row[]; caption: string }) {
  const [asTable, setAsTable] = useState(false);
  const [hover, setHover] = useState<System | null>(null);
  const max = Math.max(1, ...rows.map((r) => r.count));
  // a calm axis: round ticks (1, 2, 2.5 or 5 times a power of ten), about four of them
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find((k) => k * mag >= raw) ?? 10) * mag;
  const top = Math.ceil(max / step) * step;
  const grid = Array.from({ length: Math.round(top / step) }, (_, i) => Math.round((i + 1) * step * 10) / 10);

  return (
    <figure className="sysbars">
      <figcaption className="sysbars__cap">
        <span>{caption}</span>
        <button type="button" className="icon-toggle" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}
          title={asTable ? 'Show as chart' : 'Show as table'}>
          {asTable ? <ChartBar size={16} /> : <Table size={16} />}
          <span className="sr-only">{asTable ? 'Show as chart' : 'Show as table'}</span>
        </button>
      </figcaption>

      {asTable ? (
        <table className="sysbars__table">
          <thead><tr><th>System</th><th>Structures</th><th>Largest named</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.system}>
                <td><i style={{ background: SYSTEM_COLOUR[r.system] }} aria-hidden="true" />{SYSTEM_LABEL[r.system]}</td>
                <td className="num">{r.count}</td>
                <td>{r.examples.slice(0, 2).join(', ') || 'None found'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="sysbars__plot" style={{ ['--top' as string]: top }}>
          <div className="sysbars__grid" aria-hidden="true">
            {grid.map((g) => <span key={g} style={{ bottom: `${(g / top) * 100}%` }}><em>{g}</em></span>)}
          </div>
          <ul className="sysbars__bars">
            {rows.map((r, i) => (
              <li key={r.system} style={{ ['--i' as string]: i }}>
                <button
                  type="button"
                  className={`sysbar${hover === r.system ? ' sysbar--on' : ''}${r.count === 0 ? ' sysbar--none' : ''}`}
                  onMouseEnter={() => setHover(r.system)}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(r.system)}
                  onBlur={() => setHover(null)}
                  aria-label={`${SYSTEM_LABEL[r.system]}: ${r.count} structures`}
                >
                  <span className="sysbar__col">
                    <span className="sysbar__value">{r.count}</span>
                    <span className="sysbar__fill" style={{ height: `${(r.count / top) * 100}%`, background: SYSTEM_COLOUR[r.system] }} />
                  </span>
                  <span className="sysbar__name" aria-hidden="true">{SHORT[r.system] ?? SYSTEM_LABEL[r.system]}</span>
                </button>
                {hover === r.system && r.examples.length > 0 && (
                  <div className="sysbar__tip" role="tooltip"
                    style={{ bottom: `calc(34px + (100% - 34px) * ${(r.count / top).toFixed(4)} + 30px)` }}>
                    <b>{SYSTEM_LABEL[r.system]}</b>
                    <span>{r.count} {r.count === 1 ? 'structure' : 'structures'}</span>
                    <ul>{r.examples.map((e) => <li key={e}>{e}</li>)}</ul>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </figure>
  );
}
