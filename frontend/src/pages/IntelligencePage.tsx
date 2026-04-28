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
import { asCurrency, asPercent, safeArray } from '../lib/format';
import type { CatalogSpySnapshotResponse, DashboardReportResponse } from '../types/api';

interface IntelligencePageProps {
  api: ApiClient;
  retailerId: number;
  refreshKey: number;
}

const COLORS = ['#111827', '#374151', '#6b7280', '#9ca3af'];

export function IntelligencePage({ api, retailerId, refreshKey }: IntelligencePageProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [dashboardReport, setDashboardReport] = useState<DashboardReportResponse | null>(null);
  const [liveCatalogSpy, setLiveCatalogSpy] = useState<CatalogSpySnapshotResponse | null>(null);

  const [marketItems, setMarketItems] = useState<Array<Record<string, unknown>>>([]);
  const [dropPatterns, setDropPatterns] = useState<Array<Record<string, unknown>>>([]);
  const [catalogItems, setCatalogItems] = useState<Array<Record<string, unknown>>>([]);
  const [forecasts, setForecasts] = useState<Array<Record<string, unknown>>>([]);

  useEffect(() => {
    if (retailerId <= 0) {
      setMarketItems([]);
      setDropPatterns([]);
      setCatalogItems([]);
      setForecasts([]);
      setDashboardReport(null);
      setLiveCatalogSpy(null);
      return;
    }

    let cancelled = false;

    async function load() {
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

        if (cancelled) return;
        setMarketItems(safeArray<Record<string, unknown>>(market.items));
        setDropPatterns(safeArray<Record<string, unknown>>(drop.patterns));
        setCatalogItems(safeArray<Record<string, unknown>>(catalog.items));
        setForecasts(safeArray<Record<string, unknown>>(demand.forecasts));
        setDashboardReport(dashboard);
        setLiveCatalogSpy(catalogSpy);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load intelligence data');
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
        {loading ? <p>Loading intelligence...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
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

        <div className="list-wrap mt-6">
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

      <Panel title="Drop Pattern Feed" subtitle="Top products with repeated competitor price drops">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Product</th>
                <th>SKU</th>
                <th>Drop %</th>
                <th>Streak</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {dropPatterns.slice(0, 25).map((row, idx) => (
                <tr key={`${String(row.retailer_sku || idx)}-${idx}`}>
                  <td>{String(row.product_name || row.name || '-')}</td>
                  <td>{String(row.retailer_sku || row.sku || '-')}</td>
                  <td>{asPercent(Number(row.drop_pct || row.drop_percent || 0) * 100)}</td>
                  <td>{Number(row.streak_count || row.streak || 0)}</td>
                  <td>{asPercent(Number(row.confidence || 0) * 100, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {dropPatterns.length === 0 ? <p>No drop pattern rows found.</p> : null}
        </div>
      </Panel>

      <Panel title="Market Intel Feed" subtitle="Sample records for quick analyst verification">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Competitor</th>
                <th>Product</th>
                <th>Competitor Price</th>
                <th>Retailer Price</th>
                <th>Gap</th>
                <th>Strategy</th>
              </tr>
            </thead>
            <tbody>
              {marketItems.slice(0, 30).map((row, idx) => {
                const competitorPrice = Number(row.competitor_price || row.market_price || 0);
                const retailerPrice = Number(row.retailer_price || row.current_price || 0);
                const gap = retailerPrice > 0 ? ((retailerPrice - competitorPrice) / retailerPrice) * 100 : 0;
                return (
                  <tr key={`${String(row.product_name || idx)}-${idx}`}>
                    <td>{String(row.competitor_name || row.competitor || '-')}</td>
                    <td>{String(row.product_name || row.name || '-')}</td>
                    <td>{asCurrency(competitorPrice)}</td>
                    <td>{asCurrency(retailerPrice)}</td>
                    <td>{asPercent(gap)}</td>
                    <td>{String(row.strategy_label || row.strategy || '-')}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {marketItems.length === 0 ? <p>No market intelligence rows found.</p> : null}
        </div>
      </Panel>
    </div>
  );
}
