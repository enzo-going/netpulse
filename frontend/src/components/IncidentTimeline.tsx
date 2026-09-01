import { useState } from "react";
import { analyzeIncident, getHealth, listIncidents } from "../api/client";
import type { IncidentRead } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { relativeTime } from "../lib/format";
import "./IncidentTimeline.css";

const POLL_MS = 15_000;

async function loadTimeline() {
  const [incidents, health] = await Promise.all([listIncidents(), getHealth()]);
  return { incidents, health };
}

export function IncidentTimeline() {
  const { data, error, refresh } = usePolling(loadTimeline, POLL_MS);
  const [analyzing, setAnalyzing] = useState<number | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  async function requestAnalysis(incident: IncidentRead) {
    setAnalyzing(incident.id);
    setAnalysisError(null);
    try {
      await analyzeIncident(incident.id);
      refresh();
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : String(err));
    } finally {
      setAnalyzing(null);
    }
  }

  if (error) return null; // A grade ja comunica indisponibilidade da API.

  return (
    <section className="incident-timeline" aria-labelledby="incidents-title">
      <div className="section-heading">
        <div>
          <p className="section-kicker">Correlação automática</p>
          <h2 id="incidents-title">Linha do tempo de incidentes</h2>
        </div>
        <span className="incident-timeline__mode">modo {data?.health.mode ?? "…"}</span>
      </div>

      {analysisError && <p className="incident-timeline__error">{analysisError}</p>}
      {!data ? (
        <div className="incident-timeline__empty">Carregando incidentes…</div>
      ) : data.incidents.length === 0 ? (
        <div className="incident-timeline__empty">
          Nenhum incidente confirmado. Falhas transitórias abaixo do limiar não aparecem aqui.
        </div>
      ) : (
        <div className="incident-timeline__list">
          {data.incidents.map((incident) => (
            <article
              key={incident.id}
              className={`incident-card incident-card--${incident.severity}`}
            >
              <div className="incident-card__rail" aria-hidden="true" />
              <div className="incident-card__content">
                <div className="incident-card__header">
                  <div>
                    <div className="incident-card__meta">
                      <span className={`incident-card__status is-${incident.status}`}>
                        {incident.status === "open" ? "Aberto" : "Resolvido"}
                      </span>
                      <time dateTime={incident.opened_at}>{relativeTime(incident.opened_at)}</time>
                    </div>
                    <h3>{incident.title}</h3>
                  </div>
                  <span className="incident-card__count">
                    {new Set(incident.members.map((member) => member.asset_id)).size} ativo(s)
                  </span>
                </div>

                <div className="incident-card__members">
                  {incident.members.map((member) => (
                    <span key={`${member.check_id}-${member.first_failure_at}`}>
                      {member.asset_name} · {member.check_label}
                      {member.recovered_at ? " ✓" : ""}
                    </span>
                  ))}
                </div>

                {incident.analysis && (
                  <div className="incident-card__analysis">
                    <strong>Parecer assistido por IA</strong>
                    <p>{incident.analysis}</p>
                    <small>
                      {incident.analysis_model} · {relativeTime(incident.analysis_at)}
                    </small>
                  </div>
                )}

                {data.health.ai_enabled && !incident.analysis && (
                  <div className="incident-card__ai-action">
                    <button
                      type="button"
                      disabled={analyzing === incident.id}
                      onClick={() => requestAnalysis(incident)}
                    >
                      {analyzing === incident.id ? "Analisando…" : "Gerar parecer com IA"}
                    </button>
                    <small>Envia sub-rede, nomes, localizações, checks e erros deste incidente.</small>
                  </div>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
