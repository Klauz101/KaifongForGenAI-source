import { useI18n } from "../i18n";

export function Loading() {
  const { t } = useI18n();
  return <p role="status" className="p-6 text-lg">{t("loading")}</p>;
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <div role="alert" className="m-4 rounded border-2 border-red-800 bg-red-50 p-4 text-red-900">
      <p className="font-semibold">{t("error")}</p>
      <p>{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="mt-2 rounded bg-red-800 px-3 py-1 text-white">
          {t("retry")}
        </button>
      )}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <p className="p-6 text-lg text-gray-700">{text}</p>;
}
