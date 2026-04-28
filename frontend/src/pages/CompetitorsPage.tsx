import { useEffect, useMemo, useState } from 'react';

import { KpiCard } from '../components/KpiCard';
import { Panel } from '../components/Panel';
import { ApiClient } from '../lib/api';
import { asCurrency, safeArray } from '../lib/format';
import type { RetailerProfilePayload } from '../types/api';

interface CompetitorsPageProps {
  api: ApiClient;
  retailerId: number;
}

interface CompetitorCatalogItem {
  competitor_name?: string;
  product_name?: string;
  catalog_sku?: string;
  price?: number;
  in_stock?: boolean;
  times_out_of_stock?: number;
  last_seen_at?: string;
}

interface CompetitorSummary {
  name: string;
  items: CompetitorCatalogItem[];
  productCount: number;
  inStockCount: number;
  outOfStockCount: number;
  averagePrice: number;
  minPrice: number;
  maxPrice: number;
  isKnown: boolean;
}

function buildCompetitorSummaries(items: CompetitorCatalogItem[], knownCompetitors: string[]): CompetitorSummary[] {
  const grouped = new Map<string, CompetitorCatalogItem[]>();

  for (const item of items) {
    const name = item.competitor_name || 'Unknown Competitor';
    const bucket = grouped.get(name) || [];
    bucket.push(item);
    grouped.set(name, bucket);
  }

  return Array.from(grouped.entries())
    .map(([name, competitorItems]) => {
      const prices = competitorItems.map((item) => Number(item.price || 0)).filter((price) => Number.isFinite(price) && price > 0);
      const inStockCount = competitorItems.filter((item) => item.in_stock !== false).length;
      const outOfStockCount = competitorItems.length - inStockCount;
      const averagePrice = prices.length > 0 ? prices.reduce((total, price) => total + price, 0) / prices.length : 0;
      const minPrice = prices.length > 0 ? Math.min(...prices) : 0;
      const maxPrice = prices.length > 0 ? Math.max(...prices) : 0;

      return {
        name,
        items: competitorItems,
        productCount: competitorItems.length,
        inStockCount,
        outOfStockCount,
        averagePrice,
        minPrice,
        maxPrice,
        isKnown: knownCompetitors.includes(name),
      };
    })
    .sort((left, right) => right.productCount - left.productCount || left.name.localeCompare(right.name));
}

