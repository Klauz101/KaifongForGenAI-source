import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useI18n } from "../i18n";
import type { TrendPoint } from "../api/types";

export function MoodChart({ points }: { points: TrendPoint[] }) {
  const { t } = useI18n();
  const data = points.map((p) => ({ date: p.date.slice(5), mood: p.status === "completed" ? p.mood ?? null : null }));
  return (
    <figure aria-label={t("mood")}>
      <figcaption className="mb-1 font-semibold">{t("mood")}</figcaption>
      <div className="h-48 w-full">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -20 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" interval={2} fontSize={12} />
            <YAxis domain={[1, 5]} ticks={[1, 2, 3, 4, 5]} fontSize={12} />
            <Tooltip />
            <Line type="monotone" dataKey="mood" stroke="#1d4ed8" strokeWidth={3} dot={{ r: 3 }} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

interface Cell {
  symbol: string;
  cls: string;
  label: string;
}

type Mapper = (p: TrendPoint) => Cell;
const GOOD = "bg-green-100 text-green-900 border-green-700";
const WARN = "bg-amber-200 text-amber-950 border-amber-600";
const BAD = "bg-red-100 text-red-900 border-red-700";
const NONE = "bg-gray-100 text-gray-700 border-gray-400";
const MISSED = "bg-gray-200 text-gray-800 border-gray-500";

const unknown: Cell = { symbol: "?", cls: NONE, label: "unknown" };
const missed: Cell = { symbol: "–", cls: MISSED, label: "no answer" };

export const appetiteCell: Mapper = (p) =>
  p.status !== "completed"
    ? missed
    : p.appetite === "normal"
      ? { symbol: "✓", cls: GOOD, label: "normal" }
      : p.appetite === "reduced"
        ? { symbol: "↓", cls: WARN, label: "reduced" }
        : p.appetite === "skipped_meals"
          ? { symbol: "✕", cls: BAD, label: "skipped meals" }
          : unknown;

export const outCell: Mapper = (p) =>
  p.status !== "completed"
    ? missed
    : p.went_out === "yes"
      ? { symbol: "✓", cls: GOOD, label: "went out" }
      : p.went_out === "no"
        ? { symbol: "✕", cls: BAD, label: "stayed in" }
        : unknown;

export const medsCell: Mapper = (p) =>
  p.status !== "completed"
    ? missed
    : p.meds === "yes"
      ? { symbol: "✓", cls: GOOD, label: "taken" }
      : p.meds === "partial"
        ? { symbol: "½", cls: WARN, label: "partly" }
        : p.meds === "no"
          ? { symbol: "✕", cls: BAD, label: "not taken" }
          : unknown;

/** Daily dot strip: symbol + colour so it never relies on colour alone. */
export function DotStrip({ title, points, map }: { title: string; points: TrendPoint[]; map: Mapper }) {
  return (
    <div>
      <div className="mb-1 font-semibold">{title}</div>
      <ol className="flex flex-wrap gap-1">
        {points.map((p) => {
          const c = map(p);
          return (
            <li
              key={p.call_id}
              className={`flex h-8 w-8 items-center justify-center rounded border text-sm font-bold ${c.cls}`}
              title={`${p.date}: ${c.label}`}
              aria-label={`${p.date}: ${c.label}`}
            >
              {c.symbol}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
