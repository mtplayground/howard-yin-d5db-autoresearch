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

