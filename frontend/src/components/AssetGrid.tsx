import { useMemo, useState } from "react";
import { listAssets } from "../api/client";
import type { AssetStatusRead, Status } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { formatLatency, kindLabel, relativeTime } from "../lib/format";
import { StatusBadge } from "./StatusBadge";
import "./AssetGrid.css";

const POLL_MS = 15_000;

const FILTERS: { value: Status | "all"; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "down", label: "Fora do ar" },
  { value: "degraded", label: "Degradado" },
  { value: "up", label: "No ar" },
  { value: "unknown", label: "Desconhecido" },
];

export function AssetGrid() {
  const [busca, setBusca] = useState("");
  const [filtro, setFiltro] = useState<Status | "all">("all");

  // A busca acontece no cliente sobre o retorno completo do polling, não como
  // outra chamada por tecla — o parque de demonstração tem 20 ativos; a API
  // já filtra por `busca` e `estado` para redes maiores, mas o cliente não
  // precisa disso na escala atual.
  const { data, error, loading } = usePolling(() => listAssets(), POLL_MS);

  const filtered = useMemo(() => {
    if (!data) return [];
    const termo = busca.trim().toLowerCase();
    return data.filter((asset) => {
      if (filtro !== "all" && asset.status !== filtro) return false;
      if (!termo) return true;
      return (
        asset.name.toLowerCase().includes(termo) ||
        asset.address.toLowerCase().includes(termo) ||
        (asset.location ?? "").toLowerCase().includes(termo)
      );
    });
  }, [data, busca, filtro]);

  const counts = useMemo(() => {
    const base: Record<Status, number> = { up: 0, degraded: 0, down: 0, unknown: 0 };
    for (const asset of data ?? []) base[asset.status]++;
    return base;
  }, [data]);

  if (loading) {
    return <div className="asset-grid__state">Carregando ativos…</div>;
  }

  if (error) {
    return (
      <div className="asset-grid__state asset-grid__state--error">
        <p>{error.message}</p>
        <p className="asset-grid__hint">
          A API precisa estar no ar. Rode <code>netpulse serve</code> a partir de{" "}
          <code>backend/</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="asset-grid">
      <div className="asset-grid__toolbar">
        <input
          type="search"
          placeholder="Buscar por nome, endereço ou local…"
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
          className="asset-grid__search"
          aria-label="Buscar ativos"
        />
        <div className="asset-grid__filters" role="group" aria-label="Filtrar por estado">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              className={`asset-grid__filter ${filtro === f.value ? "is-active" : ""}`}
              onClick={() => setFiltro(f.value)}
            >
              {f.label}
              {f.value !== "all" && counts[f.value] > 0 && (
                <span className="asset-grid__filter-count">{counts[f.value]}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="asset-grid__state">
          {data && data.length > 0
            ? "Nenhum ativo corresponde ao filtro."
            : "Nenhum ativo cadastrado ainda."}
        </div>
      ) : (
        <div className="asset-grid__table-wrap">
          <table className="asset-grid__table">
            <thead>
              <tr>
                <th>Ativo</th>
                <th>Endereço</th>
                <th>Tipo</th>
                <th>Estado</th>
                <th>Latência</th>
                <th>Visto</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((asset) => (
                <AssetRow key={asset.id} asset={asset} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AssetRow({ asset }: { asset: AssetStatusRead }) {
  return (
    <tr className={`asset-row asset-row--${asset.status}`}>
      <td>
        <div className="asset-row__name">{asset.name}</div>
        {asset.location && <div className="asset-row__location">{asset.location}</div>}
      </td>
      <td className="mono asset-row__address">{asset.address}</td>
      <td>
        <span className="asset-row__kind">{kindLabel(asset.kind)}</span>
      </td>
      <td>
        <StatusBadge status={asset.status} />
      </td>
      <td className="mono">{formatLatency(asset.latency_ms)}</td>
      <td className="asset-row__seen">{relativeTime(asset.last_seen)}</td>
    </tr>
  );
}
