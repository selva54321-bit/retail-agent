interface KpiCardProps {
  label: string;
  value: string | number;
  hint?: string;
}

export function KpiCard({ label, value, hint }: KpiCardProps) {
  return (
    <article className="kpi-card fade-in">
      <p className="kpi-label">{label}</p>
      <strong className="kpi-value">{value}</strong>
      {hint ? <p className="kpi-hint">{hint}</p> : null}
    </article>
  );
}
