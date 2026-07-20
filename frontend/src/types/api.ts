export interface HealthResponse {
  status: string;
  environment: string;
  database_configured: boolean;
}

export interface AppConfigResponse {
  environment: string;
  public_origin: string;
  model_provider: string;
}

export interface SessionResponse {
  authenticated: boolean;
}

export interface ModelSettingsResponse {
  provider: string;
  base_url: string | null;
  default_model: string;
  api_key_configured: boolean;
}

export interface ModelSettingsUpdate {
  provider?: string;
  base_url?: string | null;
  default_model?: string;
  api_key?: string;
  clear_api_key?: boolean;
}
