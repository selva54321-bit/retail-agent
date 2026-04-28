import { useMemo, useState, type ReactElement } from 'react';

import { AppShell, type ViewKey } from './components/AppShell';
import { ApiClient, defaultApiBase } from './lib/api';
import { CyclesPage } from './pages/CyclesPage';
import { HealthPage } from './pages/HealthPage';
import { IntakePage } from './pages/IntakePage';
import { IntelligencePage } from './pages/IntelligencePage';
import { CompetitorsPage } from './pages/CompetitorsPage';
import { OverviewPage } from './pages/OverviewPage';
import { RecommendationsPage } from './pages/RecommendationsPage';
import { RetailersPage } from './pages/RetailersPage';
import type { RunCycleResponse } from './types/api';

function App() {
  const [activeView, setActiveView] = useState<ViewKey>('overview');
  const [apiBase, setApiBase] = useState(defaultApiBase);
  const [retailerId, setRetailerId] = useState(1);
  const [cycleId, setCycleId] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  const api = useMemo(() => new ApiClient(apiBase), [apiBase]);

  function triggerRefresh() {
    setRefreshKey((prev) => prev + 1);
  }

  function handleRetailerChange(nextId: number) {
    setRetailerId(nextId);
    setCycleId('');
    triggerRefresh();
  }

  function handleCycleCreated(result: RunCycleResponse) {
    setRetailerId(result.retailer_id);
    setCycleId(result.cycle_id);
    setActiveView('overview');
    triggerRefresh();
  }

  let page: ReactElement;
  switch (activeView) {
    case 'intake':
      page = <IntakePage api={api} retailerId={retailerId} onCycleCreated={handleCycleCreated} />;
      break;
    case 'competitors':
      page = <CompetitorsPage api={api} retailerId={retailerId} />;
      break;
    case 'cycles':
      page = (
        <CyclesPage
          api={api}
          retailerId={retailerId}
          onCycleCreated={handleCycleCreated}
          onCycleChange={setCycleId}
        />
      );
      break;
    case 'recommendations':
      page = (
        <RecommendationsPage
          api={api}
          retailerId={retailerId}
          cycleId={cycleId}
          onCycleChange={setCycleId}
          refreshKey={refreshKey}
        />
      );
      break;
    case 'intelligence':
      page = <IntelligencePage api={api} retailerId={retailerId} refreshKey={refreshKey} />;
      break;
    case 'retailers':
      page = (
        <RetailersPage
          api={api}
          retailerId={retailerId}
          onRetailerChange={handleRetailerChange}
          refreshKey={refreshKey}
        />
      );
      break;
    case 'health':
      page = <HealthPage api={api} refreshKey={refreshKey} />;
      break;
    case 'overview':
    default:
      page = (
        <OverviewPage
          api={api}
          retailerId={retailerId}
          cycleId={cycleId}
          onCycleChange={setCycleId}
          refreshKey={refreshKey}
        />
      );
  }

  return (
    <AppShell
      activeView={activeView}
      onChangeView={setActiveView}
      apiBase={apiBase}
      onChangeApiBase={setApiBase}
      retailerId={retailerId}
      onChangeRetailerId={handleRetailerChange}
      cycleId={cycleId}
      onChangeCycleId={setCycleId}
      onRefresh={triggerRefresh}
    >
      {page}
    </AppShell>
  );
}

export default App;
