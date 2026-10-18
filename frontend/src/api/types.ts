export type Level = "red" | "amber" | "green";
export type Bucket = Level | "not_called";
export type Role = "admin" | "social_worker" | "family";

export interface User {
  id: number;
  name: string;
  email: string;
  role: Role;
  linked_elder_ids: number[];
}

export interface Reason {
  code: string;
  text_en: string;
  text_zh: string;
  evidence_call_ids: number[];
}

export interface ElderBrief {
  id: number;
  display_name: string;
  display_name_en: string;
  age: number;
  district: string;
  language: string;
  status: string;
  is_synthetic: boolean;
}

export interface OpenAlert {
  id: number;
  level: Level;
  type: string;
  status: "open" | "acknowledged";
}

export interface TodayCard extends ElderBrief {
  open_alert: OpenAlert | null;
  level: Level | null;
  bucket: Bucket;
  reason_count: number;
  top_reasons: Reason[];
  last_call_at: string | null;
  last_call_status: string | null;
}

export interface Today {
  date: string;
  summary: { need_attention: number; urgent: number; calls_completed: number; total: number };
  cards: TodayCard[];
}

export interface ElderListItem extends ElderBrief {
  level: Level | null;
}

export interface FamilyElder {
  id: number;
  display_name: string;
  display_name_en: string;
}

export interface TrendPoint {
  date: string;
  call_id: number;
  status: string;
  mood?: number | null;
  sleep?: string;
  appetite?: string;
  meds?: string;
  pain?: string;
  went_out?: string;
  lonely?: boolean;
  confidence?: string;
}

export interface AlertItem {
  id: number;
  elder_id: number;
  call_id: number | null;
  level: Level;
  type: string;
  reason_en: string;
  reason_zh: string;
  created_at: string;
  status: "open" | "acknowledged" | "resolved";
  outcome: string | null;
  notes: string | null;
  resolved_at: string | null;
  acknowledged_at: string | null;
  elder?: ElderBrief;
}

export interface ElderDetail {
  elder: ElderBrief & {
    full_name: string;
    phone: string;
    gender: string;
    interests: string[];
    care_notes: string | null;
    preferred_call_time: string;
  };
  consent: {
    method: string;
    witnessed_by: string;
    consented_at: string;
    withdrawn_at: string | null;
    scope_family_digest: boolean;
  } | null;
  risk: { level: Level | null; reasons: Reason[] };
  risk_history: { date: string; level: Level; reason_count: number }[];
  trends: TrendPoint[];
  memory_facts: { id: number; fact: string; fact_yue: string; category: string; active: boolean }[];
  alerts: AlertItem[];
  actions: { id: number; action: string; notes: string | null; created_at: string; alert_id: number | null }[];
  nudges: {
    id: number;
    type: string;
    text_en: string;
    text_yue: string;
    elder_response: string;
    followed_up: boolean | null;
    date: string | null;
    resource: string | null;
    resource_verified: boolean | null;
  }[];
  connection: { suggested: number; accepted: number; followed_up: number };
  calls: {
    id: number;
    started_at: string;
    status: string;
    duration_s: number;
    mode: string;
    summary_en: string | null;
    summary_yue: string | null;
  }[];
}

export interface EvidenceField {
  value: unknown;
  confidence?: string;
  location?: string | null;
  evidence_turns: number[];
}

export interface CallDetail {
  call: {
    id: number;
    started_at: string;
    ended_at: string | null;
    status: string;
    mode: string;
    language: string;
    duration_s: number;
    turn_count: number;
    safety_triggered: boolean;
    avg_latency_ms: number | null;
  };
  elder: ElderBrief;
  summary: {
    extraction: Record<string, EvidenceField | unknown>;
    summary_en: string;
    summary_yue: string;
    overall_confidence: string;
    model_used: string;
  } | null;
  turns: {
    index: number;
    speaker: "elder" | "kaifong";
    text: string;
    text_en: string | null;
    stt_confidence: number | null;
    is_scripted_safety: boolean;
    latency_breakdown: { stt_ms?: number; llm_ms?: number; tts_ms?: number };
  }[];
  has_transcript: boolean;
}

export interface SpokenLine {
  audio_b64: string | null;
  mime: string;
}

