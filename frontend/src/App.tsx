import { useEffect, useState, type FormEvent } from 'react';

import {
  ApiError,
  confirmIdea,
  getAppConfig,
  getHealth,
  getIdea,
  getIdeas,
  getModelSettings,
  getSession,
  login,
  logout,
  refineIdea,
  updateModelSettings,
} from './api/client';
import { useProgressEvents } from './hooks/useProgressEvents';
import type { AppConfigResponse, HealthResponse, IdeaListResponse, IdeaResponse, IdeaSort, ModelSettingsResponse, RunResponse } from './types/api';

type LoadState =
  | { status: 'loading' }
  | { status: 'login'; message?: string }
  | { status: 'ready'; health: HealthResponse; config: AppConfigResponse; modelSettings: ModelSettingsResponse }
  | { status: 'error'; message: string };

export function App() {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [passphrase, setPassphrase] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [selectedIdeaId, setSelectedIdeaId] = useState<string | null>(null);
  const [ideaRefreshKey, setIdeaRefreshKey] = useState(0);
  const progressEvents = useProgressEvents(state.status === 'ready');

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
      <section className="workbench">
        <header className="workbenchHeader">
          <p className="eyebrow">受保护控制台</p>
          <h1>研究自动化工作台</h1>
        </header>
        <div className="workbenchGrid">
          <div className="ideaWorkspace">
            <IdeasPanel
              enabled={state.status === 'ready'}
              selectedIdeaId={selectedIdeaId}
              refreshKey={ideaRefreshKey}
              onSelectIdea={setSelectedIdeaId}
            />
            <IdeaDetailPanel
              enabled={state.status === 'ready'}
              ideaId={selectedIdeaId}
              onRefined={() => setIdeaRefreshKey((value) => value + 1)}
            />
          </div>
          <div className="consolePanels">
            <StatusPanel state={state} onLogout={handleLogout} />
            <ProgressPanel status={progressEvents.status} events={progressEvents.events} />
            {state.status === 'ready' ? (
              <ModelSettingsPanel settings={state.modelSettings} onSaved={handleModelSettingsSaved} />
            ) : null}
          </div>
        </div>
      </section>
    </main>
  );
}

type IdeaFilters = {
  topic: string;
  status: string;
  minScore: string;
  sort: IdeaSort;
};

type IdeaLoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: IdeaListResponse }
  | { status: 'error'; message: string };

const defaultIdeaFilters: IdeaFilters = {
  topic: '',
  status: 'candidate',
  minScore: 'any',
  sort: 'score_desc',
};

function IdeasPanel({
  enabled,
  selectedIdeaId,
  refreshKey,
  onSelectIdea,
}: {
  enabled: boolean;
  selectedIdeaId: string | null;
  refreshKey: number;
  onSelectIdea: (ideaId: string) => void;
}) {
  const [draftFilters, setDraftFilters] = useState<IdeaFilters>(defaultIdeaFilters);
  const [filters, setFilters] = useState<IdeaFilters>(defaultIdeaFilters);
  const [ideasState, setIdeasState] = useState<IdeaLoadState>({ status: 'idle' });

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let active = true;
    setIdeasState({ status: 'loading' });
    getIdeas({
      topic: filters.topic.trim() || undefined,
      status: filters.status === 'all' ? undefined : filters.status,
      min_score: filters.minScore === 'any' ? undefined : Number(filters.minScore),
      sort: filters.sort,
      limit: 50,
    })
      .then((data) => {
        if (active) {
          setIdeasState({ status: 'ready', data });
        }
      })
      .catch((error) => {
        if (active) {
          setIdeasState({ status: 'error', message: error instanceof Error ? error.message : '无法加载 idea 列表' });
        }
      });
    return () => {
      active = false;
    };
  }, [enabled, filters, refreshKey]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters(draftFilters);
  }

  return (
    <section className="panel ideasPanel">
      <div className="settingsHeader">
        <strong>候选 idea</strong>
        <span>{ideasState.status === 'ready' ? `${ideasState.data.total} 条` : ideasState.status === 'loading' ? '加载中' : '待加载'}</span>
      </div>
      <form className="ideaFilters" onSubmit={handleSubmit}>
        <label htmlFor="idea-topic">
          主题
          <input
            id="idea-topic"
            value={draftFilters.topic}
            onChange={(event) => setDraftFilters({ ...draftFilters, topic: event.target.value })}
            placeholder="检索标题、动机、相关工作"
          />
        </label>
        <label htmlFor="idea-status">
          状态
          <select
            id="idea-status"
            value={draftFilters.status}
            onChange={(event) => setDraftFilters({ ...draftFilters, status: event.target.value })}
          >
            <option value="candidate">候选</option>
            <option value="draft">草稿</option>
            <option value="approved">已确认</option>
            <option value="rejected">已拒绝</option>
            <option value="archived">已归档</option>
            <option value="all">全部</option>
          </select>
        </label>
        <label htmlFor="idea-min-score">
          可行性
          <select
            id="idea-min-score"
            value={draftFilters.minScore}
            onChange={(event) => setDraftFilters({ ...draftFilters, minScore: event.target.value })}
          >
            <option value="any">不限</option>
            <option value="0.8">80% 以上</option>
            <option value="0.6">60% 以上</option>
            <option value="0.4">40% 以上</option>
          </select>
        </label>
        <label htmlFor="idea-sort">
          排序
          <select
            id="idea-sort"
            value={draftFilters.sort}
            onChange={(event) => setDraftFilters({ ...draftFilters, sort: event.target.value as IdeaSort })}
          >
            <option value="score_desc">可行性高到低</option>
            <option value="score_asc">可行性低到高</option>
            <option value="created_desc">最新生成</option>
            <option value="created_asc">最早生成</option>
            <option value="title_asc">标题 A-Z</option>
          </select>
        </label>
        <button type="submit">筛选</button>
      </form>
      <IdeaList ideasState={ideasState} selectedIdeaId={selectedIdeaId} onSelectIdea={onSelectIdea} />
    </section>
  );
}

