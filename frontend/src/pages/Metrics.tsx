import { Bar, BarChart, CartesianGrid, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import { Empty, ErrorBox, Loading } from "../components/States";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";

export default function Metrics() {
  const { t } = useI18n();
  const { data, error, loading, reload } = useAsync(api.latency, [], 15_000);
  const cost = useAsync(api.cost, [], 15_000).data;
  const evalState = useAsync(api.evaluation, []).data;
  const evaluation = evalState?.results ?? null;
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return null;
  const { stats, providers, turns, target_ms } = data;
  const rows = turns.map((x, i) => ({
    n: i + 1,
    stt: x.stt_ms ?? 0,
    llm: x.llm_ms ?? 0,
    tts: x.tts_ms ?? 0,
  }));

  return (
    <div>
      <h1 className="text-2xl font-bold">{t("metrics_title")}</h1>
      <p className="mt-1 text-gray-700">{t("metrics_note")}</p>
      {evalState && (
        <section className="mt-4 rounded border-2 p-3">
          <h2 className="font-bold">{t("eval_title")}</h2>
          {!evaluation ? (
            <p className="mt-1">{t("eval_none")}</p>
          ) : (
            <>
              <p className="mt-1 text-sm text-gray-700">{t("eval_synthetic")}</p>
              <ul className="mt-2 text-lg">
                {evaluation.targets.map((g) => (
                  <li key={g.name}>
                    {g.name}: <strong>{g.value}</strong> ({t("eval_target")} {g.target}) ·{" "}
                    {g.met === true ? t("eval_met") : g.met === false ? t("eval_notMet") : t("eval_notMeasurable")}
                  </li>
                ))}
                <li>
                  {t("eval_triage")}: <strong>{evaluation.triage.summary}</strong>
                  {!evaluation.triage.agreed_by_team && ` · ${t("eval_notAgreed")}`}
                </li>
                <li>
                  {t("eval_stt")}: <strong>{evaluation.stt.cer_pct === null ? t("eval_notMeasured") : `${evaluation.stt.cer_pct}% (${evaluation.stt.clips})`}</strong>
                </li>
              </ul>
              <p role="note" className="mt-2 rounded bg-yellow-100 p-2 font-semibold text-yellow-950">
                {t("eval_stretchWarn", { a: evaluation.redflags.stretch_recall_pct })}
              </p>
            </>
          )}
        </section>
      )}
      {cost && (
        <section className="mt-4 rounded border-2 p-3">
          <h2 className="font-bold">{t("cost_title")}</h2>
          {cost.status === "ok" ? (
            <ul className="mt-1 text-lg">
              <li>{t("cost_perCall")}: <strong>{cost.currency} {cost.avg_cost_per_call?.toFixed(4)}</strong></li>
              <li>{t("cost_perMonth", { n: cost.calls_per_month })}: <strong>{cost.currency} {cost.est_monthly_per_person?.toFixed(2)}</strong></li>
            </ul>
          ) : (
            <p className="mt-1 font-semibold">{t("cost_notMeasured")}</p>
          )}
          <p className="text-sm text-gray-700">
            {t("cost_calls")}: {cost.calls_measured}
            {cost.missing_prices.length > 0 && ` · ${t("cost_missing")}: ${cost.missing_prices.join(", ")}`}
          </p>
        </section>
      )}
      <section className="mt-4">
        <h2 className="font-bold">{t("metrics_providers")}</h2>
        <p>{Object.entries(providers).map(([k, v]) => `${k}=${v}`).join(", ")}</p>
      </section>
      {stats.count === 0 ? (
        <Empty text={t("metrics_none")} />
      ) : (
        <>
          <p className="mt-4 text-xl">
            {t("metrics_median")}: <strong>{stats.median_ms} ms</strong> · p90 {stats.p90_ms} ms · n={stats.count} ·{" "}
            {t("metrics_target")}: &lt; {target_ms} ms{" "}
            {stats.median_ms !== null && stats.median_ms < target_ms ? "✓" : "✕"}
          </p>
          <h2 className="mt-4 font-bold">{t("metrics_turns")}</h2>
          <div className="h-64 w-full">
            <ResponsiveContainer>
              <BarChart data={rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="n" />
                <YAxis />
                <Tooltip />
                <Legend />
                <ReferenceLine y={target_ms} stroke="#b91c1c" strokeDasharray="4 4" />
                <Bar dataKey="stt" stackId="a" fill="#1d4ed8" name="STT" />
                <Bar dataKey="llm" stackId="a" fill="#047857" name="LLM" />
                <Bar dataKey="tts" stackId="a" fill="#b45309" name="TTS" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
}
