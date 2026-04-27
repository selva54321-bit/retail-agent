import type { PropsWithChildren } from 'react';

export type ViewKey =
  | 'overview'
  | 'intake'
  | 'cycles'
  | 'recommendations'
  | 'intelligence'
  | 'retailers'
  | 'health';

interface AppShellProps extends PropsWithChildren {
  activeView: ViewKey;
  onChangeView: (view: ViewKey) => void;
  apiBase: string;
  onChangeApiBase: (value: string) => void;
  retailerId: number;
  onChangeRetailerId: (value: number) => void;
  cycleId: string;
  onChangeCycleId: (value: string) => void;
  onRefresh: () => void;
}

const NAV_ITEMS: Array<{ key: ViewKey; label: string; desc: string }> = [
  { key: 'overview', label: 'Overview', desc: 'Cycle dashboard and charts' },
  { key: 'intake', label: 'Intake + Run', desc: 'Form and chat onboarding' },
  { key: 'cycles', label: 'Cycles', desc: 'Run and inspect cycle history' },
  { key: 'recommendations', label: 'Recommendations', desc: 'Approve and track pricing' },
  { key: 'intelligence', label: 'Intelligence', desc: 'Competitor behavior and demand' },
  { key: 'retailers', label: 'Retailers', desc: 'Profiles and catalog records' },
  { key: 'health', label: 'Health', desc: 'Service readiness checks' },
];

export function AppShell({
  activeView,
  onChangeView,
  apiBase,
  onChangeApiBase,
  retailerId,
  onChangeRetailerId,
  cycleId,
  onChangeCycleId,
  onRefresh,
  children,
}: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar fade-in">
        <div className="brand">
          <p className="brand-kicker">RetailAgent</p>
          <h1>Market Command</h1>
          <p className="brand-text">White / black / gray control room for every backend route.</p>
        </div>

        <nav className="nav-list">
          {NAV_ITEMS.map((item, index) => (
            <button
              type="button"
              key={item.key}
              className={`nav-item ${activeView === item.key ? 'active' : ''}`}
              onClick={() => onChangeView(item.key)}
              style={{ animationDelay: `${index * 50}ms` }}
            >
              <span>{item.label}</span>
              <small>{item.desc}</small>
            </button>
          ))}
        </nav>
      </aside>

      <main className="workspace">
        <header className="topbar fade-in">
          <label>
            API Base URL
            <input
              value={apiBase}
              onChange={(event) => onChangeApiBase(event.target.value)}
              placeholder="http://localhost:8000"
            />
          </label>

          <label>
            Active Retailer ID
            <input
              type="number"
              value={retailerId}
              onChange={(event) => onChangeRetailerId(Number(event.target.value || 0))}
              min={0}
            />
          </label>

          <label>
            Active Cycle ID
            <input
              value={cycleId}
              onChange={(event) => onChangeCycleId(event.target.value)}
              placeholder="cycle-..."
            />
          </label>

          <button type="button" className="secondary-btn" onClick={onRefresh}>
            Refresh Data
          </button>
        </header>

        <section className="content">{children}</section>
      </main>
    </div>
  );
}
