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

export type IdeaSort = 'created_desc' | 'created_asc' | 'score_desc' | 'score_asc' | 'title_asc';

export interface IdeaResponse {
  id: string;
  title: string;
  problem_statement: string | null;
  hypothesis: string | null;
  status: string;
  score: number | null;
  rationale: string | null;
  source_context: {
    knowledge_item_ids?: string[];
    knowledge_items?: Array<{
      id: string;
      title: string;
      source: string;
      url: string;
      code_repository_url?: string | null;
    }>;
    related_work?: string[];
    [key: string]: unknown;
  };
  extra: {
    feasibility?: string;
    reusable_points?: string[];
    [key: string]: unknown;
  };
  created_at: string;
  updated_at: string;
}

export interface IdeaListResponse {
  items: IdeaResponse[];
  total: number;
  limit: number;
  offset: number;
  sort: IdeaSort;
}

export interface IdeaListQuery {
  topic?: string;
  status?: string;
  min_score?: number;
  sort?: IdeaSort;
  limit?: number;
  offset?: number;
}

export interface IdeaRefineRequest {
  message: string;
}

export interface IdeaRefineResponse {
  idea: IdeaResponse;
  assistant_message: string;
}

export type ProgressEventType = 'connected' | 'progress' | 'log' | 'artifact' | 'heartbeat';

export interface ProgressEvent {
  id: string;
  type: ProgressEventType;
  message: string;
  run_id: string | null;
  stage: string | null;
  artifact_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}
