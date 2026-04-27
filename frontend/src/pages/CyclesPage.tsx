import { useEffect, useState } from 'react';

import { Panel } from '../components/Panel';
import { ApiClient } from '../lib/api';
import { asDate } from '../lib/format';
import type { RunCycleResponse } from '../types/api';

interface CyclesPageProps {
  api: ApiClient;
  retailerId: number;
  onCycleCreated: (result: RunCycleResponse) => void;
  onCycleChange: (cycleId: string) => void;
}

export function CyclesPage({ api, retailerId, onCycleCreated, onCycleChange }: CyclesPageProps) {
  const [provider, setProvider] = useState('gemini');
  const [useDemoProfile, setUseDemoProfile] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [cycles, setCycles] = useState<Array<Record<string, unknown>>>([]);

  async function loadCycles() {
    if (retailerId <= 0) {
      setCycles([]);
      return;
    }
    try {
      const res = await api.getCycles(retailerId, 25);
      setCycles(res.cycles || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load cycles');
    }
  }

  useEffect(() => {
    void loadCycles();
  }, [retailerId]);

  async function runCycleNow() {
    setLoading(true);
    setError('');
    try {
      const result = await api.runCycle({
        retailer_id: retailerId,
        stream: false,
        provider,
        use_demo_profile: useDemoProfile,
      });
      onCycleCreated(result);
      onCycleChange(result.cycle_id);
      await loadCycles();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to run cycle');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page-grid">
      <Panel title="Run Cycle" subtitle="Trigger full multi-agent cycle directly">
        <div className="inline-fields">
          <label>
            Provider
            <select value={provider} onChange={(event) => setProvider(event.target.value)} className="field">
              <option value="gemini">gemini</option>
              <option value="ollama">ollama</option>
              <option value="grok">grok</option>
            </select>
          </label>

          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={useDemoProfile}
              onChange={(event) => setUseDemoProfile(event.target.checked)}
            />
            Use demo profile
          </label>
        </div>

        <div className="action-row">
          <button type="button" className="primary-btn" disabled={loading} onClick={runCycleNow}>
            {loading ? 'Running...' : 'Run Cycle'}
          </button>
          <button type="button" className="secondary-btn" onClick={loadCycles}>
            Refresh History
          </button>
        </div>
        {error ? <p className="error-text">{error}</p> : null}
      </Panel>

      <Panel title="Cycle History" subtitle="Recent runs for selected retailer">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Cycle ID</th>
                <th>Status</th>
                <th>Started</th>
                <th>Scraped</th>
                <th>Matches</th>
                <th>Recommendations</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {cycles.map((cycle) => (
                <tr key={String(cycle.cycle_id || Math.random())}>
                  <td>{String(cycle.cycle_id || '')}</td>
                  <td>{String(cycle.status || '-')}</td>
                  <td>{asDate(cycle.started_at)}</td>
                  <td>{Number(cycle.records_scraped || 0)}</td>
                  <td>{Number(cycle.matches_found || 0)}</td>
                  <td>{Number(cycle.recommendations_made || 0)}</td>
                  <td>
                    <button
                      type="button"
                      className="ghost-btn"
                      onClick={() => onCycleChange(String(cycle.cycle_id || ''))}
                    >
                      Open
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {cycles.length === 0 ? <p>No cycles found for this retailer.</p> : null}
        </div>
      </Panel>
    </div>
  );
}
