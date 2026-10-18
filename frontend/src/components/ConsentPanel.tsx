import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth";
import { formatHK } from "../format";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";

/** Consent status, withdrawal (after an in-person confirmation), and admin export / purge. */
export function ConsentPanel({ elderId, onChanged }: { elderId: number; onChanged: () => void }) {
  const { t } = useI18n();
  const { user } = useAuth();
  const { data, reload } = useAsync(() => api.consent(elderId), [elderId]);
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (!data) return null;
  const withdrawn = data.withdrawn_at !== null;

  async function withdraw(e: FormEvent) {
    e.preventDefault();
    if (!notes.trim()) return setError(t("consent_withdrawNote"));
    try {
      await api.withdrawConsent(elderId, notes.trim());
      setOpen(false);
      reload();
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function download() {
    const blob = new Blob([JSON.stringify(await api.exportData(elderId), null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `kaifong-export-${elderId}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function purge() {
    if (!window.confirm(t("consent_purgeConfirm"))) return;
    await api.purge(elderId);
    setMessage(t("consent_purged"));
    onChanged();
  }

  return (
    <div className="mt-2 rounded border-2 p-3">
      <p className="font-semibold">
        {withdrawn ? t("consent_withdrawnOn", { d: formatHK(data.withdrawn_at) }) : t("consent_active")}
        {data.retention_days ? ` · ${t("consent_retention", { n: data.retention_days })}` : ""}
      </p>
      {withdrawn && data.deletion_due_at && <p className="text-red-900">{t("consent_deletionDue", { d: formatHK(data.deletion_due_at) })}</p>}
      {message && <p role="status" className="font-semibold text-green-900">{message}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        {!withdrawn && !open && (
          <button onClick={() => setOpen(true)} className="rounded border-2 border-red-800 px-3 py-2 font-semibold text-red-800">
            {t("consent_withdraw")}
          </button>
        )}
        {user?.role === "admin" && (
          <button onClick={() => void download()} className="rounded border-2 border-blue-900 px-3 py-2 font-semibold text-blue-900">
            {t("consent_export")}
          </button>
        )}
        {user?.role === "admin" && withdrawn && (
          <button onClick={() => void purge()} className="rounded bg-red-800 px-3 py-2 font-semibold text-white">
            {t("consent_purge")}
          </button>
        )}
      </div>
      {open && (
        <form onSubmit={withdraw} className="mt-3 space-y-2">
          <p className="rounded bg-red-50 p-2 text-red-900">{t("consent_withdrawWarn")}</p>
          <label className="block">
            {t("consent_withdrawNote")}
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} className="w-full rounded border-2 border-gray-600 p-2" />
          </label>
          {error && <p role="alert" className="font-semibold text-red-800">{error}</p>}
          <div className="flex gap-2">
            <button className="rounded bg-red-800 px-4 py-2 font-semibold text-white">{t("consent_withdrawConfirm")}</button>
            <button type="button" onClick={() => setOpen(false)} className="rounded border-2 px-4 py-2">{t("alerts_cancel")}</button>
          </div>
        </form>
      )}
    </div>
  );
}
