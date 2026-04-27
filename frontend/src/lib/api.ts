import type {
  CompetitorCatalogResponse,
  CycleLogResponse,
  DashboardReportResponse,
  DemandForecastResponse,
  DropPatternResponse,
  HealthResponse,
  IntakeChatRunRequest,
  IntakeFormRunRequest,
  MarketIntelligenceResponse,
  RecommendationApprovalDecision,
  RecommendationApprovalResponse,
  RecommendationListResponse,
  RetailerListItem,
  RetailerProfilePayload,
  RunCycleRequest,
  RunCycleResponse,
} from '../types/api';

export class ApiClient {
  private readonly baseUrl: string;
  private readonly apiPrefix: string;

  constructor(baseUrl: string, apiPrefix = '/api/v1') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.apiPrefix = apiPrefix;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(init.headers || {}),
      },
      ...init,
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`API ${response.status}: ${text}`);
    }

    return (await response.json()) as T;
  }

  healthLive(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health/live');
  }

  healthReady(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health/ready');
  }

  listRetailers(): Promise<RetailerListItem[]> {
    return this.request<RetailerListItem[]>(`${this.apiPrefix}/retailers`);
  }

  getRetailer(retailerId: number): Promise<RetailerProfilePayload> {
    return this.request<RetailerProfilePayload>(`${this.apiPrefix}/retailers/${retailerId}`);
  }

  saveRetailer(profile: RetailerProfilePayload): Promise<{ retailer_id: number }> {
    return this.request<{ retailer_id: number }>(`${this.apiPrefix}/retailers`, {
      method: 'POST',
      body: JSON.stringify({ profile }),
    });
  }

  intakeFormRun(payload: IntakeFormRunRequest): Promise<RunCycleResponse> {
    return this.request<RunCycleResponse>(`${this.apiPrefix}/intake/form/run`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  intakeChatRun(payload: IntakeChatRunRequest): Promise<RunCycleResponse> {
    return this.request<RunCycleResponse>(`${this.apiPrefix}/intake/chat/run`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  runCycle(payload: RunCycleRequest): Promise<RunCycleResponse> {
    return this.request<RunCycleResponse>(`${this.apiPrefix}/cycles/run`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  getCycles(retailerId: number, limit = 10): Promise<CycleLogResponse> {
    return this.request<CycleLogResponse>(`${this.apiPrefix}/cycles/retailers/${retailerId}?limit=${limit}`);
  }

  getDashboardLatest(retailerId: number): Promise<DashboardReportResponse> {
    return this.request<DashboardReportResponse>(
      `${this.apiPrefix}/dashboard/retailers/${retailerId}/latest`,
    );
  }

  getDashboardCycle(retailerId: number, cycleId: string): Promise<DashboardReportResponse> {
    return this.request<DashboardReportResponse>(
      `${this.apiPrefix}/dashboard/retailers/${retailerId}/cycles/${cycleId}`,
    );
  }

  getRecommendations(retailerId: number, pendingOnly = false, limit = 50): Promise<RecommendationListResponse> {
    const pending = pendingOnly ? 'true' : 'false';
    return this.request<RecommendationListResponse>(
      `${this.apiPrefix}/recommendations/retailers/${retailerId}?pending_only=${pending}&limit=${limit}`,
    );
  }

  approveRecommendations(
    retailerId: number,
    cycleId: string,
    decisions: RecommendationApprovalDecision[],
  ): Promise<RecommendationApprovalResponse> {
    return this.request<RecommendationApprovalResponse>(
      `${this.apiPrefix}/recommendations/retailers/${retailerId}/cycles/${cycleId}/approvals`,
      {
        method: 'POST',
        body: JSON.stringify({ decisions }),
      },
    );
  }

  getMarketIntelligence(retailerId: number, limitPerCompetitor = 10): Promise<MarketIntelligenceResponse> {
    return this.request<MarketIntelligenceResponse>(
      `${this.apiPrefix}/intelligence/retailers/${retailerId}?limit_per_competitor=${limitPerCompetitor}`,
    );
  }

  getDropPatterns(retailerId: number): Promise<DropPatternResponse> {
    return this.request<DropPatternResponse>(`${this.apiPrefix}/intelligence/retailers/${retailerId}/drop-patterns`);
  }

  getCompetitorCatalog(retailerId: number): Promise<CompetitorCatalogResponse> {
    return this.request<CompetitorCatalogResponse>(`${this.apiPrefix}/intelligence/retailers/${retailerId}/catalog`);
  }

  getDemandForecasts(retailerId: number, limit = 50): Promise<DemandForecastResponse> {
    return this.request<DemandForecastResponse>(
      `${this.apiPrefix}/intelligence/retailers/${retailerId}/demand-forecasts?limit=${limit}`,
    );
  }
}

export const defaultApiBase =
  import.meta.env.VITE_API_BASE_URL?.toString() || 'http://localhost:8000';
