import type { TrendPoint } from "../types";

export function ActivityChart({ data }: { data: TrendPoint[] }) {
  const width = 640;
  const height = 190;
  const padding = 24;
  const max = Math.max(...data.map((point) => point.orders), 1);
  const coords = data.map((point, index) => ({
    x: padding + (index * (width - padding * 2)) / (data.length - 1),
    y: height - padding - (point.orders / max) * (height - padding * 2),
  }));
  const line = coords.map(({ x, y }, index) => `${index === 0 ? "M" : "L"}${x},${y}`).join(" ");
  const area = `${line} L${coords.at(-1)?.x},${height - padding} L${coords[0].x},${height - padding} Z`;

  return (
    <div className="chart-wrap">
      <svg className="activity-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="最近七小时订单趋势">
        <defs>
          <linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#2f80ed" stopOpacity="0.3" />
            <stop offset="1" stopColor="#2f80ed" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75, 1].map((level) => (
          <line key={level} x1={padding} x2={width - padding} y1={height - padding - level * (height - padding * 2)} y2={height - padding - level * (height - padding * 2)} className="chart-grid" />
        ))}
        <path d={area} fill="url(#chart-fill)" />
        <path d={line} className="chart-line" />
        {coords.map(({ x, y }, index) => (
          <g key={data[index].label}>
            <circle cx={x} cy={y} r="4" className="chart-dot" />
            <text x={x} y={height - 3} textAnchor="middle" className="chart-label">{data[index].label}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}
