import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { AlertToasts } from "./AlertToast";
import { ConnectionBanner } from "./ConnectionBanner";
import { useI18n } from "../i18n";

export function Layout() {
  const { user, logout } = useAuth();
  const { lang, setLang, t } = useI18n();
  const staff = user?.role === "admin" || user?.role === "social_worker";
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded px-3 py-2 text-lg font-semibold ${isActive ? "bg-blue-900 text-white" : "text-blue-900 hover:bg-blue-100"}`;

  return (
    <div className="min-h-screen bg-white text-gray-900">
      {staff && <AlertToasts />}
      {staff && <ConnectionBanner />}
      <div className="bg-yellow-200 px-4 py-1 text-center text-sm font-semibold text-yellow-950">
        {t("syntheticBanner")}
      </div>
      <header className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
        <Link to="/" className="mr-4 text-2xl font-bold">
          {t("appName")}
        </Link>
        {staff && (
          <nav className="flex gap-1" aria-label="Main">
            <NavLink to="/" end className={link}>{t("nav_today")}</NavLink>
            <NavLink to="/elders" className={link}>{t("nav_elders")}</NavLink>
            <NavLink to="/alerts" className={link}>{t("nav_alerts")}</NavLink>
            {user?.role === "admin" && (
              <NavLink to="/metrics" className={link}>{t("nav_metrics")}</NavLink>
            )}
            {user?.role === "admin" && (
              <NavLink to="/audit" className={link}>{t("nav_audit")}</NavLink>
            )}
            {user?.role === "admin" && (
              <NavLink to="/demo" className={link}>{t("nav_demo")}</NavLink>
            )}
          </nav>
        )}
        <div className="ml-auto flex items-center gap-3">
          <button
            onClick={() => setLang(lang === "en" ? "zh" : "en")}
            className="rounded border-2 border-blue-900 px-3 py-1 font-semibold text-blue-900"
            aria-label="Switch language"
          >
            {lang === "en" ? "繁體中文" : "English"}
          </button>
          <span className="hidden text-sm sm:inline">{user?.name}</span>
          <button onClick={() => void logout()} className="rounded bg-gray-800 px-3 py-1 text-white">
            {t("signOut")}
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl p-4">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-5xl p-4 text-sm">
        <Link to="/privacy" className="text-blue-900 underline">{t("privacy_link")}</Link>
      </footer>
    </div>
  );
}
