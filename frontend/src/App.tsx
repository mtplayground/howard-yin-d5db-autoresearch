import { useEffect, useState, type FormEvent } from 'react';

import {
  ApiError,
  getAppConfig,
  getHealth,
  getModelSettings,
  getSession,
  login,
  logout,
  updateModelSettings,
} from './api/client';
import type { AppConfigResponse, HealthResponse, ModelSettingsResponse } from './types/api';

type LoadState =
  | { status: 'loading' }
  | { status: 'login'; message?: string }
  | { status: 'ready'; health: HealthResponse; config: AppConfigResponse; modelSettings: ModelSettingsResponse }
  | { status: 'error'; message: string };

export function App() {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [passphrase, setPassphrase] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let active = true;

    async function boot() {
      try {
        const session = await getSession();
        if (!session.authenticated) {
          if (active) {
            setState({ status: 'login' });
          }
          return;
        }
        await loadStatus(active);
      } catch (error) {
        if (active) {
          setState({
            status: 'error',
            message: error instanceof Error ? error.message : '无法检查会话状态',
          });
        }
      }
    }

    boot();

    return () => {
      active = false;
    };
  }, []);

  async function loadStatus(active = true) {
    try {
      const [health, config, modelSettings] = await Promise.all([getHealth(), getAppConfig(), getModelSettings()]);
      if (active) {
        setState({ status: 'ready', health, config, modelSettings });
      }
    } catch (error) {
      if (active) {
        if (error instanceof ApiError && error.status === 401) {
          setState({ status: 'login' });
          return;
        }
        setState({
          status: 'error',
          message: error instanceof Error ? error.message : '无法连接后端服务',
        });
      }
    }
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    try {
      await login(passphrase);
      setPassphrase('');
      await loadStatus();
    } catch (error) {
      const message =
        error instanceof ApiError && error.status === 401
          ? '访问口令不正确'
          : error instanceof Error
            ? error.message
            : '登录失败';
      setState({ status: 'login', message });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleLogout() {
    await logout();
    setState({ status: 'login' });
  }

  function handleModelSettingsSaved(modelSettings: ModelSettingsResponse) {
    setState((current) => {
      if (current.status !== 'ready') {
        return current;
      }
      return { ...current, modelSettings };
    });
  }

  if (state.status === 'login') {
    return (
      <main className="shell">
        <section className="loginLayout">
          <div>
            <p className="eyebrow">单账户访问保护</p>
            <h1>输入访问口令</h1>
            <p className="summary">控制台和 API 已启用单账户会话保护。</p>
          </div>
          <form className="panel loginForm" onSubmit={handleLogin}>
            <label htmlFor="passphrase">访问口令</label>
            <input
              id="passphrase"
              type="password"
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
              autoComplete="current-password"
              required
            />
            {state.message ? <p className="formError">{state.message}</p> : null}
            <button type="submit" disabled={submitting}>
              {submitting ? '正在进入...' : '进入控制台'}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <section className="intro">
        <div>
          <p className="eyebrow">受保护控制台</p>
          <h1>研究自动化工作台</h1>
          <p className="summary">当前会话已通过访问口令验证，后端 API 将接受同源请求。</p>
        </div>
        <div className="consolePanels">
          <StatusPanel state={state} onLogout={handleLogout} />
          {state.status === 'ready' ? (
            <ModelSettingsPanel settings={state.modelSettings} onSaved={handleModelSettingsSaved} />
          ) : null}
        </div>
      </section>
    </main>
  );
}

function StatusPanel({ state, onLogout }: { state: LoadState; onLogout: () => void }) {
  if (state.status === 'loading') {
    return <div className="panel">正在检查会话...</div>;
  }

  if (state.status === 'error') {
    return (
      <div className="panel panelError">
        <strong>连接失败</strong>
        <span>{state.message}</span>
      </div>
    );
  }

  if (state.status !== 'ready') {
    return <div className="panel">正在检查会话...</div>;
  }

  return (
    <div className="panel">
      <div className="row">
        <span>API</span>
        <strong>{state.health.status}</strong>
      </div>
      <div className="row">
        <span>环境</span>
        <strong>{state.config.environment}</strong>
      </div>
      <div className="row">
        <span>数据库配置</span>
        <strong>{state.health.database_configured ? '已配置' : '未配置'}</strong>
      </div>
      <div className="row">
        <span>模型提供方</span>
        <strong>{state.config.model_provider}</strong>
      </div>
      <button className="secondaryButton" type="button" onClick={onLogout}>
        退出
      </button>
    </div>
  );
}

function ModelSettingsPanel({
  settings,
  onSaved,
}: {
  settings: ModelSettingsResponse;
  onSaved: (settings: ModelSettingsResponse) => void;
}) {
  const [provider, setProvider] = useState(settings.provider);
  const [baseUrl, setBaseUrl] = useState(settings.base_url ?? '');
  const [defaultModel, setDefaultModel] = useState(settings.default_model);
  const [apiKey, setApiKey] = useState('');
  const [clearApiKey, setClearApiKey] = useState(false);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      const updated = await updateModelSettings({
        provider,
        base_url: baseUrl || null,
        default_model: defaultModel,
        api_key: apiKey || undefined,
        clear_api_key: clearApiKey,
      });
      setApiKey('');
      setClearApiKey(false);
      setMessage('已保存');
      onSaved(updated);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="panel settingsForm" onSubmit={handleSubmit}>
      <div className="settingsHeader">
        <strong>模型设置</strong>
        <span>{settings.api_key_configured ? 'API key 已配置' : 'API key 未配置'}</span>
      </div>
      <label htmlFor="model-provider">提供方</label>
      <input id="model-provider" value={provider} onChange={(event) => setProvider(event.target.value)} required />
      <label htmlFor="model-base-url">Base URL</label>
      <input
        id="model-base-url"
        value={baseUrl}
        onChange={(event) => setBaseUrl(event.target.value)}
        placeholder="https://api.openai.com/v1"
      />
      <label htmlFor="default-model">默认模型</label>
      <input id="default-model" value={defaultModel} onChange={(event) => setDefaultModel(event.target.value)} required />
      <label htmlFor="model-api-key">API key</label>
      <input
        id="model-api-key"
        type="password"
        value={apiKey}
        onChange={(event) => setApiKey(event.target.value)}
        placeholder="保持为空则不修改"
        autoComplete="off"
      />
      <label className="checkboxRow">
        <input type="checkbox" checked={clearApiKey} onChange={(event) => setClearApiKey(event.target.checked)} />
        清除已保存的 API key
      </label>
      {message ? <p className="formHint">{message}</p> : null}
      <button type="submit" disabled={saving}>
        {saving ? '正在保存...' : '保存设置'}
      </button>
    </form>
  );
}
