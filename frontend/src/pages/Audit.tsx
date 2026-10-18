import { useState } from "react";
import { api } from "../api/client";
import { ErrorBox, Loading } from "../components/States";
import { formatHK } from "../format";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";

/** Admin-only audit viewer: ids and actions, never content. */
export default function Audit() {
  const { t } = useI18n();
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useAsync(() => api.audit(action, entity, offset), [action, entity, offset]);

  return (
    <div>
      <h1 className="text-2xl font-bold">{t("audit_title")}</h1>
      <p className="text-gray-700">{t("audit_note")}</p>
      <div className="my-3 flex flex-wrap gap-4">
        <label>
          {t("audit_filterAction")}{" "}
          <input value={action} onChange={(e) => { setOffset(0); setAction(e.target.value); }} placeholder="read_transcript" className="rounded border-2 p-1" />
        </label>
        <label>
          {t("audit_filterEntity")}{" "}
          <select value={entity} onChange={(e) => { setOffset(0); setEntity(e.target.value); }} className="rounded border-2 p-1">
            <option value="">{t("all")}</option>
            {["call", "alert", "elder", "digest", "user", "system"].map((x) => (
              <option key={x} value={x}>{x}</option>
            ))}
          </select>
        </label>
      </div>
      {loading && !data && <Loading />}
      {error && <ErrorBox message={error} onRetry={reload} />}
      {data && (
        <>
          <p className="mb-2 font-semibold">{t("audit_total", { n: data.total })}</p>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b-2">
                <th className="p-1">{t("audit_when")}</th>
                <th className="p-1">{t("audit_who")}</th>
                <th className="p-1">{t("audit_action")}</th>
                <th className="p-1">{t("audit_record")}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((a) => (
                <tr key={a.id} className="border-b">
                  <td className="p-1">{formatHK(a.at)}</td>
                  <td className="p-1">{a.user}</td>
                  <td className="p-1 font-mono">{a.action}</td>
                  <td className="p-1">{a.entity}{a.entity_id != null ? ` #${a.entity_id}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 flex gap-2">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))} className="rounded border-2 px-3 py-1 disabled:opacity-40">
              {t("audit_prev")}
            </button>
            <button disabled={offset + 50 >= data.total} onClick={() => setOffset(offset + 50)} className="rounded border-2 px-3 py-1 disabled:opacity-40">
              {t("audit_next")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
