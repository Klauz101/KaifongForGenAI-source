import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ElderListItem } from "../api/types";
import { Empty, ErrorBox, Loading } from "../components/States";
import { RiskBadge } from "../components/RiskBadge";
import { elderName } from "../format";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";

export default function Elders() {
  const { t, lang } = useI18n();
  const { data, error, loading, reload } = useAsync(api.elders, []);
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  const rows = (data ?? []) as ElderListItem[];
  if (rows.length === 0) return <Empty text={t("emptyCaseload")} />;
  return (
    <table className="w-full text-left text-lg">
      <thead>
        <tr className="border-b-2">
          <th className="p-2">{t("nav_elders")}</th>
          <th className="p-2">{t("age")}</th>
          <th className="p-2">{t("district")}</th>
          <th className="p-2">{t("level")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((e) => (
          <tr key={e.id} className="border-b">
            <td className="p-2">
              <Link className="font-semibold text-blue-900 underline" to={`/elders/${e.id}`}>
                {elderName(e, lang)}
              </Link>
            </td>
            <td className="p-2">{e.age}</td>
            <td className="p-2">{e.district}</td>
            <td className="p-2"><RiskBadge level={e.level} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
