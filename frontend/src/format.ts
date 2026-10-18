import type { ElderBrief, Reason } from "./api/types";
import type { Lang } from "./i18n/strings";

const dt = new Intl.DateTimeFormat("en-HK", {
  timeZone: "Asia/Hong_Kong",
  dateStyle: "medium",
  timeStyle: "short",
});

/** Timestamps are UTC in the API; always show Hong Kong time. */
export function formatHK(iso: string | null | undefined): string {
  return iso ? dt.format(new Date(iso)) : "—";
}

export function elderName(e: Pick<ElderBrief, "display_name" | "display_name_en">, lang: Lang): string {
  return lang === "zh" ? e.display_name : `${e.display_name_en} ${e.display_name === e.display_name_en ? "" : e.display_name}`.trim();
}

export function reasonText(r: Pick<Reason, "text_en" | "text_zh">, lang: Lang): string {
  return lang === "zh" ? r.text_zh : r.text_en;
}
