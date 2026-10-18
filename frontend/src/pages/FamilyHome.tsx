import { api } from "../api/client";
import { ErrorBox, Loading } from "../components/States";
import { formatHK } from "../format";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";

/** Family accounts see only the weekly digest text. No transcripts, no call data, ever. */
export default function FamilyHome() {
  const { t, lang } = useI18n();
  const { data, error, loading, reload } = useAsync(api.familyDigest, []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  return (
    <div>
      <h1 className="text-2xl font-bold">{t("digest_title")}</h1>
      {!data || data.length === 0 ? (
        <p className="mt-3 text-lg text-gray-700">{t("digest_familyNone")}</p>
      ) : (
        data.map((d) => (
          <section key={d.elder.display_name_en} className="mt-4">
            <h2 className="text-xl font-semibold">
              {lang === "zh" ? d.elder.display_name : d.elder.display_name_en}
            </h2>
            <p className="text-sm text-gray-700">
              {t("digest_week", { d: d.week_start })}
              {d.sent_simulated_at ? ` · ${t("digest_sent")} ${formatHK(d.sent_simulated_at)}` : ""}
            </p>
            <div className="mt-2 grid gap-4 md:grid-cols-2">
              <article className="whitespace-pre-line rounded border-2 p-4 text-lg">{d.text_en}</article>
              <article className="whitespace-pre-line rounded border-2 p-4 text-lg">{d.text_zh}</article>
            </div>
          </section>
        ))
      )}
      <p className="mt-4 text-gray-700">{t("familyNote")}</p>
    </div>
  );
}
