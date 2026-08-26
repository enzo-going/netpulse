/** Tempo relativo curto em pt-BR: "12s atrás", "3min atrás", "nunca". */
export function relativeTime(iso: string | null): string {
  if (!iso) return "nunca";
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffSec = Math.round(diffMs / 1000);

  if (diffSec < 5) return "agora";
  if (diffSec < 60) return `${diffSec}s atrás`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}min atrás`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h atrás`;
  const diffDay = Math.round(diffHour / 24);
  return `${diffDay}d atrás`;
}

export function formatLatency(ms: number | null): string {
  if (ms === null) return "—";
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

/** Espelha AssetKind em netpulse/models.py — um tipo sem entrada aqui cairia
    na tela com o valor cru em inglês. */
const KIND_LABEL: Record<string, string> = {
  server: "Servidor",
  switch: "Switch",
  router: "Roteador",
  firewall: "Firewall",
  printer: "Impressora",
  workstation: "Estação",
  service: "Serviço",
  other: "Outro",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}
