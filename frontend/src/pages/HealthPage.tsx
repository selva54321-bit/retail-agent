import { useEffect, useState } from 'react';

import { KpiCard } from '../components/KpiCard';
import { Panel } from '../components/Panel';
import { ApiClient } from '../lib/api';
import type { HealthResponse } from '../types/api';

interface HealthPageProps {
  api: ApiClient;
  refreshKey: number;
}



export function HealthPage({ api, refreshKey }: HealthPageProps) {
  const [live, setLive] = useState<HealthResponse | null>(null);
  const [ready, setReady] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function loadHealth() {
    setLoading(true);
    setError('');
    try {
      const [liveRes, readyRes] = await Promise.all([api.healthLive(), api.healthReady()]);
      setLive(liveRes);
      setReady(readyRes);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch health endpoints');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadHealth();
  }, [api, refreshKey]);

  return (
    <div className="page-grid">
      <Panel title="Service Health" subtitle="Live and readiness checks from backend">
        <div className="action-row">
          <button type="button" className="secondary-btn" onClick={loadHealth}>
            Refresh Health
          </button>
        </div>

        {loading ? <p>Checking health...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}

        <div className="kpi-grid">
          <KpiCard label="Live Status" value={live?.status || '-'} />
          <KpiCard label="Ready Status" value={ready?.status || '-'} />
          <KpiCard label="Live Details" value={Object.keys(live?.details || {}).length} hint="keys" />
          <KpiCard label="Ready Details" value={Object.keys(ready?.details || {}).length} hint="keys" />
        </div>
      </Panel>

      <Panel title="/health/live" subtitle="Raw payload">
        <pre>{JSON.stringify(live, null, 2)}</pre>
      </Panel>

      <Panel title="/health/ready" subtitle="Raw payload">
        <pre>{JSON.stringify(ready, null, 2)}</pre>
      </Panel>
    </div>
  );
}
