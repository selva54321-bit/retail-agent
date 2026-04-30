import { useEffect, useMemo, useState } from 'react';

import { Panel } from '../components/Panel';
import { ApiClient } from '../lib/api';
import { asCurrency, asPercent, asDate, cycleLabel } from '../lib/format';
import type { Recommendation } from '../types/api';

interface RecommendationsPageProps {
  api: ApiClient;
  retailerId: number;
  cycleId: string;
  onCycleChange: (cycleId: string) => void;
  refreshKey: number;
}

export function RecommendationsPage({
  api,
  retailerId,
  cycleId,
  onCycleChange,
  refreshKey,
}: RecommendationsPageProps) {
  const [pendingOnly, setPendingOnly] = useState(false);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [decisionMap, setDecisionMap] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [resultMessage, setResultMessage] = useState('');
  const [cycles, setCycles] = useState<Array<Record<string, unknown>>>([]);

  const availableCycles = useMemo(
    () => Array.from(new Set(recommendations.map((r) => r.cycle_id))).filter(Boolean),
    [recommendations],
  );

  const cycleStartedAt = useMemo(() => {
    const byId = new Map<string, unknown>();
    for (const cycle of cycles) {
      byId.set(String(cycle.cycle_id || ''), cycle.started_at);
    }
    return byId;
  }, [cycles]);

  const activeCycle = cycleId || availableCycles[0] || '';

  const visibleRows = useMemo(
    () => recommendations.filter((r) => (activeCycle ? r.cycle_id === activeCycle : true)),
    [recommendations, activeCycle],
  );

  useEffect(() => {
    if (retailerId <= 0) {
      setRecommendations([]);
      return;
    }

    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const res = await api.getRecommendations(retailerId, pendingOnly, 200);
        const cycleRes = await api.getCycles(retailerId, 200);
        if (cancelled) return;
        setRecommendations(res.recommendations || []);
        setCycles(cycleRes.cycles || []);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load recommendations');
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
  }, [api, retailerId, pendingOnly, refreshKey]);

  useEffect(() => {
    const defaults: Record<string, boolean> = {};
    for (const row of visibleRows) {
      defaults[row.retailer_sku] = row.approved === null ? true : Boolean(row.approved);
    }
    setDecisionMap(defaults);
  }, [activeCycle, visibleRows]);

  async function submitApprovals() {
    if (!activeCycle) {
      setError('Select a cycle first.');
      return;
    }

    const decisions = visibleRows.map((row) => ({
      retailer_sku: row.retailer_sku,
      approved: decisionMap[row.retailer_sku] ?? true,
    }));

    try {
      setError('');
      const res = await api.approveRecommendations(retailerId, activeCycle, decisions);
      setResultMessage(`Updated ${res.modified_count} recommendation approval(s).`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to submit approvals');
    }
  }

  return (
    <div className="page-grid">
      <Panel title="Recommendations" subtitle="Review generated pricing actions and apply approvals">
        <div className="inline-fields">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={pendingOnly}
              onChange={(event) => setPendingOnly(event.target.checked)}
            />
            Pending only
          </label>

          <label>
            Cycle
            <select
              className="field"
              value={activeCycle}
              onChange={(event) => onCycleChange(event.target.value)}
            >
              <option value="">All cycles</option>
              {availableCycles.map((id) => (
                <option key={id} value={id}>
                  {cycleLabel(id, cycleStartedAt.get(id))}
                </option>
              ))}
            </select>
          </label>

          <button type="button" className="secondary-btn" onClick={submitApprovals}>
            Submit Approvals
          </button>
        </div>

        {loading ? <p>Loading recommendations...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {resultMessage ? <p className="success-text">{resultMessage}</p> : null}

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Approve</th>
                <th>Cycle</th>
                <th>SKU</th>
                <th>Product</th>
                <th>Current</th>
                <th>Recommended</th>
                <th>Shift</th>
                <th>Action</th>
                <th>Confidence</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => (
                <tr key={`${row.cycle_id}-${row.retailer_sku}`}>
                  <td>
                    <input
                      type="checkbox"
                      checked={decisionMap[row.retailer_sku] ?? true}
                      onChange={(event) =>
                        setDecisionMap((prev) => ({ ...prev, [row.retailer_sku]: event.target.checked }))
                      }
                    />
                  </td>
                  <td>{cycleLabel(row.cycle_id, cycleStartedAt.get(row.cycle_id))}</td>
                  <td>{row.retailer_sku}</td>
                  <td>{row.product_name}</td>
                  <td>{asCurrency(row.current_price)}</td>
                  <td>{asCurrency(row.recommended_price)}</td>
                  <td>{asPercent(Number(row.price_change_pct) * 100)}</td>
                  <td>
                    <span className="badge neutral">{row.action}</span>
                  </td>
                  <td>{asPercent(Number(row.confidence) * 100, 0)}</td>
                  <td>{asDate(row.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && visibleRows.length === 0 ? <p>No recommendations found.</p> : null}
        </div>
      </Panel>
    </div>
  );
}
