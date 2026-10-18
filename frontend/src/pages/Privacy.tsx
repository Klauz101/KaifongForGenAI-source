import { Link } from "react-router-dom";
import { useI18n } from "../i18n";
import privacy from "../i18n/privacy.json";

/** Public, plain-language privacy notice (PDPO DPP5). English and Cantonese; readable without signing in. */
export default function Privacy() {
  const { t, lang, setLang } = useI18n();
  const page = lang === "zh" ? privacy.zh : privacy.en;
  return (
    <main className="mx-auto max-w-2xl p-6 text-lg leading-relaxed">
      <div className="flex items-center justify-between">
        <Link to="/" className="text-blue-900 underline">← {t("privacy_back")}</Link>
        <button onClick={() => setLang(lang === "en" ? "zh" : "en")} className="rounded border-2 border-blue-900 px-3 py-1 font-semibold text-blue-900">
          {lang === "en" ? "繁體中文" : "English"}
        </button>
      </div>
      <h1 className="mt-4 text-3xl font-bold">{page.title}</h1>
      <p className="mt-1 text-sm font-semibold text-yellow-900">{t("syntheticBanner")}</p>
      {page.sections.map((s) => (
        <section key={s.h} className="mt-6">
          <h2 className="text-xl font-bold">{s.h}</h2>
          {s.p.map((para) => (
            <p key={para} className="mt-2">{para}</p>
          ))}
        </section>
      ))}
    </main>
  );
}
