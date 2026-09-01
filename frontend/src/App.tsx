import { useState } from "react";
import { AssetDetail } from "./components/AssetDetail";
import { AssetGrid } from "./components/AssetGrid";
import { IncidentTimeline } from "./components/IncidentTimeline";
import { OverviewBar } from "./components/OverviewBar";
import "./App.css";

export default function App() {
  const [selectedAsset, setSelectedAsset] = useState<number | null>(null);

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1 className="app__title">NetPulse</h1>
          <p className="app__subtitle">Estado do parque de ativos, atualizado a cada 15s.</p>
        </div>
        <a
          className="app__repo-link"
          href="https://github.com/enzo-going/netpulse"
          target="_blank"
          rel="noreferrer"
        >
          Repositório ↗
        </a>
      </header>

      <main className="app__main">
        <OverviewBar />
        <IncidentTimeline />
        {selectedAsset !== null && (
          <AssetDetail
            key={selectedAsset}
            assetId={selectedAsset}
            onClose={() => setSelectedAsset(null)}
          />
        )}
        <div className="section-heading">
          <div>
            <p className="section-kicker">Inventário monitorado</p>
            <h2>Ativos</h2>
          </div>
        </div>
        <AssetGrid onSelect={setSelectedAsset} />
      </main>
    </div>
  );
}
