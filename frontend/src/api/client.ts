import type {
  AppConfigResponse,
  HealthResponse,
  IdeaListQuery,
  IdeaListResponse,
  ModelSettingsResponse,
  ModelSettingsUpdate,
  SessionResponse,
} from '../types/api';

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new ApiError(`请求失败: ${response.status}`, response.status);
  }

  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(`请求失败: ${response.status}`, response.status);
  }

  return response.json() as Promise<T>;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(`请求失败: ${response.status}`, response.status);
  }

  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>('/api/health');
}

export function getAppConfig(): Promise<AppConfigResponse> {
  return getJson<AppConfigResponse>('/api/config');
}

export function getSession(): Promise<SessionResponse> {
  return getJson<SessionResponse>('/api/auth/session');
}

export function login(passphrase: string): Promise<SessionResponse> {
  return postJson<SessionResponse>('/api/auth/login', { passphrase });
}

export function logout(): Promise<SessionResponse> {
  return postJson<SessionResponse>('/api/auth/logout');
}

export function getModelSettings(): Promise<ModelSettingsResponse> {
  return getJson<ModelSettingsResponse>('/api/settings/model');
}

export function updateModelSettings(payload: ModelSettingsUpdate): Promise<ModelSettingsResponse> {
  return putJson<ModelSettingsResponse>('/api/settings/model', payload);
}

export function getIdeas(query: IdeaListQuery = {}): Promise<IdeaListResponse> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value));
    }
  }
  const suffix = params.toString();
  return getJson<IdeaListResponse>(suffix ? `/api/ideas?${suffix}` : '/api/ideas');
}