function IdeaList({
  ideasState,
  selectedIdeaId,
  onSelectIdea,
}: {
  ideasState: IdeaLoadState;
  selectedIdeaId: string | null;
  onSelectIdea: (ideaId: string) => void;
}) {
  if (ideasState.status === 'idle' || ideasState.status === 'loading') {
    return <p className="emptyState">正在加载候选 idea。</p>;
  }

  if (ideasState.status === 'error') {
    return <p className="formError">{ideasState.message}</p>;
  }

  if (ideasState.data.items.length === 0) {
    return <p className="emptyState">暂无匹配的候选 idea。</p>;
  }

  return (
    <ol className="ideaList">
      {ideasState.data.items.map((idea) => (
        <li key={idea.id}>
          <div className="ideaItemHeader">
            <strong>{idea.title}</strong>
            <span>{formatScore(idea.score)}</span>
          </div>
          <p>{idea.rationale || idea.problem_statement || '暂无动机说明'}</p>
          <div className="ideaMeta">
            <span>{idea.status}</span>
            <span>{new Date(idea.created_at).toLocaleDateString()}</span>
            {idea.extra.feasibility ? <span>{idea.extra.feasibility}</span> : null}
          </div>
          {idea.source_context.related_work?.length ? (
            <div className="relatedWork">
              {idea.source_context.related_work.slice(0, 3).map((work) => (
                <span key={work}>{work}</span>
              ))}
            </div>
          ) : null}
          <button
            className={selectedIdeaId === idea.id ? 'activeIdeaButton' : 'secondaryButton'}
            type="button"
            onClick={() => onSelectIdea(idea.id)}
          >
            {selectedIdeaId === idea.id ? '正在查看' : '查看详情'}
          </button>
        </li>
      ))}
    </ol>
  );
}

type IdeaDetailState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; idea: IdeaResponse; assistantMessage?: string; run?: RunResponse }
  | { status: 'error'; message: string };

