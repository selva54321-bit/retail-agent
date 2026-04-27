import { useMemo, useState } from 'react';

import { Panel } from '../components/Panel';
import { ApiClient } from '../lib/api';
import type { IntakeChatRunRequest, IntakeFormRunRequest, RetailerProfilePayload, RunCycleResponse } from '../types/api';

interface IntakePageProps {
  api: ApiClient;
  retailerId: number;
  onCycleCreated: (result: RunCycleResponse) => void;
}

const DEFAULT_PROFILE: RetailerProfilePayload = {
  store_name: 'The TV Shop Coimbatore',
  category: 'televisions',
  subcategories: ['LED TV', 'OLED TV', 'Smart TV', '4K TV'],
  location: 'Coimbatore, Tamil Nadu',
  brand_positioning: 'specialist_retailer',
  known_competitors: ['Amazon India', 'Flipkart', 'Poorvika', 'Croma'],
  pricing_strategy: 'competitive_parity',
  cost_margin_floor: 0.12,
  max_price_shift_pct: 0.15,
  auto_apply_prices: false,
  alert_threshold_pct: 0.05,
  scan_frequency: 'daily',
  onboarding_complete: true,
  catalog: [
    {
      name: 'LG 81.28 cm 32 inch Full HD LED Smart WebOS TV',
      sku: '32LQ570BPSA',
      current_price: 17912,
      cost: 14375,
    },
  ],
};

