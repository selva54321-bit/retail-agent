export function asCurrency(value: unknown): string {
  const n = Number(value || 0);
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(n);
}

export function asPercent(value: unknown, digits = 1): string {
  const n = Number(value || 0);
  return `${n.toFixed(digits)}%`;
}

export function asDate(value: unknown): string {
  if (!value) return '-';
  const raw = String(value).replace('T', ' ');
  return raw.slice(0, 19);
}

export function cycleLabel(cycleId: unknown, startedAt?: unknown): string {
  const id = String(cycleId || '');
  if (!id) return '-';
  const started = asDate(startedAt);
  return started === '-' ? id : `${id} (${started})`;
}

export function parseCompetitorPrices(value: unknown): Record<string, number> {
  if (!value) return {};
  if (typeof value === 'object') return value as Record<string, number>;
  try {
    return JSON.parse(String(value).replace(/'/g, '"')) as Record<string, number>;
  } catch {
    return {};
  }
}

export function safeArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}
