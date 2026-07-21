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

export interface RunResponse {
  id: string;
  idea_id: string | null;
  status: string;
  trigger_source: string;
  current_stage: string | null;
  parameters: Record<string, unknown>;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunEventResponse {
  id: string;
  run_id: string;
  event_type: string;
  stage: string | null;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface MonitorArtifactResponse {
  id: string;
  kind: string;
  storage_key: string;
  filename: string | null;
  content_type: string | null;
  byte_size: number | null;
  checksum_sha256: string | null;
  extra: Record<string, unknown>;
  created_at: string;
}

export interface MonitorExperimentResponse {
  id: string;
  idea_id: string | null;
  title: string;
  hypothesis: string | null;
  status: string;
  metrics: {
    last_run?: {
      status?: string;
      numeric_results?: Record<string, number>;
      logs?: {
        stdout?: string;
        stderr?: string;
      };
      charts?: Array<{
        path?: string;
        byte_size?: number;
        content_type?: string;
        base64?: string;
      }>;
      persisted_artifacts?: Array<{
        id?: string;
        kind?: string;
        storage_key?: string;
        filename?: string | null;
        content_type?: string | null;
        byte_size?: number | null;
      }>;
    };
    [key: string]: unknown;
  };
  result_summary: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  artifacts: MonitorArtifactResponse[];
}

export interface RunMonitorResponse {
  run: RunResponse;
  events: RunEventResponse[];
  experiments: MonitorExperimentResponse[];
}

export interface IdeaConfirmResponse {
  idea: IdeaResponse;
  run: RunResponse;
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