export interface StartCallResponse {
  call_id: number;
  stt: string; // "mock" = the server cannot transcribe audio, so the browser should do the listening
  language: "yue" | "en";
  elder: { id: number; display_name: string; display_name_en: string };
  greeting: SpokenLine & { type: "call.greeting"; text: string; text_en: string };
}

/** Events the server sends on /ws/calls/{id} (SPEC 6.3, plus reply_chunk for sentence streaming). */
export type CallEvent =
  | { type: "turn.transcript"; index: number; text: string; confidence: number | null; stt_ms: number; raw?: string | null }
  | { type: "turn.reply_chunk"; seq: number; text: string }
  | ({ type: "turn.reply_audio"; seq: number } & SpokenLine)
  | { type: "turn.reply_text"; index: number; text: string; text_en: string | null; stage: string }
  | { type: "turn.translation"; index: number; text_en: string }
  | { type: "call.ended"; call_id: number; status: string; duration_s: number }
  | { type: "error"; message: string; code?: string };

export interface LatencyMetrics {
  providers: Record<string, string>;
  stats: { count: number; median_ms: number | null; mean_ms: number | null; p90_ms: number | null };
  target_ms: number;
  turns: { call_id: number; index: number; stt_ms?: number; llm_ms?: number; tts_ms?: number; ttfa_ms?: number }[];
}

export type Outcome =
  | "home_visit"
  | "phone_call"
  | "referral"
  | "family_contacted"
  | "centre_activity_arranged"
  | "no_action_needed";

export const OUTCOMES: Outcome[] = [
  "home_visit",
  "phone_call",
  "referral",
  "family_contacted",
  "centre_activity_arranged",
  "no_action_needed",
];

/** Events pushed on /ws/dashboard. */
export type DashboardEvent =
  | { type: "alert.created"; alert: AlertItem; elder: { id: number; display_name: string; display_name_en: string } }
  | { type: "alert.updated"; alert: AlertItem }
  | { type: "risk.updated"; elder_id: number; level: Level; reasons: string[] }
  | { type: "call.started" | "call.ended" | "call.summary_ready"; call_id: number; elder_id?: number }
  | { type: string; [k: string]: unknown };

export interface Digest {
  id: number;
  elder_id: number;
  week_start: string;
  text_en: string;
  text_zh: string;
  generated_at: string;
  sent_simulated_at: string | null;
}

/** What a family account may see: text only, never calls or transcripts. */
export interface FamilyDigestItem {
  elder: { display_name: string; display_name_en: string };
  week_start: string;
  text_en: string;
  text_zh: string;
  sent_simulated_at: string | null;
}

export interface ConsentStatus {
  has_consent: boolean;
  active: boolean;
  consented_at: string | null;
  method: string | null;
  witnessed_by: string | null;
  scope_calls: boolean | null;
  scope_family_digest: boolean | null;
  retention_days: number | null;
  withdrawn_at: string | null;
  deletion_due_at: string | null;
  elder_status: string | null;
}

export interface AuditItem {
  id: number;
  at: string;
  user_id: number | null;
  user: string;
  action: string;
  entity: string;
  entity_id: number | null;
}

export interface CostSummary {
  currency: string;
  calls_measured: number;
  calls_priced: number;
  avg_cost_per_call: number | null;
  est_monthly_per_person: number | null;
  calls_per_month: number;
  missing_prices: string[];
  status: string;
}

export interface EvalTarget {
  name: string;
  target: string;
  value: string;
  met: boolean | null;
}

export interface EvalResults {
  generated_at: string;
  synthetic_note: string;
  targets: EvalTarget[];
  redflags: { core_recall_pct: number; stretch_recall_pct: number; stretch_misses: { id: string; text: string }[]; counts: Record<string, number> };
  extraction: { agreement_pct: number; provider: string; model: string; by_tag_pct: Record<string, number>; cases: number };
  triage: { summary: string; agreed_by_team: boolean };
  stt: { status: string; cer_pct: number | null; clips: number };
}

export interface DemoStatus {
  app_env: string;
  providers: Record<string, string>;
  calls_in_progress: number;
  open_alerts: number;
  people: {
    id: number;
    display_name: string;
    display_name_en: string;
    language: string;
    status: string;
    level: Level | null;
    call_in_progress: boolean;
  }[];
}

export interface JyutpingConversion {
  is_jyutping: boolean;
  hanzi: string;
  segments: { kind: string; text: string; options: string[]; chosen: number; known: boolean }[];
}
