import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { ErrorBox, Loading } from "../components/States";
import { formatHK } from "../format";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";

/** Staff preview of the family digest: generate, read it exactly as family will, then mark it sent (simulated). */
export default function DigestPage() {
  const { id } = useParams();
  const elderId = Number(id);
  const { t } = useI18n();
  const { data, error, loading, reload } = useAsync(() => api.elderDigest(elderId), [elderId]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setProblem(null);
    try {
      await action();
      reload();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading && data === undefined) return <Loading />;
  if (error) return <ErrorBox message={error} onRetry={reload} />;

  return (
    <div>
      <Link to={`/elders/${elderId}`} className="text-blue-900 underline">← {t("back")}</Link>
      <h1 className="mt-2 text-2xl font-bold">{t("digest_title")}</h1>
      <p className="mt-1 text-gray-700">{t("digest_noteStaff")}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          disabled={busy}
          onClick={() => void run(() => api.generateDigest(elderId))}
          className="rounded bg-blue-900 px-4 py-2 font-semibold text-white disabled:opacity-60"
        >
          {t("digest_generate")}
        </button>
        {data && !data.sent_simulated_at && (
          <button
            disabled={busy}
            onClick={() => void run(() => api.sendDigest(data.id))}
            className="rounded bg-green-800 px-4 py-2 font-semibold text-white disabled:opacity-60"
          >
            {t("digest_send")}
          </button>
        )}
      </div>
      {problem && <p role="alert" className="mt-2 font-semibold text-red-800">{problem}</p>}
      {!data ? (
        <p className="mt-4 text-lg">{t("digest_none")}</p>
      ) : (
        <section className="mt-4">
          <p className="font-semibold">
            {t("digest_week", { d: data.week_start })} ·{" "}
            {data.sent_simulated_at ? `${t("digest_sent")}: ${formatHK(data.sent_simulated_at)}` : t("digest_draft")}
          </p>
          <div className="mt-2 grid gap-4 md:grid-cols-2">
            <article className="whitespace-pre-line rounded border-2 p-4 text-lg">{data.text_en}</article>
            <article className="whitespace-pre-line rounded border-2 p-4 text-lg">{data.text_zh}</article>
          </div>
        </section>
      )}
    </div>
  );
}
