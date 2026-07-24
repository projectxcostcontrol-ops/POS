export default function BarChart({ labels, values, height = 200 }) {
  const max = Math.max(...values, 1);
  const barWidth = 100 / values.length;

  return (
    <div style={{ width: '100%', height }}>
      <svg width="100%" height="100%" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none">
        {values.map((v, i) => {
          const barHeight = (v / max) * (height - 20);
          const x = i * barWidth + barWidth * 0.15;
          const w = barWidth * 0.7;
          const y = height - 20 - barHeight;
          return (
            <rect key={i} x={x} y={y} width={w} height={barHeight}
              rx="1" fill="var(--accent, #2a78d6)" />
          );
        })}
      </svg>
      <div style={{ display: 'flex', marginTop: 4 }}>
        {labels.map((l, i) => (
          <div key={i} style={{
            flex: 1, fontSize: 10, color: 'var(--text-muted)', textAlign: 'center',
            overflow: 'hidden', whiteSpace: 'nowrap',
          }}>
            {l}
          </div>
        ))}
      </div>
    </div>
  );
}
