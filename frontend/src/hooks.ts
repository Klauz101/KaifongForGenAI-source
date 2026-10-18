import { useCallback, useEffect, useRef, useState } from "react";
import type { DashboardEvent } from "./api/types";
import { subscribe } from "./dashboardEvents";

export interface Async<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch on mount / when deps change; optional polling (live updates arrive via WebSocket in a later phase). */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: number): Async<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps);

  useEffect(() => {
    let cancelled = false;
    run()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [run, tick]);

  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(() => setTick((n) => n + 1), pollMs);
    return () => clearInterval(id);
  }, [pollMs]);

  return { data, error, loading, reload: () => setTick((n) => n + 1) };
}

/** Subscribe to staff dashboard events (shared socket, see dashboardEvents.ts). */
export function useDashboardEvents(onEvent: (event: DashboardEvent) => void): void {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => subscribe((e) => handler.current(e)), []);
}
