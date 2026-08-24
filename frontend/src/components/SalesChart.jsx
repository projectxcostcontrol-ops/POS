import { useState } from 'react';

const W = 300;
const H = 130;
const PAD = 8;

/**
 * Sales over time: straight segments, a dot per data point, and a
 * least-squares trend line.
 *
 * Straight rather than curved on purpose - a smoothed line invents values
 * between the points it was given, so a day that jumped reads as a gentle
 * climb. The trend line is what answers "which way is this going", which
 * a jagged daily line genuinely can't: the eye follows the last spike
 * instead of the direction.
 */
export default function SalesChart({ points, formatLabel, formatValue }) {
  const [sel, setSel] = useState(-1);

  if (!points || points.length === 0) {
    return (
      <p style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center', padding: '36px 0' }}>
        ยังไม่มียอดขายในช่วงนี้
      </p>
    );
  }

  // A single point has no line to draw and no trend to fit.
  if (points.length === 1) {
    return (
      <p style={{ fontSize: 13, textAlign: 'center', padding: '36px 0' }}>
        {formatLabel(points[0])} · <b>{formatValue(points[0].sales)}</b>
      </p>
    );
  }

  const values = points.map((p) => p.sales);
  let max = Math.max(...values);
  let min = Math.min(...values);
  const span0 = max - min || 1;
  max += span0 * 0.15;          // headroom so the peak never touches the edge
  min -= span0 * 0.1;
  const span = max - min;

  const step = W / (points.length - 1);
  const pos = points.map((p, i) => [
    i * step,
    H - PAD - ((p.sales - min) / span) * (H - PAD * 2),
  ]);

  const line = 'M' + pos.map(([x, y]) => `${x},${y}`).join(' L');
  const area = `${line} L${W},${H} L0,${H} Z`;

  // Least-squares fit through the points.
  const n = pos.length;
  let sx = 0, sy = 0, sxy = 0, sxx = 0;
  pos.forEach(([x, y]) => { sx += x; sy += y; sxy += x * y; sxx += x * x; });
  const slope = (n * sxy - sx * sy) / ((n * sxx - sx * sx) || 1);
  const intercept = (sy - slope * sx) / n;

  const selected = sel >= 0 ? points[sel] : null;
  const selPos = sel >= 0 ? pos[sel] : null;

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        style={{ display: 'block', width: '100%', height: 130, touchAction: 'none' }}>
        <defs>
          <linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity=".22" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <path d={area} fill="url(#salesFill)" />
        <path d={`M0,${intercept} L${W},${slope * W + intercept}`} fill="none"
          stroke="var(--text-warning)" strokeWidth="1.5" strokeDasharray="5 4"
          opacity=".8" vectorEffect="non-scaling-stroke" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />

        {pos.map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r={i === sel ? 4.5 : 2.8}
            fill={i === sel ? 'var(--accent)' : 'var(--surface-2)'}
            stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        ))}

        {/* Invisible full-height columns: a 3px dot is unhittable with a
            thumb, so the whole slice around it is the target. */}
        {pos.map(([x], i) => (
          <rect key={`hit-${i}`} x={x - step / 2} y="0" width={step} height={H}
            fill="transparent" style={{ cursor: 'pointer' }}
            onClick={() => setSel(i === sel ? -1 : i)} />
        ))}
      </svg>

      {selected && (
        <div style={{
          position: 'absolute',
          left: `${(selPos[0] / W) * 100}%`,
          top: `${(selPos[1] / H) * 100}%`,
          transform: 'translate(-50%,-118%)',
          background: 'var(--text-primary)', color: '#fff',
          borderRadius: 8, padding: '7px 10px', fontSize: 11.5,
          whiteSpace: 'nowrap', pointerEvents: 'none', zIndex: 2,
        }}>
          <span style={{ fontSize: 14, fontWeight: 600, display: 'block' }}>
            {formatValue(selected.sales)}
          </span>
          <span style={{ opacity: .7, fontSize: 10.5 }}>{formatLabel(selected)}</span>
        </div>
      )}

      <div style={{
        display: 'flex', gap: 14, justifyContent: 'flex-end', marginTop: 8,
        fontSize: 10.5, color: 'var(--text-muted)',
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <i style={{ width: 14, borderTop: '2px solid var(--accent)' }} />ยอดขาย
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <i style={{ width: 14, borderTop: '2px dashed var(--text-warning)' }} />แนวโน้ม
        </span>
      </div>
    </div>
  );
}
