import { useState } from 'react';

// Room for the axis labels. Without it the leftmost point sits on the Y
// numbers and the bottom row of X labels gets clipped.
const W = 320;

/**
 * Sales over time.
 *
 * The axis is fixed by the period, not by the data. A day always runs
 * 00:00–24:00 even if the shop only sold between 11 and 2, and every day
 * in a range gets a slot even if it took nothing. Drawing only the hours
 * that had sales made the axis stretch and squash between refreshes, so
 * two charts of the same shop were never comparable - and a dead
 * afternoon vanished instead of showing as the flat line it was.
 *
 * Straight segments rather than a smoothed curve: a curve invents values
 * between the points it was given, so a lunch rush reads as a gentle
 * climb from breakfast. The dashed trend line is a least-squares fit,
 * which answers "which way is this going" - something a jagged daily
 * line can't, because the eye follows the last spike instead.
 */
export default function SalesChart({ points, from, to, granularity, formatValue, compact = false }) {
  const [sel, setSel] = useState(-1);
  // The receipts page has a wide content column, so the default 320:170
  // ratio became much taller than the information justified. A shallower
  // viewBox keeps labels and hit targets correctly proportioned without
  // stretching the SVG or changing any chart behaviour.
  const H = compact ? 122 : 170;
  const M = { top: 10, right: 8, bottom: 22, left: 34 };
  const PW = W - M.left - M.right;
  const PH = H - M.top - M.bottom;

  const series = buildSeries(points, from, to, granularity);
  if (series.length === 0) {
    return (
      <p style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center', padding: '44px 0' }}>
        ยังไม่มียอดขายในช่วงนี้
      </p>
    );
  }

  const max = Math.max(...series.map((p) => p.value));
  const ticks = yTicks(max);
  const top = ticks[ticks.length - 1] || 1;

  const x = (i) => M.left + (series.length === 1 ? PW / 2 : (i / (series.length - 1)) * PW);
  const y = (v) => M.top + PH - (v / top) * PH;

  const pos = series.map((p, i) => [x(i), y(p.value)]);
  const line = 'M' + pos.map(([px, py]) => `${px},${py}`).join(' L');
  const area = `${line} L${x(series.length - 1)},${M.top + PH} L${x(0)},${M.top + PH} Z`;

  // Least-squares fit, clamped to the plot so a steep trend can't run off
  // the frame and cross the axis labels.
  const n = pos.length;
  let sx = 0, sy = 0, sxy = 0, sxx = 0;
  pos.forEach(([px, py]) => { sx += px; sy += py; sxy += px * py; sxx += px * px; });
  const denom = n * sxx - sx * sx;
  const slope = denom ? (n * sxy - sx * sy) / denom : 0;
  const intercept = (sy - slope * sx) / n;
  const clamp = (v) => Math.max(M.top, Math.min(M.top + PH, v));
  const t0 = clamp(slope * M.left + intercept);
  const t1 = clamp(slope * (M.left + PW) + intercept);

  const selected = sel >= 0 ? series[sel] : null;
  const slot = PW / (series.length || 1);

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', width: '100%', height: 'auto' }}>
        <defs>
          <linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity=".22" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {ticks.map((v) => (
          <g key={v}>
            <line x1={M.left} y1={y(v)} x2={M.left + PW} y2={y(v)}
              stroke="var(--border)" strokeWidth="1" />
            <text x={M.left - 6} y={y(v) + 3} textAnchor="end"
              fontSize="8" fill="var(--text-muted)">{shortNumber(v)}</text>
          </g>
        ))}

        <path d={area} fill="url(#salesFill)" />
        <path d={`M${M.left},${t0} L${M.left + PW},${t1}`} fill="none"
          stroke="var(--text-warning)" strokeWidth="1.5" strokeDasharray="5 4" opacity=".8" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />

        {pos.map(([px, py], i) => (
          <circle key={i} cx={px} cy={py} r={i === sel ? 4 : 2.2}
            fill={i === sel ? 'var(--accent)' : 'var(--surface-2)'}
            stroke="var(--accent)" strokeWidth="1.6" />
        ))}

        {series.map((p, i) => (p.label ? (
          <text key={`l-${i}`} x={x(i)} y={H - 7} textAnchor="middle"
            fontSize="8" fill="var(--text-muted)">{p.label}</text>
        ) : null))}

        {/* Invisible full-height columns: a 2px dot is unhittable with a
            thumb, so the whole slice around it is the target. */}
        {series.map((p, i) => (
          <rect key={`h-${i}`} x={x(i) - slot / 2} y={M.top} width={slot} height={PH}
            fill="transparent" style={{ cursor: 'pointer' }}
            onClick={() => setSel(i === sel ? -1 : i)} />
        ))}
      </svg>

      {selected && (
        <div style={{
          position: 'absolute',
          left: `${(x(sel) / W) * 100}%`,
          top: `${(y(selected.value) / H) * 100}%`,
          transform: 'translate(-50%,-118%)',
          background: 'var(--text-primary)', color: '#fff',
          borderRadius: 8, padding: '6px 9px', fontSize: 11,
          whiteSpace: 'nowrap', pointerEvents: 'none', zIndex: 2,
        }}>
          <span style={{ fontSize: 13, fontWeight: 600, display: 'block' }}>
            {formatValue(selected.value)}
          </span>
          <span style={{ opacity: .7, fontSize: 10 }}>{selected.tooltip}</span>
        </div>
      )}

      <div style={{
        display: 'flex', gap: 14, justifyContent: 'flex-end', marginTop: 4,
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

/**
 * Every slot in the period, whether or not it sold anything.
 *
 * The API returns only buckets that had sales, which is correct for it to
 * do - but a chart built straight from that has no way to show a quiet
 * hour, and its axis changes shape with the data.
 */
function buildSeries(points, from, to, granularity) {
  const byKey = {};
  (points || []).forEach((p) => { byKey[p.t] = p.sales; });

  const start = new Date(from);
  const end = new Date(to);
  if (isNaN(start.getTime()) || isNaN(end.getTime())) {
    // No usable window - fall back to whatever the API gave rather than
    // rendering nothing.
    return (points || []).map((p) => ({
      value: p.sales, label: '', tooltip: p.t,
    }));
  }

  const out = [];

  if (granularity === 'hour') {
    // A full trading day, midnight to midnight, so today's shape can be
    // compared with yesterday's at a glance.
    const day = new Date(start);
    day.setHours(0, 0, 0, 0);
    for (let h = 0; h < 24; h++) {
      const at = new Date(day);
      at.setHours(h);
      out.push({
        value: byKey[keyFor(at, true)] || 0,
        label: h % 4 === 0 ? String(h) : '',
        tooltip: `${String(h).padStart(2, '0')}:00 น.`,
      });
    }
    return out;
  }

  const cur = new Date(start);
  cur.setHours(0, 0, 0, 0);
  const last = new Date(end);
  last.setHours(0, 0, 0, 0);

  const days = Math.round((last - cur) / 86400000) + 1;
  const every = Math.max(1, Math.ceil(days / 6));

  // The guard is a safety net, not a feature: a malformed range should
  // not spin forever.
  for (let i = 0; cur <= last && i < 400; i++) {
    out.push({
      value: byKey[keyFor(cur, false)] || 0,
      label: i % every === 0 ? String(cur.getDate()) : '',
      tooltip: cur.toLocaleDateString('th-TH', { day: 'numeric', month: 'short' }),
    });
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}

/**
 * Matches the key the backend buckets by, which is the shop's local clock
 * (the request sends its UTC offset). Building these in UTC instead would
 * shift the whole day by seven hours in Thailand.
 */
function keyFor(d, withHour) {
  const p = (v) => String(v).padStart(2, '0');
  const base = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return withHour ? `${base}T${p(d.getHours())}:00` : base;
}

/** Round numbers on the Y axis - 0/500/1000, not 0/437/874. */
function yTicks(max) {
  if (!max || max <= 0) return [0, 1];
  const rough = max / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * mag >= rough) || 10) * mag;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 100) / 100);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function shortNumber(v) {
  if (v >= 1000000) return `${(v / 1000000).toFixed(v % 1000000 ? 1 : 0)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k`;
  return String(Math.round(v));
}