function IdeaDetailPanel({
  enabled,
  ideaId,
  onRefined,
}: {
  enabled: boolean;
  ideaId: string | null;
  onRefined: () => void;
}) {
  const [detailState, setDetailState] = useState<IdeaDetailState>({ status: 'idle' });
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!enabled || !ideaId) {
      setDetailState({ status: 'idle' });
      return;
    }
    let active = true;
    setDetailState({ status: 'loading' });
    getIdea(ideaId)
      .then((idea) => {
        if (active) {
          setDetailState({ status: 'ready', idea });
        }
      })
      .catch((error) => {
        if (active) {
          setDetailState({ status: 'error', message: error instanceof Error ? error.message : '无法加载 idea 详情' });
        }
      });
    return () => {
      active = false;
    };
  }, [enabled, ideaId]);

  async function handleRefine(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ideaId || !message.trim()) {
      return;
    }
    setSubmitting(true);
    try {
      const response = await refineIdea(ideaId, { message });
      setDetailState({ status: 'ready', idea: response.idea, assistantMessage: response.assistant_message });
      setMessage('');
      onRefined();
    } catch (error) {
      setDetailState({ status: 'error', message: error instanceof Error ? error.message : '细化失败' });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleConfirm() {
    if (!ideaId) {
      return;
    }
    setConfirming(true);
    try {
      const response = await confirmIdea(ideaId);
      setDetailState({
        status: 'ready',
        idea: response.idea,
        assistantMessage: '已确认并触发自动实验运行。',
        run: response.run,
      });
      onRefined();
    } catch (error) {
      setDetailState({ status: 'error', message: error instanceof Error ? error.message : '确认失败' });
    } finally {
      setConfirming(false);
    }
  }

  return (
    <section className="panel ideaDetailPanel">
      <div className="settingsHeader">
        <strong>idea 详情</strong>
        <span>{detailState.status === 'ready' ? detailState.idea.status : ideaId ? '加载中' : '未选择'}</span>
      </div>
      <IdeaDetailBody detailState={detailState} />
      {detailState.status === 'ready' ? (
        <>
          <div className="confirmGateway">
            <button type="button" onClick={handleConfirm} disabled={confirming || detailState.idea.status === 'approved'}>
              {confirming ? '正在触发...' : detailState.idea.status === 'approved' ? '已确认' : '确认并执行'}
            </button>
            {detailState.run ? <span>运行 {detailState.run.status}</span> : null}
          </div>
          <form className="refineForm" onSubmit={handleRefine}>
            <label htmlFor="idea-refine-message">交流/微调</label>
            <textarea
              id="idea-refine-message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              maxLength={4000}
              placeholder="例如：收窄实验范围，强调可验证指标"
              required
            />
            <button type="submit" disabled={submitting}>
              {submitting ? '正在细化...' : '提交细化'}
            </button>
          </form>
        </>
      ) : null}
    </section>
  );
}

function IdeaDetailBody({ detailState }: { detailState: IdeaDetailState }) {
  if (detailState.status === 'idle') {
    return <p className="emptyState">从候选列表选择一条 idea。</p>;
  }
  if (detailState.status === 'loading') {
    return <p className="emptyState">正在加载 idea 详情。</p>;
  }
  if (detailState.status === 'error') {
    return <p className="formError">{detailState.message}</p>;
  }

  const { idea } = detailState;
  return (
    <div className="ideaDetailContent">
      <div>
        <h2>{idea.title}</h2>
        <span className="scoreBadge">{formatScore(idea.score)}</span>
      </div>
      <DetailBlock label="动机" value={idea.rationale} />
      <DetailBlock label="问题" value={idea.problem_statement} />
      <DetailBlock label="假设" value={idea.hypothesis} />
      <DetailBlock label="可行性" value={idea.extra.feasibility} />
      {idea.source_context.related_work?.length ? (
        <div className="detailBlock">
          <strong>相关工作</strong>
          <div className="relatedWork">
            {idea.source_context.related_work.map((work) => (
              <span key={work}>{work}</span>
            ))}
          </div>
        </div>
      ) : null}
      {detailState.assistantMessage ? <p className="assistantMessage">{detailState.assistantMessage}</p> : null}
    </div>
  );
}

function DetailBlock({ label, value }: { label: string; value?: string | null }) {
  if (!value) {
    return null;
  }
  return (
    <div className="detailBlock">
      <strong>{label}</strong>
      <p>{value}</p>
    </div>
  );
}

function formatScore(score: number | null): string {
  if (score === null) {
    return '未评分';
  }
  return `${Math.round(score * 100)}%`;
}

function ProgressPanel({ status, events }: ReturnType<typeof useProgressEvents>) {
  return (
    <div className="panel progressPanel">
      <div className="settingsHeader">
        <strong>实时进度</strong>
        <span>{status === 'open' ? '已连接' : status === 'connecting' ? '连接中' : status === 'error' ? '连接异常' : '未连接'}</span>
      </div>
      {events.length === 0 ? (
        <p className="emptyState">等待流水线进度、日志与产物更新。</p>
      ) : (
        <ol className="eventList">
          {events.map((event) => (
            <li key={event.id}>
              <span>{event.type}</span>
              <strong>{event.stage ?? event.run_id ?? '全局'}</strong>
              <p>{event.message}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
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
