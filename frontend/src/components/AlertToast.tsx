import { BellRing, Volume2, VolumeX, X } from "lucide-react";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import type { DashboardEvent } from "../api/types";
import { playAlertTone, setSoundEnabled, soundEnabled } from "../alertSound";
import { elderName } from "../format";
import { useDashboardEvents } from "../hooks";
import { useI18n } from "../i18n";
import type { StringKey } from "../i18n/strings";

interface Toast {
  id: number;
  level: "red" | "amber";
  type: string;
  reason_en: string;
  reason_zh: string;
  elder: { id: number; display_name: string; display_name_en: string };
}

/** Full-width alert toast: red ones are urgent, stay until dismissed, and beep. */
export function AlertToasts() {
  const { t, lang } = useI18n();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [sound, setSound] = useState(soundEnabled());

  const onEvent = useCallback((e: DashboardEvent) => {
    if (e.type !== "alert.created" || !("alert" in e) || !("elder" in e)) return;
    const a = e.alert as import("../api/types").AlertItem;
    const level = a.level === "red" ? "red" : "amber";
    setToasts((prev) => [
      { id: a.id, level, type: a.type, reason_en: a.reason_en, reason_zh: a.reason_zh, elder: e.elder as Toast["elder"] },
      ...prev.filter((x) => x.id !== a.id),
    ]);
    if (level === "red") playAlertTone();
  }, []);
  useDashboardEvents(onEvent);

  if (toasts.length === 0) return null;
  return (
    <div className="fixed inset-x-0 top-0 z-50 flex flex-col gap-1 p-2" aria-live="assertive">
      {toasts.map((x) => (
        <div
          key={x.id}
          role="alert"
          className={`flex flex-wrap items-center gap-3 rounded-lg border-4 p-4 text-lg shadow-2xl ${
            x.level === "red"
              ? "border-red-950 bg-red-800 text-white"
              : "border-amber-700 bg-amber-200 text-amber-950"
          }`}
        >
          <BellRing aria-hidden size={28} />
          <div className="min-w-0 flex-1">
            <div className="font-bold">
              {x.level === "red" ? t("toast_urgent") : t("toast_new")} — {elderName(x.elder, lang)}
            </div>
            <div>
              {x.type !== "red_flag_phrase" && `${t(`type_${x.type}` as StringKey)}: `}
              {lang === "zh" ? x.reason_zh : x.reason_en}
            </div>
          </div>
          <Link
            to="/alerts"
            onClick={() => setToasts((p) => p.filter((y) => y.id !== x.id))}
            className="rounded bg-white px-4 py-2 font-bold text-gray-900"
          >
            {t("toast_open")}
          </Link>
          <button
            onClick={() => {
              const next = !sound;
              setSound(next);
              setSoundEnabled(next);
            }}
            aria-label={t("toast_sound")}
            className="rounded border-2 border-current p-2"
          >
            {sound ? <Volume2 aria-hidden size={20} /> : <VolumeX aria-hidden size={20} />}
          </button>
          <button
            onClick={() => setToasts((p) => p.filter((y) => y.id !== x.id))}
            aria-label={t("toast_dismiss")}
            className="rounded border-2 border-current p-2"
          >
            <X aria-hidden size={20} />
          </button>
        </div>
      ))}
    </div>
  );
}
