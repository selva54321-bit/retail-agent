import { useEffect, useState, useRef } from 'react';
import * as XLSX from 'xlsx';

import { Panel } from '../components/Panel';
import { ApiClient } from '../lib/api';
import type { RetailerListItem, RetailerProfilePayload } from '../types/api';

interface RetailersPageProps {
  api: ApiClient;
  retailerId: number;
  onRetailerChange: (retailerId: number) => void;
  refreshKey: number;
}

const EMPTY_PROFILE: RetailerProfilePayload = {
  store_name: '',
  category: '',
  subcategories: [],
  location: '',
  brand_positioning: 'mid-market',
  known_competitors: [],
  pricing_strategy: 'competitive_parity',
  cost_margin_floor: 0.1,
  max_price_shift_pct: 0.15,
  auto_apply_prices: false,
  alert_threshold_pct: 0.05,
  scan_frequency: 'daily',
  catalog: [],
  onboarding_complete: true,
};

export function RetailersPage({ api, retailerId, onRetailerChange, refreshKey }: RetailersPageProps) {
  const [items, setItems] = useState<RetailerListItem[]>([]);
  const [profile, setProfile] = useState<RetailerProfilePayload>(EMPTY_PROFILE);
  const [selectedId, setSelectedId] = useState<number>(retailerId || 0);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingProfile, setLoadingProfile] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSelectedId(retailerId || 0);
  }, [retailerId]);

  async function loadRetailers() {
    setLoadingList(true);
    setError('');
    try {
      const list = await api.listRetailers();
      setItems(list || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load retailers');
    } finally {
      setLoadingList(false);
    }
  }

  useEffect(() => {
    void loadRetailers();
  }, [api, refreshKey]);

  function selectRetailer(nextId: number) {
    setSelectedId(nextId);
    onRetailerChange(nextId);
  }

  useEffect(() => {
    if (selectedId <= 0) {
      return;
    }

    let cancelled = false;
    async function loadProfile() {
      setLoadingProfile(true);
      setError('');
      try {
        const p = await api.getRetailer(selectedId);
        if (!cancelled) {
          setProfile({ ...EMPTY_PROFILE, ...p });
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load retailer profile');
        }
      } finally {
        if (!cancelled) {
          setLoadingProfile(false);
        }
      }
    }

    void loadProfile();
    return () => {
      cancelled = true;
    };
  }, [api, selectedId]);

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
    setProfile((prev) => ({ ...prev, catalog: prev.catalog.filter((_, idx) => idx !== index) }));
  }

  async function handleFileUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      const data = await file.arrayBuffer();
      const workbook = XLSX.read(data);
      const firstSheet = workbook.Sheets[workbook.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(firstSheet);

      const newCatalogItems = rows.map((row) => {
        const name = String(row.Name || row.name || row.Product || row.product_name || '');
        const sku = String(row.SKU || row.sku || row.catalog_sku || '');
        const currentPrice = Number(row['Current Price'] || row.current_price || row.Price || row.price || 0);
        const cost = Number(row.Cost || row.cost || 0);

        return { name, sku, current_price: currentPrice, cost };
      }).filter((item) => item.name || item.sku);

      if (newCatalogItems.length > 0) {
        setProfile((prev) => ({
          ...prev,
          catalog: [...prev.catalog, ...newCatalogItems],
        }));
        setMessage(`Imported ${newCatalogItems.length} SKUs from ${file.name}. Remember to save!`);
      } else {
        setError('No valid rows found in the Excel file.');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to parse Excel file');
    }

    event.target.value = '';
  }

  async function saveProfile() {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const payload: RetailerProfilePayload = {
        ...profile,
        subcategories: profile.subcategories.map((s) => s.trim()).filter(Boolean),
        known_competitors: profile.known_competitors.map((c) => c.trim()).filter(Boolean),
      };
      const res = await api.saveRetailer(payload);
      setMessage(`Saved retailer profile under ID ${res.retailer_id}`);
      onRetailerChange(res.retailer_id);
      setSelectedId(res.retailer_id);
      await loadRetailers();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save retailer profile');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page-grid two-col">
      <Panel title="Retailer Registry" subtitle="List and select retailer profiles">
        <div className="action-row">
          <button type="button" className="secondary-btn" onClick={loadRetailers}>
            Refresh
          </button>
          <button
            type="button"
            className="secondary-btn"
            onClick={() => {
              setSelectedId(0);
              setProfile(EMPTY_PROFILE);
            }}
          >
            New Profile
          </button>
        </div>
        {loadingList ? <p>Loading retailers...</p> : null}
        <div className="list-wrap">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`list-item button-list ${selectedId === item.id ? 'active' : ''}`}
              onClick={() => selectRetailer(item.id)}
            >
              <strong>{item.store_name}</strong>
              <span>ID: {item.id}</span>
              <span>{item.updated_at || '-'}</span>
            </button>
          ))}
          {items.length === 0 ? <p>No retailers available yet.</p> : null}
        </div>
      </Panel>

      <Panel title="Retailer Profile" subtitle="Create or update profile by store name">
        {loadingProfile ? <p>Loading profile...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {message ? <p className="success-text">{message}</p> : null}

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
              value={profile.subcategories.join(', ')}
              onChange={(event) =>
                updateProfile(
                  'subcategories',
                  event.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                )
              }
            />
          </label>
          <label>
            Competitors (comma separated)
            <input
              className="field"
              value={profile.known_competitors.join(', ')}
              onChange={(event) =>
                updateProfile(
                  'known_competitors',
                  event.target.value.split(',').map((s) => s.trim()).filter(Boolean),
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
          <div className="flex items-center gap-2">
            <input
              type="file"
              accept=".xlsx, .xls, .csv"
              style={{ display: 'none' }}
              ref={fileInputRef}
              onChange={(e) => void handleFileUpload(e)}
            />
            <button type="button" onClick={() => fileInputRef.current?.click()} className="secondary-btn">
              Upload XLSX
            </button>
            <button type="button" onClick={addCatalogRow} className="secondary-btn">
              + Add SKU
            </button>
          </div>
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
                    <button type="button" className="ghost-btn" onClick={() => removeCatalogRow(index)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="action-row">
          <button type="button" className="primary-btn" disabled={saving} onClick={saveProfile}>
            {saving ? 'Saving...' : 'Save Retailer Profile'}
          </button>
        </div>
      </Panel>
    </div>
  );
}
