export type Primitive = string | number | boolean | null;

export type JsonValue = Primitive | JsonValue[] | { [key: string]: JsonValue };

export interface HealthResponse {
  status: string;
  details: Record<string, unknown>;
}

export interface RetailerListItem {
  id: number;
  store_name: string;
  updated_at?: string | null;
}

export interface CatalogItemPayload {
  name: string;
  sku: string;
  current_price: number;
  cost: number;
}

export interface RetailerProfilePayload {
  store_name: string;
  category: string;
  subcategories: string[];
  location: string;
  brand_positioning: string;
  known_competitors: string[];
  pricing_strategy: string;
  cost_margin_floor: number;
  max_price_shift_pct: number;
  auto_apply_prices: boolean;
  alert_threshold_pct: number;
  scan_frequency: string;
  catalog: CatalogItemPayload[];
  onboarding_complete: boolean;
}

export interface RunCycleResponse {
  retailer_id: number;
  cycle_id: string;
  provider: string;
  model: string;
  summary: Record<string, number>;
  final_state: Record<string, unknown>;
}

export interface CycleLogResponse {
  retailer_id: number;
  cycles: Array<Record<string, unknown>>;
}

export interface Recommendation {
  retailer_id: number;
  cycle_id: string;
  retailer_sku: string;
  product_name: string;
  current_price: number;
  recommended_price: number;
  price_change: number;
  price_change_pct: number;
  action: string;
  confidence: number;
  reasoning: string;
  guardrail_applied: number;
  guardrail_note: string;
  approved: boolean | null;
  created_at: string;
}

export interface RecommendationListResponse {
  retailer_id: number;
  recommendations: Recommendation[];
}

export interface RecommendationApprovalDecision {
  retailer_sku: string;
  approved: boolean;
}

export interface RecommendationApprovalResponse {
  modified_count: number;
}

export interface MarketIntelligenceResponse {
  retailer_id: number;
  items: Array<Record<string, unknown>>;
}

export interface DropPatternResponse {
  retailer_id: number;
  patterns: Array<Record<string, unknown>>;
}

export interface CompetitorCatalogResponse {
  retailer_id: number;
  competitor_name?: string | null;
  items: Array<Record<string, unknown>>;
}

export interface DemandForecastResponse {
  retailer_id: number;
  forecasts: Array<Record<string, unknown>>;
}

export interface DashboardAlert {
  severity: string;
  message: string;
  source: string;
}

export interface DashboardReportResponse {
  retailer_id: number;
  cycle_id: string;
  cycle_log: Record<string, unknown>;
  analytics: Array<Record<string, unknown>>;
  recommendations: Recommendation[];
  market_intelligence: Array<Record<string, unknown>>;
  drop_patterns: Array<Record<string, unknown>>;
  competitor_catalog: Array<Record<string, unknown>>;
  alerts: DashboardAlert[];
  briefing: string;
}

export interface IntakeFormRunRequest {
  retailer_id: number;
  stream: boolean;
  provider?: string;
  profile: RetailerProfilePayload;
}

export interface IntakeChatMessage {
  role: string;
  content: string;
}

export interface IntakeChatRunRequest {
  retailer_id: number;
  stream: boolean;
  provider?: string;
  transcript?: string;
  messages?: IntakeChatMessage[];
}

export interface RunCycleRequest {
  retailer_id: number;
  stream: boolean;
  provider?: string;
  use_demo_profile?: boolean;
  profile?: RetailerProfilePayload;
}
