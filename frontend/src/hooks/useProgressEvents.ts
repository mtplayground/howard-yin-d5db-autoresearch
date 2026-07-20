import { useEffect, useState } from 'react';

import type { ProgressEvent } from '../types/api';

type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'error';

interface ProgressEventState {
  status: ConnectionStatus;
  events: ProgressEvent[];
}

const EVENT_TYPES = ['connected', 'progress', 'log', 'artifact'] as const;

export function useProgressEvents(enabled: boolean, runId?: string) {
  const [state, setState] = useState<ProgressEventState>({ status: 'idle', events: [] });

  useEffect(() => {
    if (!enabled) {
      setState({ status: 'idle', events: [] });
      return;
    }

    const params = new URLSearchParams();
    if (runId) {
      params.set('run_id', runId);
    }
    const url = `/api/events/stream${params.size ? `?${params.toString()}` : ''}`;
    const source = new EventSource(url, { withCredentials: true });

    setState((current) => ({ ...current, status: 'connecting' }));

    source.onopen = () => {
      setState((current) => ({ ...current, status: 'open' }));
    };

    source.onerror = () => {
      setState((current) => ({ ...current, status: 'error' }));
    };

    const handlers = EVENT_TYPES.map((eventType) => {
      const handler = (message: MessageEvent<string>) => {
        try {
          const event = JSON.parse(message.data) as ProgressEvent;
          setState((current) => ({
            status: current.status === 'idle' ? 'open' : current.status,
            events: [event, ...current.events].slice(0, 50),
          }));
        } catch {
          setState((current) => ({ ...current, status: 'error' }));
        }
      };
      source.addEventListener(eventType, handler);
      return { eventType, handler };
    });

    return () => {
      handlers.forEach(({ eventType, handler }) => source.removeEventListener(eventType, handler));
      source.close();
    };
  }, [enabled, runId]);

  return state;
}
