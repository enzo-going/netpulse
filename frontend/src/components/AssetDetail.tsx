import { getAssetDetails } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { formatLatency, kindLabel, relativeTime } from "../lib/format";
import { LatencyChart } from "./LatencyChart";
import { StatusBadge } from "./StatusBadge";
import "./AssetDetail.css";

const POLL_MS = 15_000;

interface AssetDetailProps {
  assetId: number;
  onClose: () => void;
}

export function AssetDetail({ assetId, onClose }: AssetDetailProps) {
  const { data, error, loading } = usePolling(() => getAssetDetails(assetId), POLL_MS);

  return (
    <section className="asset-detail" aria-labelledby="asset-detail-title">
      <button className="asset-detail__close" type="button" onClick={onClose}>
        Fechar ×
      </button>
      {loading && <p className="asset-detail__state">Carregando detalhes…</p>}
      {error && <p className="asset-detail__state is-error">{error.message}</p>}
      {data && (
        <>
          <div className="asset-detail__heading">
            <div>
              <p className="section-kicker">Detalhes do ativo</p>
              <h2 id="asset-detail-title">{data.asset.name}</h2>
              <p>
                <span className="mono">{data.asset.address}</span> · {kindLabel(data.asset.kind)}
                {data.asset.location ? ` · ${data.asset.location}` : ""}
              </p>
            </div>
            <StatusBadge status={data.asset.status} />
          </div>

          <div className="asset-detail__facts">
            <span>Sub-rede: {data.asset.subnet ?? "não identificada"}</span>
            <span>Latência atual: {formatLatency(data.asset.latency_ms)}</span>
            <span>Última coleta: {relativeTime(data.asset.last_seen)}</span>
          </div>

          <div className="asset-detail__checks">
            {data.asset.checks.map(({ check, latest }) => (
              <article className="check-card" key={check.id}>
                <div className="check-card__header">
                  <div>
                    <h3>{check.label}</h3>
                    <p>A cada {check.interval_seconds}s · timeout {check.timeout_seconds}s</p>
                  </div>
                  <StatusBadge status={latest?.status ?? "unknown"} />
                </div>
                {latest?.error && <p className="check-card__error">{latest.error}</p>}
                <LatencyChart
                  points={data.histories[check.id]?.points ?? []}
                  label={`${data.asset.name} / ${check.label}`}
                />
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
