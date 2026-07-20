import type { AppConfigResponse, HealthResponse } from '../types/api';

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`请求失败: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>('/api/health');
}

export function getAppConfig(): Promise<AppConfigResponse> {
  return getJson<AppConfigResponse>('/api/config');
}

