import { useEffect, useState } from "react";
const clock = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true });
// This clock ticks without rerendering the dashboard or waiting for a data API.
export default function MarketClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const id = window.setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, []);
  return <span className="market-clock" title="Current time in New York. Quote and candle times are shown separately.">{clock.format(now)} ET</span>;
}
