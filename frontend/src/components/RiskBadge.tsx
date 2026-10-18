import { AlertOctagon, CheckCircle2, CircleHelp, TriangleAlert } from "lucide-react";
import { useI18n } from "../i18n";
import type { Level } from "../api/types";

const STYLE = {
  red: { cls: "bg-red-800 text-white border-red-900", Icon: AlertOctagon },
  amber: { cls: "bg-amber-200 text-amber-950 border-amber-600", Icon: TriangleAlert },
  green: { cls: "bg-green-100 text-green-900 border-green-700", Icon: CheckCircle2 },
  none: { cls: "bg-gray-100 text-gray-900 border-gray-500", Icon: CircleHelp },
} as const;

/** Always icon + text, never colour alone. */
export function RiskBadge({ level }: { level: Level | null }) {
  const { t } = useI18n();
  const key = level ?? "none";
  const { cls, Icon } = STYLE[key];
  const label = t(`level_${key}` as const);
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border-2 px-3 py-1 text-base font-semibold ${cls}`}
      title={level === "green" ? t("greenTooltip") : undefined}
    >
      <Icon aria-hidden size={20} />
      {label}
    </span>
  );
}
