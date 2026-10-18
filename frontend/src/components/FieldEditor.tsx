import { useState } from "react";
import { api } from "../api/client";
import { useI18n } from "../i18n";

const OPTIONS: Record<string, (string | number | boolean | null)[]> = {
  mood: [null, 1, 2, 3, 4, 5],
  sleep: ["good", "poor", "unknown"],
  appetite: ["normal", "reduced", "skipped_meals", "unknown"],
  meds_taken: ["yes", "partial", "no", "unknown", "not_applicable"],
  pain: ["none", "mild", "moderate", "severe", "unknown"],
  went_out_today: ["yes", "no", "unknown"],
  loneliness_expressed: [true, false],
};

export const EDITABLE_FIELDS = Object.keys(OPTIONS);

/** Social workers can correct an extracted value (PDPO DPP2). Every edit is audited server-side. */
export function FieldEditor({ callId, field, current, onSaved }: { callId: number; field: string; current: unknown; onSaved: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(String(current));
  const [error, setError] = useState<string | null>(null);
  const options = OPTIONS[field];
  if (!options) return null;

  async function save() {
    const chosen = options.find((o) => String(o) === value);
    try {
      await api.correct(callId, field, chosen === undefined ? null : chosen);
      setOpen(false);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="rounded border px-2 text-sm">
        {t("edit")}
      </button>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-1">
      <select aria-label={field} value={value} onChange={(e) => setValue(e.target.value)} className="rounded border-2 p-1 text-sm">
        {options.map((o) => (
          <option key={String(o)} value={String(o)}>
            {o === null ? "—" : String(o)}
          </option>
        ))}
      </select>
      <button onClick={() => void save()} className="rounded bg-blue-900 px-2 text-sm text-white">{t("editSave")}</button>
      <button onClick={() => setOpen(false)} className="rounded border px-2 text-sm">{t("alerts_cancel")}</button>
      {error && <span role="alert" className="text-sm font-semibold text-red-800">{error}</span>}
    </span>
  );
}
