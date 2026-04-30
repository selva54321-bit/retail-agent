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
import { asPercent, safeArray } from '../lib/format';
import type { CatalogSpySnapshotResponse, DashboardReportResponse } from '../types/api';

interface IntelligencePageProps {
  api: ApiClient;
  retailerId: number;
  refreshKey: number;
}

const COLORS = ['#111827', '#374151', '#6b7280', '#9ca3af'];
const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function parseInsightPayload(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object') return value as Record<string, unknown>;
  try {
    return JSON.parse(String(value).replace(/'/g, '"')) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function strategyInsight(row: Record<string, unknown>): string {
  const insights = parseInsightPayload(row.insights_json || row.insights);
  const patterns = safeArray<string>(insights.price_patterns);
  if (patterns[0]) return patterns[0];
  if (insights.price_pattern) return String(insights.price_pattern);

  const flashSale = parseInsightPayload(insights.latest_flash_sale);
  if (flashSale.product) {
    const drop = flashSale.drop_pct !== undefined ? ` dropped ${flashSale.drop_pct}%` : ' flash-sale signal';
    return `${String(flashSale.product).slice(0, 54)}${drop}`;
  }

  if (insights.focus_product) return String(insights.focus_product);
  if (insights.note) return String(insights.note);
  return '-';
}

function dayName(value: unknown): string {
  const index = Number(value);
  if (!Number.isInteger(index) || index < 0 || index >= DAY_NAMES.length) return '-';
  return DAY_NAMES[index];
}

export function IntelligencePage({ api, retailerId, refreshKey }: IntelligencePageProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [dashboardReport, setDashboardReport] = useState<DashboardReportResponse | null>(null);
  const [liveCatalogSpy, setLiveCatalogSpy] = useState<CatalogSpySnapshotResponse | null>(null);

  const [marketItems, setMarketItems] = useState<Array<Record<string, unknown>>>([]);
  const [dropPatterns, setDropPatterns] = useState<Array<Record<string, unknown>>>([]);
  const [catalogItems, setCatalogItems] = useState<Array<Record<string, unknown>>>([]);
  const [forecasts, setForecasts] = useState<Array<Record<string, unknown>>>([]);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  // Centralized load function for manual refresh and initial load
  async function loadAllData() {
    if (retailerId <= 0) return;
    setLoading(true);
    setError('');
    try {
      const [market, drop, catalog, demand] = await Promise.all([
        api.getMarketIntelligence(retailerId, 20),
        api.getDropPatterns(retailerId),
        api.getCompetitorCatalog(retailerId),
        api.getDemandForecasts(retailerId, 100),
      ]);
      const dashboard = await api.getDashboardLatest(retailerId);
      const catalogSpy = await api.getCatalogSpySnapshot(retailerId);

      setMarketItems(safeArray<Record<string, unknown>>(market.items));
      setDropPatterns(safeArray<Record<string, unknown>>(drop.patterns));
      setCatalogItems(safeArray<Record<string, unknown>>(catalog.items));
      setForecasts(safeArray<Record<string, unknown>>(demand.forecasts));
      setDashboardReport(dashboard);
      setLiveCatalogSpy(catalogSpy);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load intelligence data');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAllData();
  }, [api, retailerId, refreshKey]);

  const strategyMix = useMemo(() => {
    const summary = new Map<string, number>();
    for (const item of marketItems) {
      const key = String(item.strategy_label || item.strategy || 'unknown');
      summary.set(key, (summary.get(key) || 0) + 1);
    }
    return Array.from(summary.entries()).map(([name, value]) => ({ name, value }));
  }, [marketItems]);

  const demandMix = useMemo(() => {
    const summary = new Map<string, number>();
    for (const item of forecasts) {
      const key = String(item.demand_signal || item.signal || 'flat').toLowerCase();
      summary.set(key, (summary.get(key) || 0) + 1);
    }
    return Array.from(summary.entries()).map(([name, value]) => ({ name, value }));
  }, [forecasts]);

  const competitorMix = useMemo(() => {
    const summary = new Map<string, number>();
    for (const item of catalogItems) {
      const key = String(item.competitor_name || item.competitor || 'unknown');
      summary.set(key, (summary.get(key) || 0) + 1);
    }
    return Array.from(summary.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 10);
  }, [catalogItems]);

  const marketStrategyRows = useMemo(() => {
    const latestByCompetitor = new Map<string, Record<string, unknown>>();
    for (const item of marketItems) {
      const competitor = String(item.competitor_name || item.competitor || 'unknown');
      if (!latestByCompetitor.has(competitor)) {
        latestByCompetitor.set(competitor, item);
      }
    }
    return Array.from(latestByCompetitor.values());
  }, [marketItems]);

  const catalogSpyAlerts = useMemo(
    () => {
      const dashboardAlerts = safeArray<Record<string, unknown>>(dashboardReport?.catalog_alerts);
      return dashboardAlerts.length > 0 ? dashboardAlerts : safeArray<Record<string, unknown>>(liveCatalogSpy?.catalog_alerts);
    },
    [dashboardReport, liveCatalogSpy],
  );

  const fastMovers = useMemo(
    () => {
      const dashboardFastMovers = safeArray<Record<string, unknown>>(dashboardReport?.fast_movers);
      return dashboardFastMovers.length > 0 ? dashboardFastMovers : safeArray<Record<string, unknown>>(liveCatalogSpy?.fast_movers);
    },
    [dashboardReport, liveCatalogSpy],
  );

  return (
    <div className="page-grid">
      <Panel title="Intelligence Snapshot" subtitle="Aggregated competitor and demand signals">
        <div className="flex items-start justify-between">
          <div>
            {loading ? <p>Loading intelligence...</p> : null}
            {error ? <p className="error-text">{error}</p> : null}
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => void loadAllData()} className="px-3 py-1 bg-secondary rounded-md text-sm">Refresh</button>
            <div className="text-xs text-muted-foreground">{lastUpdated ? `Updated ${lastUpdated.toLocaleString()}` : '—'}</div>
          </div>
        </div>
        <div className="kpi-grid">
          <KpiCard label="Market Intelligence Rows" value={marketItems.length} />
          <KpiCard label="Drop Patterns" value={dropPatterns.length} />
          <KpiCard label="Competitor Catalog Rows" value={catalogItems.length} />
          <KpiCard label="Demand Forecast Rows" value={forecasts.length} />
        </div>
      </Panel>

      <Panel title="CatalogSpy" subtitle="Stock availability, new arrivals, discontinued, and fast movers">
        <div className="kpi-grid">
          <KpiCard label="New Arrivals" value={catalogSpyAlerts.filter((item) => String(item.type || '') === 'new_arrival').length} />
          <KpiCard label="Stock-Outs" value={catalogSpyAlerts.filter((item) => String(item.type || '') === 'stock_out').length} />
          <KpiCard label="Possibly Discontinued" value={catalogSpyAlerts.filter((item) => String(item.type || '') === 'discontinued').length} />
          <KpiCard label="Fast Movers" value={fastMovers.length} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
          <div className="list-wrap">
            {catalogSpyAlerts.length === 0 ? <p>No CatalogSpy alerts captured for the latest cycle.</p> : null}
            {catalogSpyAlerts.map((alert, idx) => (
              <article key={`${String(alert.message || 'catalog')}-${idx}`} className="list-item">
                <span className={`badge ${String(alert.severity || 'medium').toLowerCase()}`}>
                  {String(alert.type || 'catalog_spy')}
                </span>
                <p>{String(alert.message || '')}</p>
              </article>
            ))}
          </div>

          <div className="bg-card rounded-2xl p-4 border border-border">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-lg font-semibold">Fast Movers</h4>
              <div className="text-sm text-muted-foreground">{fastMovers.length} items</div>
            </div>
            {fastMovers.length === 0 ? (
              <p className="text-sm text-muted-foreground">No fast movers detected.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse min-w-[600px]">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="pb-2 font-semibold">Product</th>
                      <th className="pb-2 font-semibold">SKU</th>
                      <th className="pb-2 font-semibold">Velocity</th>
                      <th className="pb-2 font-semibold">Seen</th>
                      <th className="pb-2 font-semibold">Last Seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fastMovers.map((fm: any, idx: number) => (
                      <tr key={`${fm.retailer_sku || idx}-${idx}`} className="border-b border-border/50 last:border-0 hover:bg-muted/30">
                        <td className="py-2 pr-4">
                          <div className="font-medium text-sm line-clamp-2">{String(fm.product_name || fm.name || fm.message || '-')}</div>
                        </td>
                        <td className="py-2">{String(fm.retailer_sku || fm.sku || '-')}</td>
                        <td className="py-2">{fm.velocity !== undefined ? String(fm.velocity) : '-'}</td>
                        <td className="py-2">{Number(fm.times_seen || fm.times || 0)}</td>
                        <td className="py-2 text-sm text-muted-foreground">{fm.last_seen ? new Date(fm.last_seen).toLocaleDateString() : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </Panel>

      <Panel title="Strategy Mix" subtitle="Pricing strategy labels seen in market intelligence">
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Tooltip />
              <Pie data={strategyMix} dataKey="value" nameKey="name" outerRadius={90} innerRadius={46}>
                {strategyMix.map((entry, index) => (
                  <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="Demand Signal Mix" subtitle="Demand forecast label distribution">
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={demandMix}>
              <CartesianGrid strokeDasharray="3 3" stroke="#d1d5db" />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="value" fill="#111827" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="Competitor Coverage" subtitle="Rows captured per competitor in catalog snapshots">
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={competitorMix}>
              <CartesianGrid strokeDasharray="3 3" stroke="#d1d5db" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={70} />
              <YAxis />
              <Tooltip />
              <Bar dataKey="value" fill="#374151" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title="Demand Forecast Feed" subtitle="Predicted SKU demand signals based on price velocity and stock-outs">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Product</th>
                <th>Signal</th>
                <th>Confidence</th>
                <th>Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {forecasts.slice(0, 30).map((row, idx) => (
                <tr key={`${String(row.catalog_sku || idx)}-${idx}`}>
                  <td className="font-medium text-sm">{String(row.product_name || row.name || row.catalog_sku || '-')}</td>
                  <td>
                    <span className={`badge ${String(row.demand_signal || 'stable').toLowerCase()}`}>
                      {String(row.demand_signal || 'stable')}
                    </span>
                  </td>
                  <td>
                    <span className="text-xs text-muted-foreground uppercase">{String(row.confidence || 'low')}</span>
                  </td>
                  <td className="text-sm italic">{String(row.recommendation || '-')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {forecasts.length === 0 ? <p>No demand forecast rows found.</p> : null}
        </div>
      </Panel>

      <Panel title="Drop Pattern Feed" subtitle="Repeated competitor price-drop patterns from the Intel agent">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Competitor</th>
                <th>Product</th>
                <th>SKU</th>
                <th>Pattern Day</th>
                <th>Avg Drop</th>
                <th>Max Drop</th>
                <th>Drops</th>
                <th>Consistency</th>
                <th>Next Drop</th>
              </tr>
            </thead>
            <tbody>
              {dropPatterns.slice(0, 25).map((row, idx) => (
                <tr key={`${String(row.competitor_name || 'pattern')}-${String(row.catalog_sku || row.product_name || idx)}-${idx}`}>
                  <td>{String(row.competitor_name || row.competitor || '-')}</td>
                  <td>{String(row.product_name || row.name || '-')}</td>
                  <td>{String(row.catalog_sku || row.retailer_sku || row.sku || '-')}</td>
                  <td>{dayName(row.peak_day_of_week)}</td>
                  <td>{asPercent(Number(row.avg_drop_pct || 0))}</td>
                  <td>{asPercent(Number(row.max_drop_pct || 0))}</td>
                  <td>{Number(row.drop_count || 0)}</td>
                  <td>{asPercent(Number(row.consistency_score || 0) * 100, 0)}</td>
                  <td>{String(row.next_predicted_date || '-')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {dropPatterns.length === 0 ? <p>No drop pattern rows found.</p> : null}
        </div>
      </Panel>

      <Panel title="Market Intel Feed" subtitle="Competitor-level strategy signals from the Intel agent">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Brand</th>
                <th>Strategy</th>
                <th>Avg Gap</th>
                <th>Key Insight</th>
              </tr>
            </thead>
            <tbody>
              {marketStrategyRows.slice(0, 30).map((row, idx) => (
                <tr key={`${String(row.competitor_name || row.competitor || idx)}-${idx}`}>
                  <td>{String(row.competitor_name || row.competitor || '-')}</td>
                  <td>
                    <span className="badge neutral">{String(row.strategy_label || row.strategy || 'unknown')}</span>
                  </td>
                  <td>{asPercent(Number(row.avg_price_gap_pct || 0))}</td>
                  <td>{strategyInsight(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {marketItems.length === 0 ? <p>No market intelligence rows found.</p> : null}
        </div>
      </Panel>
    </div>
  );
}
