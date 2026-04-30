import { useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { KpiCard } from '../components/KpiCard';
import { Panel } from '../components/Panel';
import { ApiClient } from '../lib/api';
import { asDate, asPercent, cycleLabel, safeArray } from '../lib/format';
import type { DashboardReportResponse } from '../types/api';

interface OverviewPageProps {
  api: ApiClient;
  retailerId: number;
  cycleId: string;
  onCycleChange: (cycleId: string) => void;
  refreshKey: number;
}

const PIE_COLORS = ['#0f172a', '#374151', '#6b7280'];

export function OverviewPage({ api, retailerId, cycleId, onCycleChange, refreshKey }: OverviewPageProps) {
  const [report, setReport] = useState<DashboardReportResponse | null>(null);
  const [cycles, setCycles] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (retailerId <= 0) {
      setReport(null);
      setCycles([]);
      return;
    }

    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const cycleRes = await api.getCycles(retailerId, 20);
        if (!cancelled) {
          setCycles(cycleRes.cycles || []);
        }

        const data = cycleId
          ? await api.getDashboardCycle(retailerId, cycleId)
          : await api.getDashboardLatest(retailerId);

        if (!cancelled) {
          setReport(data);
          if (!cycleId && data.cycle_id) {
            onCycleChange(data.cycle_id);
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load dashboard data');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [api, retailerId, cycleId, refreshKey, onCycleChange]);

  const analytics = safeArray<Record<string, unknown>>(report?.analytics);
  const recommendations = safeArray<Record<string, unknown>>(report?.recommendations);
  const alerts = safeArray<Record<string, unknown>>(report?.alerts);

  const gapData = useMemo(
    () =>
      analytics.slice(0, 8).map((item) => ({
        name: String(item.product_name || '').slice(0, 22),
        gap: Number(item.price_gap_pct_to_min || 0) * 100,
      })),
    [analytics],
  );

  const recData = useMemo(
    () =>
      recommendations.slice(0, 8).map((item) => ({
        name: String(item.product_name || '').slice(0, 22),
        shift: Number(item.price_change_pct || 0) * 100,
      })),
    [recommendations],
  );

  const severityData = useMemo(() => {
    const summary = new Map<string, number>();
    for (const alert of alerts) {
      const severity = String(alert.severity || 'medium').toLowerCase();
      summary.set(severity, (summary.get(severity) || 0) + 1);
    }
    return Array.from(summary.entries()).map(([name, value]) => ({ name, value }));
  }, [alerts]);

  const cycleLog = report?.cycle_log || {};

  return (
    <div className="page-grid">
      <Panel
        title="Cycle Overview"
        subtitle="Latest or selected cycle intelligence"
        rightSlot={
          <select
            value={cycleId || ''}
            onChange={(event) => onCycleChange(event.target.value)}
            className="field"
          >
            <option value="">Latest</option>
            {cycles.map((cycle) => {
              const id = String(cycle.cycle_id || '');
              return (
                <option key={id} value={id}>
                  {cycleLabel(id, cycle.started_at)}
                </option>
              );
            })}
          </select>
        }
      >
        {loading ? <p>Loading dashboard...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {!loading && !error && report ? (
          <>
            <div className="kpi-grid">
              <KpiCard label="Cycle ID" value={report.cycle_id} hint={asDate(cycleLog.started_at)} />
              <KpiCard label="Records Scraped" value={Number(cycleLog.records_scraped || 0)} />
              <KpiCard label="Matches" value={Number(cycleLog.matches_found || 0)} />
              <KpiCard label="Recommendations" value={recommendations.length} />
              <KpiCard label="Alerts" value={alerts.length} />
              <KpiCard label="Status" value={String(cycleLog.status || 'unknown')} />
            </div>

            <div className="briefing-box">
              <h4>Morning Briefing</h4>
              <p>{report.briefing || 'No briefing generated for this cycle.'}</p>
            </div>
          </>
        ) : null}
      </Panel>

      <Panel title="Gap To Cheapest" subtitle="How far each tracked SKU is from market floor">
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={gapData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#d1d5db" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={70} />
              <YAxis tickFormatter={(v) => asPercent(v)} />
              <Tooltip formatter={(value) => `${Number(value).toFixed(1)}%`} />
              <Bar dataKey="gap" fill="#111827" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="Recommended Price Shift" subtitle="Percent move proposed by pricing agent">
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={recData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#d1d5db" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={70} />
              <YAxis tickFormatter={(v) => asPercent(v)} />
              <Tooltip formatter={(value) => `${Number(value).toFixed(1)}%`} />
              <Bar dataKey="shift" radius={[6, 6, 0, 0]}>
                {recData.map((item) => (
                  <Cell key={item.name} fill={item.shift <= 0 ? '#111827' : '#6b7280'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="Alert Severity Mix" subtitle="Distribution across low/medium/high alerts">
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Tooltip />
              <Pie dataKey="value" nameKey="name" data={severityData} outerRadius={90} innerRadius={48}>
                {severityData.map((entry, index) => (
                  <Cell key={entry.name} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="Alert Feed" subtitle="Actionable signals generated for this cycle">
        <div className="list-wrap">
          {alerts.length === 0 ? <p>No alerts recorded.</p> : null}
          {alerts.map((alert, idx) => (
            <article key={`${String(alert.message)}-${idx}`} className="list-item">
              <span className={`badge ${String(alert.severity || 'medium').toLowerCase()}`}>
                {String(alert.severity || 'medium')}
              </span>
              <p>{String(alert.message || '')}</p>
            </article>
          ))}
        </div>
      </Panel>
    </div>
  );
}
