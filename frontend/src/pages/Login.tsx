import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { useI18n } from "../i18n";

export default function Login() {
  const { login } = useAuth();
  const { t, lang, setLang } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch {
      setError(t("loginFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-md p-6">
      <h1 className="text-3xl font-bold">{t("appName")}</h1>
      <p className="mt-1 text-sm font-semibold text-yellow-900">{t("syntheticBanner")}</p>
      <form onSubmit={submit} className="mt-6 space-y-4">
        <label className="block text-lg">
          {t("email")}
          <input
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded border-2 border-gray-600 p-2"
          />
        </label>
        <label className="block text-lg">
          {t("password")}
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded border-2 border-gray-600 p-2"
          />
        </label>
        {error && <p role="alert" className="font-semibold text-red-800">{error}</p>}
        <button disabled={busy} className="w-full rounded bg-blue-900 p-3 text-lg font-semibold text-white disabled:opacity-60">
          {t("signIn")}
        </button>
      </form>
      <p className="mt-4 text-sm text-gray-700">{t("demoHint")}</p>
      <p className="mt-2 text-sm"><a href="/privacy" className="text-blue-900 underline">{t("privacy_link")}</a></p>
      <button onClick={() => setLang(lang === "en" ? "zh" : "en")} className="mt-4 underline">
        {lang === "en" ? "繁體中文" : "English"}
      </button>
    </main>
  );
}
