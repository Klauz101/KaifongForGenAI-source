import type {
  AlertItem,
  CallDetail,
  ElderDetail,
  ElderListItem,
  FamilyElder,
  JyutpingConversion,
  DemoStatus,
  AuditItem,
  ConsentStatus,
  CostSummary,
  EvalResults,
  Digest,
  FamilyDigestItem,
  LatencyMetrics,
  SpokenLine,
  StartCallResponse,
  Today,
  User,
} from "./types";

export interface Health {
  status: string;
  env: string;
  providers: Record<string, string>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("kf:unauthorized"));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  me: () => request<User>("/api/auth/me"),
  login: (email: string, password: string) =>
    request<User>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  today: () => request<Today>("/api/today"),
  elders: () => request<ElderListItem[] | FamilyElder[]>("/api/elders"),
  elder: (id: number) => request<ElderDetail>(`/api/elders/${id}`),
  call: (id: number) => request<CallDetail>(`/api/calls/${id}`),
  alerts: (status: string, level: string) => {
    const q = new URLSearchParams({ status });
    if (level) q.set("level", level);
    return request<AlertItem[]>(`/api/alerts?${q}`);
  },
  startCall: (elderId: number, language?: string) =>
    request<StartCallResponse>("/api/calls", {
      method: "POST",
      body: JSON.stringify({ elder_id: elderId, mode: "simulator", language }),
    }),
  endCall: (callId: number) => request<{ type: string }>(`/api/calls/${callId}/end`, { method: "POST" }),
  filler: (language: string) => request<SpokenLine & { text: string }>(`/api/audio/filler/${language}`),
  latency: () => request<LatencyMetrics>("/api/metrics/latency"),
  reanalyse: (callId: number) =>
    request<{ call_id: number; level: string }>(`/api/calls/${callId}/reanalyse`, { method: "POST" }),
  ackAlert: (id: number) => request<AlertItem>(`/api/alerts/${id}/acknowledge`, { method: "POST" }),
  resolveAlert: (id: number, outcome: string, notes: string) =>
    request<AlertItem>(`/api/alerts/${id}/resolve`, { method: "POST", body: JSON.stringify({ outcome, notes }) }),
  logAction: (elderId: number, action: string, notes: string) =>
    request<{ id: number }>(`/api/elders/${elderId}/actions`, {
      method: "POST",
      body: JSON.stringify({ action, notes: notes || null }),
    }),
  setStatus: (elderId: number, status: "active" | "paused") =>
    request<{ id: number; status: string }>(`/api/elders/${elderId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  familyDigest: () => request<FamilyDigestItem[]>("/api/family/digest"),
  elderDigest: (elderId: number) => request<Digest | null>(`/api/elders/${elderId}/digest`),
  generateDigest: (elderId: number) => request<Digest>(`/api/elders/${elderId}/digest`, { method: "POST" }),
  sendDigest: (digestId: number) => request<Digest>(`/api/digests/${digestId}/send`, { method: "POST" }),
  consent: (elderId: number) => request<ConsentStatus>(`/api/elders/${elderId}/consent`),
  withdrawConsent: (elderId: number, notes: string) =>
    request<ConsentStatus>(`/api/elders/${elderId}/consent/withdraw`, { method: "POST", body: JSON.stringify({ notes }) }),
  purge: (elderId: number) => request<Record<string, number>>(`/api/elders/${elderId}/purge`, { method: "POST" }),
  exportData: (elderId: number) => request<unknown>(`/api/elders/${elderId}/export`),
  audit: (action: string, entity: string, offset: number) => {
    const q = new URLSearchParams({ limit: "50", offset: String(offset) });
    if (action) q.set("action", action);
    if (entity) q.set("entity", entity);
    return request<{ total: number; items: AuditItem[] }>(`/api/audit?${q}`);
  },
  correct: (callId: number, field: string, value: string | number | boolean | null) =>
    request<{ level: string }>(`/api/calls/${callId}/extraction`, { method: "PATCH", body: JSON.stringify({ field, value }) }),
  cost: () => request<CostSummary>("/api/metrics/cost"),
  evaluation: () => request<{ results: EvalResults | null }>("/api/metrics/eval"),
  demoStatus: () => request<DemoStatus>("/api/demo/status"),
  demoReset: () => request<{ ok: boolean }>("/api/demo/reset", { method: "POST" }),
  demoDecline: (elderId: number, days = 3) =>
    request<{ level: string; reasons: string[] }>(`/api/demo/decline/${elderId}`, { method: "POST", body: JSON.stringify({ days }) }),
  demoInject: (elderId: number) => request<{ injected: string }>(`/api/demo/inject/${elderId}`, { method: "POST" }),
  demoMissedCall: (elderId: number) => request<{ level: string }>(`/api/demo/missed-call/${elderId}`, { method: "POST" }),
  jyutpingConvert: (text: string) =>
    request<JyutpingConversion>("/api/jyutping/convert", { method: "POST", body: JSON.stringify({ text }) }),
  setMemoryActive: (elderId: number, factId: number, active: boolean) =>
    request<{ id: number; active: boolean }>(`/api/elders/${elderId}/memory/${factId}`, {
      method: "PATCH",
      body: JSON.stringify({ active }),
    }),
};

// Kept for the Phase 0 health check helper.
export const getHealth = api.health;
