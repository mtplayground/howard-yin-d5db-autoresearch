import { useEffect, useState } from 'react';

import { getAppConfig, getHealth } from './api/client';
import type { AppConfigResponse, HealthResponse } from './types/api';

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; health: HealthResponse; config: AppConfigResponse }
  | { status: 'error'; message: string };

export function App() {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let active = true;

    async function loadStatus() {
      try {
        const [health, config] = await Promise.all([getHealth(), getAppConfig()]);
        if (active) {
          setState({ status: 'ready', health, config });
        }
      } catch (error) {
        if (active) {
          setState({
            status: 'error',
            message: error instanceof Error ? error.message : '无法连接后端服务',
          });
        }
      }
    }

    loadStatus();

    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="shell">
      <section className="intro">
        <div>
          <p className="eyebrow">Python 编排后端 + React SPA</p>
          <h1>研究自动化工作台骨架</h1>
          <p className="summary">
            当前版本建立了前后端入口、集中式环境配置，以及用于后续流水线能力的基础路由。
          </p>
        </div>
        <StatusPanel state={state} />
      </section>
    </main>
  );
}

function StatusPanel({ state }: { state: LoadState }) {
  if (state.status === 'loading') {
    return <div className="panel">正在检查服务状态...</div>;
  }

  if (state.status === 'error') {
    return (
      <div className="panel panelError">
        <strong>连接失败</strong>
        <span>{state.message}</span>
      </div>
    );
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
    </div>
  );
}