export function CompetitorsPage({ api, retailerId }: CompetitorsPageProps) {
  const [profile, setProfile] = useState<RetailerProfilePayload | null>(null);
  const [catalogItems, setCatalogItems] = useState<CompetitorCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [query, setQuery] = useState('');

  const loadData = async (isRefresh = false) => {
    if (!retailerId || retailerId <= 0) return;
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    try {
      const [profileRes, catalogRes] = await Promise.all([
        api.getRetailer(retailerId),
        api.getCompetitorCatalog(retailerId),
      ]);
      setProfile(profileRes);
      setCatalogItems(safeArray<CompetitorCatalogItem>(catalogRes.items));
      setLastUpdated(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load competitor data');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [retailerId]);

  const knownCompetitors = profile?.known_competitors || [];

  const summaries = useMemo(
    () => buildCompetitorSummaries(catalogItems, knownCompetitors),
    [catalogItems, knownCompetitors],
  );

  const filteredSummaries = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return summaries;

    return summaries.filter((summary) => {
      const nameMatch = summary.name.toLowerCase().includes(normalizedQuery);
      const productMatch = summary.items.some((item) =>
        String(item.product_name || '').toLowerCase().includes(normalizedQuery) ||
        String(item.catalog_sku || '').toLowerCase().includes(normalizedQuery),
      );
      return nameMatch || productMatch;
    });
  }, [query, summaries]);

  const totalProducts = catalogItems.length;
  const trackedCompetitors = summaries.length;
  const discoveredCompetitors = summaries.filter((summary) => !summary.isKnown).length;
  const inStockRate = totalProducts > 0 ? (catalogItems.filter((item) => item.in_stock !== false).length / totalProducts) * 100 : 0;
  const priceValues = catalogItems.map((item) => Number(item.price || 0)).filter((price) => Number.isFinite(price) && price > 0);
  const averageCatalogPrice = priceValues.length > 0 ? priceValues.reduce((total, price) => total + price, 0) / priceValues.length : 0;

  if (loading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-50 text-red-700 p-4 rounded-xl flex items-center gap-3">
          <span className="font-semibold">Error</span>
          <span>{error}</span>
          <button onClick={() => void loadData()} className="ml-auto underline font-medium">
            Retry
          </button>
        </div>
      </div>
    );
  }

  const scoutDiscoveries = summaries.filter((summary) => !summary.isKnown).map((summary) => summary.name);

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto w-full animate-in fade-in duration-500">
      <Panel
        title="Competitor Dashboard"
        subtitle="Track known competitors, surface scout discoveries, and review catalog coverage in one view"
        rightSlot={
          <button
            onClick={() => void loadData(true)}
            disabled={refreshing}
            className="inline-flex items-center px-4 py-2 bg-secondary text-primary font-medium rounded-lg hover:bg-secondary/80 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary transition-colors disabled:opacity-50"
          >
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        }
      >
        <div className="flex flex-col gap-4">
          <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Retailer scope</p>
              <h2 className="text-2xl font-semibold mt-1">{profile?.store_name || 'Retailer'} Competitor Intelligence</h2>
              <p className="text-sm text-muted-foreground mt-2">
                {lastUpdated ? `Last updated ${lastUpdated.toLocaleString()}` : 'No data loaded yet'}
              </p>
            </div>
            <label className="flex items-center gap-3 bg-background border border-border rounded-xl px-4 py-3 min-w-[280px]">
              <span className="text-sm text-muted-foreground">Search</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="competitor or product"
                className="flex-1 bg-transparent outline-none text-sm"
              />
            </label>
          </div>

          <div className="kpi-grid">
            <KpiCard label="Tracked Competitors" value={trackedCompetitors} hint="Groups seen in the catalog feed" />
            <KpiCard label="Total Listings" value={totalProducts} hint="All competitor catalog rows loaded" />
            <KpiCard label="Scout Discoveries" value={discoveredCompetitors} hint="Competitors not in onboarding setup" />
            <KpiCard label="In-Stock Rate" value={`${inStockRate.toFixed(0)}%`} hint="Share of listings currently in stock" />
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Panel title="Known Competitors" subtitle="Competitors you provided during onboarding">
          <div className="flex flex-wrap gap-2">
            {knownCompetitors.length > 0 ? (
              knownCompetitors.map((name) => (
                <span key={name} className="px-3 py-1 bg-white text-black border border-border font-medium rounded-full text-sm">
                  {name}
                </span>
              ))
            ) : (
              <span className="text-sm text-muted-foreground">None set up</span>
            )}
          </div>
        </Panel>

        <Panel title="Scout Discoveries" subtitle="New competitors found in the catalog feed">
          <div className="flex flex-wrap gap-2">
            {scoutDiscoveries.length > 0 ? (
              scoutDiscoveries.map((name) => (
                <span key={name} className="px-3 py-1 bg-primary text-primary-foreground font-medium rounded-full text-sm">
                  {name}
                </span>
              ))
            ) : (
              <span className="text-sm text-muted-foreground">No new discoveries yet</span>
            )}
          </div>
        </Panel>

        <Panel title="Catalog Pricing" subtitle="Quick pricing benchmark across competitor listings">
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Average price</span>
              <strong>{asCurrency(averageCatalogPrice)}</strong>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Known competitors</span>
              <strong>{knownCompetitors.length}</strong>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Discovered competitors</span>
              <strong>{discoveredCompetitors}</strong>
            </div>
          </div>
        </Panel>
      </div>

      <Panel
        title="Competitor Catalog Listings"
        subtitle="Grouped listings with stock status, SKU mapping, and per-competitor coverage"
        rightSlot={<span className="text-sm text-muted-foreground">{filteredSummaries.length} competitors shown</span>}
      >
        {filteredSummaries.length === 0 ? (
          <div className="text-center py-12 bg-card rounded-2xl border border-border text-muted-foreground">
            No competitor product listings matched your search.
          </div>
        ) : (
          <div className="space-y-6">
            {filteredSummaries.map((summary) => (
              <div key={summary.name} className="bg-background rounded-2xl border border-border overflow-hidden">
                <div className="bg-card p-4 border-b border-border flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                  <div className="flex items-center gap-3 flex-wrap">
                    <h3 className="font-semibold text-lg">{summary.name}</h3>
                    {!summary.isKnown && (
                      <span className="text-xs px-2 py-0.5 bg-blue-100 text-blue-800 font-medium rounded-full">
                        Scout Discovered
                      </span>
                    )}
                    <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-secondary-foreground">
                      {summary.productCount} products
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span className="px-2 py-1 rounded-full bg-white border border-border">In stock: {summary.inStockCount}</span>
                    <span className="px-2 py-1 rounded-full bg-white border border-border">Out of stock: {summary.outOfStockCount}</span>
                    <span className="px-2 py-1 rounded-full bg-white border border-border">Avg: {asCurrency(summary.averagePrice)}</span>
                    <span className="px-2 py-1 rounded-full bg-white border border-border">
                      Range: {asCurrency(summary.minPrice)} - {asCurrency(summary.maxPrice)}
                    </span>
                  </div>
                </div>

                <div className="px-4 py-4 overflow-x-auto">
                  <table className="w-full text-left border-collapse min-w-[760px]">
                    <thead>
                      <tr className="border-b border-border text-muted-foreground">
                        <th className="pb-3 font-semibold w-2/5">Product Listing</th>
                        <th className="pb-3 font-semibold">Mapped SKU</th>
                        <th className="pb-3 font-semibold text-right">Price (INR)</th>
                        <th className="pb-3 font-semibold text-center">Status</th>
                        <th className="pb-3 font-semibold text-right">Last Seen</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.items.map((item) => (
                        <tr
                          key={`${summary.name}-${item.catalog_sku || item.product_name || item.last_seen_at || Math.random()}`}
                          className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors"
                        >
                          <td className="py-3 pr-4">
                            <div className="font-medium text-sm line-clamp-2">{item.product_name || 'Untitled product'}</div>
                          </td>
                          <td className="py-3">
                            {item.catalog_sku ? (
                              <span className="text-xs px-2 py-1 bg-secondary text-secondary-foreground font-medium rounded-md font-mono">
                                {item.catalog_sku}
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground italic">Unmapped</span>
                            )}
                          </td>
                          <td className="py-3 text-right font-medium">{asCurrency(item.price)}</td>
                          <td className="py-3 text-center">
                            {item.in_stock ? (
                              <span className="text-xs px-2 py-1 bg-green-100 text-green-800 font-medium rounded-full">In Stock</span>
                            ) : (
                              <span className="text-xs px-2 py-1 bg-red-100 text-red-800 font-medium rounded-full">Out of Stock</span>
                            )}
                            {Number(item.times_out_of_stock || 0) > 1 && (
                              <div className="text-[10px] text-muted-foreground mt-1">OOS {Number(item.times_out_of_stock || 0)}x</div>
                            )}
                          </td>
                          <td className="py-3 text-right text-sm text-muted-foreground">
                            {item.last_seen_at ? new Date(item.last_seen_at).toLocaleDateString() : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

export default CompetitorsPage;