export function IntakePage({ api, retailerId, onCycleCreated }: IntakePageProps) {
  const [mode, setMode] = useState<'form' | 'chat'>('form');
  const [profile, setProfile] = useState<RetailerProfilePayload>(DEFAULT_PROFILE);
  const [provider, setProvider] = useState('gemini');
  const [chatTranscript, setChatTranscript] = useState(
    'Retailer: My store name is The TV Shop Coimbatore and I sell televisions.\nRetailer: I am located in Coimbatore, Tamil Nadu.\nRetailer: Our strategy is competitive parity with daily scans.',
  );
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RunCycleResponse | null>(null);
  const [error, setError] = useState('');

  const competitorText = useMemo(() => profile.known_competitors.join(', '), [profile.known_competitors]);
  const subcategoryText = useMemo(() => profile.subcategories.join(', '), [profile.subcategories]);

  function updateProfile<K extends keyof RetailerProfilePayload>(key: K, value: RetailerProfilePayload[K]) {
    setProfile((prev) => ({ ...prev, [key]: value }));
  }

  function updateCatalogRow(index: number, key: 'name' | 'sku' | 'current_price' | 'cost', value: string) {
    setProfile((prev) => {
      const next = [...prev.catalog];
      const row = { ...next[index] };
      if (key === 'current_price' || key === 'cost') {
        row[key] = Number(value || 0);
      } else {
        row[key] = value;
      }
      next[index] = row;
      return { ...prev, catalog: next };
    });
  }

  function addCatalogRow() {
    setProfile((prev) => ({
      ...prev,
      catalog: [...prev.catalog, { name: '', sku: '', current_price: 0, cost: 0 }],
    }));
  }

  function removeCatalogRow(index: number) {
    setProfile((prev) => ({
      ...prev,
      catalog: prev.catalog.filter((_, idx) => idx !== index),
    }));
  }

  async function submitFormIntake() {
    const payload: IntakeFormRunRequest = {
      retailer_id: retailerId,
      stream: false,
      provider,
      profile: {
        ...profile,
        known_competitors: competitorText
          .split(',')
          .map((v) => v.trim())
          .filter(Boolean),
        subcategories: subcategoryText
          .split(',')
          .map((v) => v.trim())
          .filter(Boolean),
      },
    };

    setLoading(true);
    setError('');
    try {
      const res = await api.intakeFormRun(payload);
      setResult(res);
      onCycleCreated(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to run form intake');
    } finally {
      setLoading(false);
    }
  }

  async function submitChatIntake() {
    const payload: IntakeChatRunRequest = {
      retailer_id: retailerId,
      stream: false,
      provider,
      transcript: chatTranscript,
    };

    setLoading(true);
    setError('');
    try {
      const res = await api.intakeChatRun(payload);
      setResult(res);
      onCycleCreated(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to run chat intake');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page-grid">
      <Panel title="Intake Mode" subtitle="Choose how you collect retailer details">
        <div className="segmented">
          <button type="button" className={mode === 'form' ? 'active' : ''} onClick={() => setMode('form')}>
            Fill Options
          </button>
          <button type="button" className={mode === 'chat' ? 'active' : ''} onClick={() => setMode('chat')}>
            Chat Transcript
          </button>
        </div>
      </Panel>

      <Panel title="Provider" subtitle="Model backend used for this run">
        <div className="inline-fields">
          <label>
            Provider
            <select value={provider} onChange={(event) => setProvider(event.target.value)} className="field">
              <option value="gemini">gemini</option>
              <option value="ollama">ollama</option>
              <option value="grok">grok</option>
            </select>
          </label>
          <label>
            Incoming Retailer ID
            <input value={retailerId} readOnly className="field" />
          </label>
        </div>
      </Panel>

      {mode === 'form' ? (
        <Panel title="Form Intake + Run" subtitle="Structured onboarding with direct fields">
          <div className="form-grid">
            <label>
              Store Name
              <input
                className="field"
                value={profile.store_name}
                onChange={(event) => updateProfile('store_name', event.target.value)}
              />
            </label>
            <label>
              Category
              <input
                className="field"
                value={profile.category}
                onChange={(event) => updateProfile('category', event.target.value)}
              />
            </label>
            <label>
              Location
              <input
                className="field"
                value={profile.location}
                onChange={(event) => updateProfile('location', event.target.value)}
              />
            </label>
            <label>
              Brand Positioning
              <input
                className="field"
                value={profile.brand_positioning}
                onChange={(event) => updateProfile('brand_positioning', event.target.value)}
              />
            </label>
            <label>
              Pricing Strategy
              <input
                className="field"
                value={profile.pricing_strategy}
                onChange={(event) => updateProfile('pricing_strategy', event.target.value)}
              />
            </label>
            <label>
              Scan Frequency
              <input
                className="field"
                value={profile.scan_frequency}
                onChange={(event) => updateProfile('scan_frequency', event.target.value)}
              />
            </label>
            <label>
              Subcategories (comma separated)
              <input
                className="field"
                value={subcategoryText}
                onChange={(event) => updateProfile('subcategories', event.target.value.split(',').map((v) => v.trim()))}
              />
            </label>
            <label>
              Competitors (comma separated)
              <input
                className="field"
                value={competitorText}
                onChange={(event) =>
                  updateProfile(
                    'known_competitors',
                    event.target.value.split(',').map((v) => v.trim()).filter(Boolean),
                  )
                }
              />
            </label>
            <label>
              Cost Margin Floor
              <input
                className="field"
                type="number"
                step="0.01"
                value={profile.cost_margin_floor}
                onChange={(event) => updateProfile('cost_margin_floor', Number(event.target.value || 0))}
              />
            </label>
            <label>
              Max Shift %
              <input
                className="field"
                type="number"
                step="0.01"
                value={profile.max_price_shift_pct}
                onChange={(event) => updateProfile('max_price_shift_pct', Number(event.target.value || 0))}
              />
            </label>
            <label>
              Alert Threshold %
              <input
                className="field"
                type="number"
                step="0.01"
                value={profile.alert_threshold_pct}
                onChange={(event) => updateProfile('alert_threshold_pct', Number(event.target.value || 0))}
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={profile.auto_apply_prices}
                onChange={(event) => updateProfile('auto_apply_prices', event.target.checked)}
              />
              Auto-apply prices
            </label>
          </div>

          <div className="catalog-head">
            <h4>Catalog</h4>
            <button type="button" onClick={addCatalogRow} className="secondary-btn">
              + Add SKU
            </button>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>SKU</th>
                  <th>Current</th>
                  <th>Cost</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {profile.catalog.map((item, index) => (
                  <tr key={`${item.sku}-${index}`}>
                    <td>
                      <input
                        className="field"
                        value={item.name}
                        onChange={(event) => updateCatalogRow(index, 'name', event.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="field"
                        value={item.sku}
                        onChange={(event) => updateCatalogRow(index, 'sku', event.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="field"
                        type="number"
                        value={item.current_price}
                        onChange={(event) => updateCatalogRow(index, 'current_price', event.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        className="field"
                        type="number"
                        value={item.cost}
                        onChange={(event) => updateCatalogRow(index, 'cost', event.target.value)}
                      />
                    </td>
                    <td>
                      <button type="button" onClick={() => removeCatalogRow(index)} className="ghost-btn">
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="action-row">
            <button type="button" className="primary-btn" disabled={loading} onClick={submitFormIntake}>
              {loading ? 'Running...' : 'Run Intake (Form)'}
            </button>
          </div>
        </Panel>
      ) : (
        <Panel title="Chat Intake + Run" subtitle="Paste conversation transcript and auto-extract profile">
          <label>
            Transcript
            <textarea
              className="field area"
              value={chatTranscript}
              onChange={(event) => setChatTranscript(event.target.value)}
            />
          </label>
          <div className="action-row">
            <button type="button" className="primary-btn" disabled={loading} onClick={submitChatIntake}>
              {loading ? 'Running...' : 'Run Intake (Chat)'}
            </button>
          </div>
        </Panel>
      )}

      <Panel title="Result" subtitle="Cycle run output from intake routes">
        {error ? <p className="error-text">{error}</p> : null}
        {!error && result ? (
          <div className="result-grid">
            <p>
              <strong>Retailer ID:</strong> {result.retailer_id}
            </p>
            <p>
              <strong>Cycle ID:</strong> {result.cycle_id}
            </p>
            <p>
              <strong>Provider/Model:</strong> {result.provider} / {result.model}
            </p>
            <pre>{JSON.stringify(result.summary, null, 2)}</pre>
          </div>
        ) : (
          <p>Run form or chat intake to populate this panel.</p>
        )}
      </Panel>
    </div>
  );
}
