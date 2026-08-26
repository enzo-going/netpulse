import { AssetGrid } from "./components/AssetGrid";
import { OverviewBar } from "./components/OverviewBar";
import "./App.css";

export default function App() {
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
        <AssetGrid />
      </main>
    </div>
  );
}
