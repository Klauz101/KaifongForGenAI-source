import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ResolveForm } from "../components/AlertActions";
import { Empty, ErrorBox, Loading } from "../components/States";
import { RiskBadge } from "../components/RiskBadge";
import { elderName, formatHK } from "../format";
import { useAsync, useDashboardEvents } from "../hooks";
import { useI18n } from "../i18n";
import type { StringKey } from "../i18n/strings";

export default function Alerts() {
  const { t, lang } = useI18n();
  const [status, setStatus] = useState("all");
  const [level, setLevel] = useState("");
  const [resolving, setResolving] = useState<number | null>(null);
  const { data, error, loading, reload } = useAsync(() => api.alerts(status, level), [status, level]);
  useDashboardEvents((e) => {
    if (e.type.startsWith("alert.")) reload();
  });

  async function acknowledge(id: number) {
    await api.ackAlert(id);
    reload();
  }

  return (
    <div>
      <h1 className="text-2xl font-bold">{t("alerts")}</h1>
      <div className="my-3 flex flex-wrap gap-4">
        <label>
          {t("status")}{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border-2 p-1">
            <option value="all">{t("all")}</option>
            <option value="open">{t("status_open")}</option>
            <option value="acknowledged">{t("status_acknowledged")}</option>
            <option value="resolved">{t("status_resolved")}</option>
          </select>
        </label>
        <label>
          {t("level")}{" "}
          <select value={level} onChange={(e) => setLevel(e.target.value)} className="rounded border-2 p-1">
            <option value="">{t("all")}</option>
            <option value="red">{t("level_red")}</option>
            <option value="amber">{t("level_amber")}</option>
          </select>
        </label>
      </div>
      {loading && !data && <Loading />}
      {error && <ErrorBox message={error} onRetry={reload} />}
      {data && data.length === 0 && <Empty text={t("empty")} />}
      <ul className="space-y-3">
        {data?.map((a) => (
          <li key={a.id} className={`rounded border-2 p-3 ${a.level === "red" && a.status !== "resolved" ? "border-red-800" : ""}`}>
            <div className="flex flex-wrap items-center gap-3">
              <RiskBadge level={a.level} />
              {a.elder && (
                <Link to={`/elders/${a.elder_id}`} className="font-bold text-blue-900 underline">
                  {elderName(a.elder, lang)}
                </Link>
              )}
              <span className="text-sm text-gray-700">{t(`type_${a.type}` as StringKey)}</span>
              <span className="ml-auto font-semibold">{t(`status_${a.status}` as const)}</span>
            </div>
            <p className="mt-1 text-lg">{lang === "zh" ? a.reason_zh : a.reason_en}</p>
            <p className="text-sm text-gray-700">
              {formatHK(a.created_at)}
              {a.call_id && (
                <>
                  {" · "}
                  <Link to={`/calls/${a.call_id}`} className="underline">
                    #{a.call_id}
                  </Link>
                </>
              )}
              {a.outcome ? ` · ${t(`act_${a.outcome}` as StringKey)}` : ""}
            </p>
            {a.notes && <p className="mt-1 rounded bg-gray-50 p-2">{a.notes}</p>}
            {a.status !== "resolved" && resolving !== a.id && (
              <div className="mt-2 flex gap-2">
                {a.status === "open" && (
                  <button onClick={() => void acknowledge(a.id)} className="rounded bg-amber-700 px-4 py-2 font-semibold text-white">
                    {t("alerts_ack")}
                  </button>
                )}
                <button onClick={() => setResolving(a.id)} className="rounded bg-blue-900 px-4 py-2 font-semibold text-white">
                  {t("alerts_resolve")}
                </button>
              </div>
            )}
            {resolving === a.id && (
              <ResolveForm
                alertId={a.id}
                onCancel={() => setResolving(null)}
                onDone={() => {
                  setResolving(null);
                  reload();
                }}
              />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
