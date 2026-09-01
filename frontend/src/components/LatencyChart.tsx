import type { HistoryPoint } from "../api/types";
import { formatLatency } from "../lib/format";
import "./LatencyChart.css";

interface LatencyChartProps {
  points: HistoryPoint[];
  label: string;
}

const WIDTH = 640;
const HEIGHT = 180;
const PAD_X = 12;
const PAD_Y = 16;

export function LatencyChart({ points, label }: LatencyChartProps) {
  const allMeasured = points.filter(
    (point): point is HistoryPoint & { latency_ms: number } => point.latency_ms !== null,
  );
  if (allMeasured.length < 2) {
    return <div className="latency-chart__empty">Histórico insuficiente para o gráfico.</div>;
  }

  // A API pode devolver ate mil pontos. Uma amostra uniforme de no maximo 240
  // preserva a forma da serie sem criar um SVG desnecessariamente pesado.
  const stride = Math.max(1, Math.ceil(allMeasured.length / 240));
  const measured = allMeasured.filter(
    (_point, index) => index % stride === 0 || index === allMeasured.length - 1,
  );

  const max = Math.max(...measured.map((point) => point.latency_ms), 1);
  const coordinates = measured.map((point, index) => {
    const x = PAD_X + (index / (measured.length - 1)) * (WIDTH - PAD_X * 2);
    const y = HEIGHT - PAD_Y - (point.latency_ms / max) * (HEIGHT - PAD_Y * 2);
    return { x, y, point };
  });
  const path = coordinates.map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const latest = measured.at(-1)!;

  return (
    <div className="latency-chart">
      <div className="latency-chart__summary">
        <span>Última: {formatLatency(latest.latency_ms)}</span>
        <span>Pico: {formatLatency(max)}</span>
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Latência de ${label} nas últimas 24 horas`}
      >
        <line x1={PAD_X} y1={HEIGHT - PAD_Y} x2={WIDTH - PAD_X} y2={HEIGHT - PAD_Y} />
        <polyline points={path} />
        {coordinates
          .filter(({ point }, index) => {
            const previous = coordinates[index - 1]?.point.status;
            return point.status === "down" || (previous !== undefined && point.status !== previous);
          })
          .map(({ x, y, point }) => (
            <circle key={`${point.ts}-${x}`} cx={x} cy={y} r="4" className={`is-${point.status}`}>
              <title>{`${new Date(point.ts).toLocaleString("pt-BR")}: ${formatLatency(point.latency_ms)}`}</title>
            </circle>
          ))}
      </svg>
    </div>
  );
}
