import type { LucideIcon } from "lucide-react";

type Tone = "blue" | "green" | "amber" | "red" | "violet" | "cyan";

interface MetricCardProps {
  label: string;
  value: string;
  detail: string;
  tone: Tone;
  icon: LucideIcon;
  progress?: number;
}

export function MetricCard({ label, value, detail, tone, icon: Icon, progress }: MetricCardProps) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <div className="metric-card__head">
        <span>{label}</span>
        <span className="metric-card__icon"><Icon size={16} strokeWidth={1.8} /></span>
      </div>
      <strong>{value}</strong>
      <div className="metric-card__foot">
        <span>{detail}</span>
        {progress !== undefined && (
          <span className="metric-card__progress" aria-label={`${progress}%`}>
            <i style={{ width: `${progress}%` }} />
          </span>
        )}
      </div>
    </article>
  );
}
