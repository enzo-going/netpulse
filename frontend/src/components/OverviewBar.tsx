import { getOverview } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import "./OverviewBar.css";

const POLL_MS = 15_000;

interface TileSpec {
  label: string;
  value: number;
  tone: "good" | "warning" | "critical" | "unknown" | "accent";
}

export function OverviewBar() {
  const { data, error } = usePolling(getOverview, POLL_MS);

  if (error || !data) return null; // AssetGrid já mostra o erro; evita duplicar.

  const tiles: TileSpec[] = [
    { label: "Ativos", value: data.total_assets, tone: "accent" },
    { label: "No ar", value: data.counts.up ?? 0, tone: "good" },
    { label: "Degradados", value: data.counts.degraded ?? 0, tone: "warning" },
    { label: "Fora do ar", value: data.counts.down ?? 0, tone: "critical" },
    { label: "Incidentes abertos", value: data.open_incidents, tone: "critical" },
  ];

  return (
    <div className="overview-bar">
      {tiles.map((tile) => (
        <div key={tile.label} className={`overview-tile overview-tile--${tile.tone}`}>
          <span className="overview-tile__value">{tile.value}</span>
          <span className="overview-tile__label">{tile.label}</span>
        </div>
      ))}
    </div>
  );
}
