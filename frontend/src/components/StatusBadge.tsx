import type { Status } from "../api/types";
import "./StatusBadge.css";

const LABEL: Record<Status, string> = {
  up: "No ar",
  degraded: "Degradado",
  down: "Fora do ar",
  unknown: "Desconhecido",
};

/**
 * Glifo por status, não só cor — daltonismo e telas monocromáticas de NOC
 * continuam distinguindo o estado. A cor reforça, o glifo e o texto carregam
 * o significado.
 */
const GLYPH: Record<Status, string> = {
  up: "●",
  degraded: "▲",
  down: "✕",
  unknown: "?",
};

interface StatusBadgeProps {
  status: Status;
  /** Grades densas usam só o glifo + cor; o texto completo fica no title. */
  compact?: boolean;
}

export function StatusBadge({ status, compact = false }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${status}`} title={LABEL[status]}>
      <span className="status-badge__glyph" aria-hidden="true">
        {GLYPH[status]}
      </span>
      {!compact && <span className="status-badge__label">{LABEL[status]}</span>}
      {compact && <span className="visually-hidden">{LABEL[status]}</span>}
    </span>
  );
}
