import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { DotStrip, MoodChart, appetiteCell, medsCell, outCell } from "../components/TrendChart";
import { ErrorBox, Loading } from "../components/States";
import { ConsentPanel } from "../components/ConsentPanel";
import { RiskBadge } from "../components/RiskBadge";
import { elderName, formatHK, reasonText } from "../format";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";
import type { StringKey } from "../i18n/strings";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="mb-2 border-b-2 pb-1 text-xl font-bold">{title}</h2>
      {children}
    </section>
  );
}

const LEVEL_SYMBOL = { red: "R", amber: "A", green: "G" } as const;
const LEVEL_CLS = {
  red: "bg-red-800 text-white",
  amber: "bg-amber-200 text-amber-950",
  green: "bg-green-100 text-green-900",
} as const;

export default function ElderDetail() {
  const { id } = useParams();
  const elderId = Number(id);
  const { t, lang } = useI18n();
  const { data, error, loading, reload } = useAsync(() => api.elder(elderId), [elderId]);

  if (loading) return <Loading />;
  if (error || !data) return <ErrorBox message={error ?? "Not found"} onRetry={reload} />;
  const { elder, consent, risk } = data;
  const respLabel: Record<string, StringKey> = {
    interested: "nudgeAccepted",
    declined: "nudgeDeclined",
    unclear: "nudgeUnclear",
  };

  async function toggle(factId: number, active: boolean) {
    await api.setMemoryActive(elderId, factId, active);
    reload();
  }

  return (
    <div>
      <Link to="/" className="text-blue-900 underline">← {t("back")}</Link>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-3xl font-bold">{elderName(elder, lang)}</h1>
        <RiskBadge level={risk.level} />
      </div>
      <Link to={`/elders/${elderId}/digest`} className="mr-2 mt-2 inline-block rounded border-2 border-blue-900 px-4 py-2 font-semibold text-blue-900">
        {t("digest_open")}
      </Link>
      <Link to={`/simulator/${elderId}`} className="mt-2 inline-block rounded bg-green-800 px-4 py-2 font-semibold text-white">
        {t("sim_open")}
      </Link>
      <p className="mt-1 text-gray-700">
        {elder.age} · {elder.gender} · {elder.district} · {elder.interests.join(", ")}
      </p>

      <Section title={t("whyFlagged")}>
        {risk.reasons.length === 0 ? (
          <p>{t("noReasons")}</p>
        ) : (
          <ul className="list-disc pl-6 text-lg">
            {risk.reasons.map((r) => (
              <li key={r.code}>
                {reasonText(r, lang)}{" "}
                {r.evidence_call_ids.slice(0, 3).map((cid) => (
                  <Link key={cid} to={`/calls/${cid}`} className="mr-1 text-sm text-blue-900 underline">
                    #{cid}
                  </Link>
                ))}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("trends")}>
        <div className="space-y-4">
          <MoodChart points={data.trends} />
          <DotStrip title={t("appetite")} points={data.trends} map={appetiteCell} />
          <DotStrip title={t("outings")} points={data.trends} map={outCell} />
          <DotStrip title={t("meds")} points={data.trends} map={medsCell} />
        </div>
      </Section>

      <Section title={t("riskHistory")}>
        <ol className="flex flex-wrap gap-1">
          {data.risk_history.map((r) => (
            <li
              key={r.date}
              title={`${r.date}: ${t(`level_${r.level}` as const)}`}
              aria-label={`${r.date}: ${t(`level_${r.level}` as const)}`}
              className={`flex h-8 w-8 items-center justify-center rounded text-sm font-bold ${LEVEL_CLS[r.level]}`}
            >
              {LEVEL_SYMBOL[r.level]}
            </li>
          ))}
        </ol>
      </Section>

      <Section title={t("memory")}>
        <ul className="space-y-1">
          {data.memory_facts.map((f) => (
            <li key={f.id} className={`flex items-center gap-3 ${f.active ? "" : "text-gray-500 line-through"}`}>
              <span>{lang === "zh" ? f.fact_yue : f.fact}</span>
              <button onClick={() => void toggle(f.id, !f.active)} className="ml-auto rounded border px-2 py-1 text-sm">
                {f.active ? t("deactivate") : t("activate")}
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <Section title={t("alerts")}>
        {data.alerts.length === 0 ? <p>{t("empty")}</p> : (
          <ul className="space-y-1">
            {data.alerts.map((a) => (
              <li key={a.id}>
                <RiskBadge level={a.level} /> {lang === "zh" ? a.reason_zh : a.reason_en} ·{" "}
                {t(`status_${a.status}` as const)} · {formatHK(a.created_at)}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("nudges")}>
        <p className="mb-2 font-semibold">
          {t("connection_title")}: {t("connection_summary", { s: data.connection.suggested, a: data.connection.accepted, f: data.connection.followed_up })}
        </p>
        {data.nudges.length === 0 ? <p>{t("empty")}</p> : (
          <ul className="space-y-1">
            {data.nudges.map((n) => (
              <li key={n.id}>
                {n.date} — {lang === "zh" ? n.text_yue : n.text_en}
                {n.resource && (
                  <span className="text-sm text-gray-700">
                    {" "}
                    ({n.resource}
                    {n.resource_verified === false ? `, ${t("connection_resourceUnverified")}` : ""})
                  </span>
                )}{" "}
                →{" "}
                <strong>{t(respLabel[n.elder_response] ?? "nudgeUnclear")}</strong>
                {n.elder_response === "interested" && (
                  <> · {n.followed_up ? t("followedUp") : t("notFollowedUp")}</>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("actions")}>
        {data.actions.length === 0 ? <p>{t("empty")}</p> : (
          <ul className="space-y-1">
            {data.actions.map((a) => (
              <li key={a.id}>
                {formatHK(a.created_at)} — <strong>{a.action.replace(/_/g, " ")}</strong>
                {a.notes ? `: ${a.notes}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("calls")}>
        <ul className="space-y-2">
          {data.calls.map((c) => (
            <li key={c.id}>
              <Link to={`/calls/${c.id}`} className="font-semibold text-blue-900 underline">
                {formatHK(c.started_at)}
              </Link>{" "}
              {c.status === "no_answer" ? `(${t("noAnswer")})` : `· ${Math.round(c.duration_s / 60)} min`}
              {c.summary_en && <div className="text-gray-700">{lang === "zh" ? c.summary_yue : c.summary_en}</div>}
            </li>
          ))}
        </ul>
      </Section>

      <Section title={t("consent_panel")}>
        <ConsentPanel elderId={elderId} onChanged={reload} />
      </Section>

      <Section title={t("profile")}>
        <p>{elder.full_name} · {elder.phone} · {elder.preferred_call_time}</p>
        {elder.care_notes && <p className="text-gray-700">{elder.care_notes}</p>}
        {consent && (
          <p className="mt-1">
            {t("consent")}: {consent.method.replace(/_/g, " ")} · {t("consentedOn")} {formatHK(consent.consented_at)} ·{" "}
            {t("witnessedBy")} {consent.witnessed_by}
          </p>
        )}
      </Section>
    </div>
  );
}
