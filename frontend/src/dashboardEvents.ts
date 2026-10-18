/** One shared /ws/dashboard connection for the whole app (toasts, Today, Alerts all listen to it). */
import type { DashboardEvent } from "./api/types";

type Handler = (event: DashboardEvent) => void;

const handlers = new Set<Handler>();
const statusHandlers = new Set<(connected: boolean) => void>();
let socket: WebSocket | null = null;
let retry: number | undefined;
let connected = false;

function setConnected(value: boolean): void {
  connected = value;
  statusHandlers.forEach((h) => h(value));
}

/** Live-update connection state, so the UI can say when it is reconnecting. */
export function subscribeStatus(handler: (connected: boolean) => void): () => void {
  statusHandlers.add(handler);
  handler(connected);
  return () => statusHandlers.delete(handler);
}

function connect(): void {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws/dashboard`);
  socket = ws;
  ws.onopen = () => setConnected(true);
  ws.onmessage = (m) => {
    let event: DashboardEvent;
    try {
      event = JSON.parse(m.data) as DashboardEvent;
    } catch {
      return; // ignore malformed event
    }
    handlers.forEach((h) => h(event));
  };
  ws.onclose = () => {
    if (socket === ws) socket = null;
    setConnected(false);
    if (handlers.size > 0) retry = window.setTimeout(connect, 3000);
  };
}

export function subscribe(handler: Handler): () => void {
  handlers.add(handler);
  if (!socket) connect();
  return () => {
    handlers.delete(handler);
    if (handlers.size === 0) {
      window.clearTimeout(retry);
      const ws = socket;
      socket = null;
      ws?.close();
    }
  };
}
