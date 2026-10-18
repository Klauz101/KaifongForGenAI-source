import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { OUTCOMES } from "../api/types";
import { useI18n } from "../i18n";
import type { StringKey } from "../i18n/strings";

const FIELD = "w-full rounded border-2 border-gray-600 p-2";

/** Resolve an alert: outcome and a note are both mandatory (SPEC 8.2 workflow). */
export function ResolveForm({ alertId, onDone, onCancel }: { alertId: number; onDone: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const [outcome, setOutcome] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!outcome || !notes.trim()) {
      setError(t("alerts_needNote"));
      return;
    }
    setBusy(true);
    try {
      await api.resolveAlert(alertId, outcome, notes.trim());
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-3 space-y-2 rounded bg-gray-50 p-3">
      <label className="block">
        {t("alerts_outcome")}
        <select value={outcome} onChange={(e) => setOutcome(e.target.value)} className={FIELD}>
          <option value="">—</option>
          {OUTCOMES.map((o) => (
            <option key={o} value={o}>
              {t(`act_${o}` as StringKey)}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        {t("alerts_note")}
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} className={FIELD} />
      </label>
      {error && <p role="alert" className="font-semibold text-red-800">{error}</p>}
      <div className="flex gap-2">
        <button disabled={busy} className="rounded bg-blue-900 px-4 py-2 font-semibold text-white disabled:opacity-60">
          {t("alerts_save")}
        </button>
        <button type="button" onClick={onCancel} className="rounded border-2 px-4 py-2">
          {t("alerts_cancel")}
        </button>
      </div>
    </form>
  );
}

/** Log a visit / call from the triage card; the note is optional. */
export function QuickActionForm({
  elderId,
  action,
  onDone,
  onCancel,
}: {
  elderId: number;
  action: "home_visit" | "phone_call";
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.logAction(elderId, action, notes.trim());
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={submit} className="mt-2 flex flex-wrap items-center gap-2">
      <input
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder={`${t(`act_${action}` as StringKey)} — ${t("alerts_noteOptional")}`}
        aria-label={t(`act_${action}` as StringKey)}
        className="min-w-0 flex-1 rounded border-2 border-gray-600 p-2"
      />
      <button className="rounded bg-blue-900 px-3 py-2 font-semibold text-white">{t("alerts_save")}</button>
      <button type="button" onClick={onCancel} className="rounded border-2 px-3 py-2">{t("alerts_cancel")}</button>
      {error && <span role="alert" className="font-semibold text-red-800">{error}</span>}
    </form>
  );
}
