import { useEffect, useState } from "react";
import { subscribeStatus } from "../dashboardEvents";
import { useI18n } from "../i18n";

/** Tells staff when live updates (alerts, triage) have dropped, so they do not trust a frozen screen. */
export function ConnectionBanner() {
  const { t } = useI18n();
  const [connected, setConnected] = useState(true);
  const [seenConnected, setSeen] = useState(false);
  useEffect(
    () =>
      subscribeStatus((c) => {
        setConnected(c);
        if (c) setSeen(true);
      }),
    [],
  );
  if (connected || !seenConnected) return null; // only after we were connected once, to avoid a flash at start-up
  return (
    <div role="status" className="bg-orange-200 px-4 py-1 text-center text-sm font-semibold text-orange-950">
      {t("conn_reconnecting")}
    </div>
  );
}
