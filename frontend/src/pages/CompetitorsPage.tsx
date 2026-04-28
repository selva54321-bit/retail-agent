import { useEffect, useState } from 'react';
import { ApiClient } from '../lib/api';
import type { RetailerProfilePayload } from '../types/api';

interface CompetitorsPageProps {
  api: ApiClient;
  retailerId: number;
}

export function CompetitorsPage({ api, retailerId }: CompetitorsPageProps) {
  const [profile, setProfile] = useState<RetailerProfilePayload | null>(null);
  const [catalogItems, setCatalogItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadData = async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const [profileRes, catalogRes] = await Promise.all([
        api.getRetailer(retailerId),
        api.getCompetitorCatalog(retailerId)
      ]);
      setProfile(profileRes);
      setCatalogItems(catalogRes.items || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load competitor data');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [retailerId]);

  if (loading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-50 text-red-700 p-4 rounded-xl flex items-center">
          <span className="mr-3 font-semibold">Error</span>
          <span>{error}</span>
          <button onClick={() => loadData()} className="ml-auto underline font-medium">Retry</button>
        </div>
      </div>
    );
  }

  const knownCompetitors = profile?.known_competitors || [];
  
  // Group catalog items by competitor
  const competitorsDataMap = catalogItems.reduce((acc: any, item: any) => {
    const compName = item.competitor_name || 'Unknown Competitor';
    if (!acc[compName]) {
      acc[compName] = [];
    }
    acc[compName].push(item);
    return acc;
  }, {});

  const allFoundCompetitors = Object.keys(competitorsDataMap);
  const newlyDiscovered = allFoundCompetitors.filter(c => !knownCompetitors.includes(c));

  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto space-y-8 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row justify-between md:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Competitor Dashboard</h1>
          <p className="text-muted-foreground mt-1">Track known competitors and discover new market players actively tracked by Scout.</p>
        </div>
        <button
          onClick={() => loadData(true)}
          disabled={refreshing}
          className="flex-shrink-0 inline-flex items-center px-4 py-2 bg-secondary text-primary font-medium rounded-lg hover:bg-secondary/80 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary transition-colors disabled:opacity-50"
        >
          <span className="mr-2">{refreshing ? '...' : 'Reload'}</span>
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-card rounded-2xl p-6 border border-border">
          <div className="flex items-center gap-3 mb-4">
            <span className="text-xs px-2 py-1 rounded bg-secondary text-secondary-foreground">Known</span>
            <h2 className="text-xl font-semibold">Your Setup Competitors</h2>
          </div>
          <p className="text-sm text-muted-foreground mb-4">Competitors you provided during onboarding profile setup.</p>
          <div className="flex flex-wrap gap-2">
            {knownCompetitors.length > 0 ? knownCompetitors.map((name: string) => (
              <span key={name} className="px-3 py-1 bg-white text-black border border-border font-medium rounded-full text-sm">
                {name}
              </span>
            )) : <span className="text-sm text-muted-foreground">None setup</span>}
          </div>
        </div>

        <div className="bg-card rounded-2xl p-6 border border-border">
          <div className="flex items-center gap-3 mb-4">
            <span className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground">Scout</span>
            <h2 className="text-xl font-semibold">Scout Discoveries</h2>
          </div>
          <p className="text-sm text-muted-foreground mb-4">Newly found competitors actively selling matching catalog items.</p>
          <div className="flex flex-wrap gap-2">
            {newlyDiscovered.length > 0 ? newlyDiscovered.map((name: string) => (
              <span key={name} className="px-3 py-1 bg-primary text-primary-foreground font-medium rounded-full text-sm">
                {name}
              </span>
            )) : <span className="text-sm text-muted-foreground">No new discoveries yet</span>}
          </div>
        </div>
      </div>

      <div className="space-y-6">
        <h2 className="text-xl font-semibold tracking-tight">Competitor Catalog Listings</h2>
        {allFoundCompetitors.length === 0 && (
          <div className="text-center py-12 bg-card rounded-2xl border border-border text-muted-foreground">
            No competitor product listings mapped yet. Run a scout/scraper cycle!
          </div>
        )}
        {allFoundCompetitors.map((competitor) => {
          const items = competitorsDataMap[competitor];
          const isKnown = knownCompetitors.includes(competitor);
          return (
            <div key={competitor} className="bg-background rounded-2xl border border-border overflow-hidden">
              <div className="bg-card p-4 border-b border-border flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="font-semibold text-lg">{competitor}</h3>
                  {!isKnown && <span className="text-xs px-2 py-0.5 bg-blue-100 text-blue-800 font-medium rounded-full">Scout Discovered</span>}
                </div>
                <span className="text-sm font-medium text-muted-foreground">{items.length} products tracked</span>
              </div>
              <div className="px-4 py-4 overflow-x-auto">
                <table className="w-full text-left border-collapse min-w-[700px]">
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
                    {items.map((item: any, idx: number) => (
                      <tr key={idx} className="border-b border-border/50 last:border-0 hover:bg-muted/30 transition-colors">
                        <td className="py-3 pr-4">
                          <div className="font-medium text-sm line-clamp-2">{item.product_name}</div>
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
                        <td className="py-3 text-right font-medium">
                          ₹{item.price?.toLocaleString()}
                        </td>
                        <td className="py-3 text-center">
                          {item.in_stock ? (
                            <span className="text-xs px-2 py-1 bg-green-100 text-green-800 font-medium rounded-full">In Stock</span>
                          ) : (
                            <span className="text-xs px-2 py-1 bg-red-100 text-red-800 font-medium rounded-full">Out of Stock</span>
                          )}
                          {item.times_out_of_stock > 1 && (
                            <div className="text-[10px] text-muted-foreground mt-1">OOS {item.times_out_of_stock}x</div>
                          )}
                        </td>
                        <td className="py-3 text-right text-sm text-muted-foreground">
                          {new Date(item.last_seen_at).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default CompetitorsPage;
