/**
 * Espelha backend/netpulse/api/schemas.py e netpulse/models.py.
 *
 * Não é gerado automaticamente — se um schema do backend mudar de forma, este
 * arquivo precisa acompanhar. É pequeno de propósito: só os campos que a
 * grade de ativos consome hoje.
 */

export type Status = "up" | "degraded" | "down" | "unknown";
export type AssetKind =
  | "server"
  | "switch"
  | "router"
  | "firewall"
  | "printer"
  | "workstation"
  | "service"
  | "other";
export type CheckType = "ping" | "tcp" | "ssl" | "snmp";

export interface CheckRead {
  id: number;
  asset_id: number;
  type: CheckType;
  label: string;
  params: Record<string, unknown>;
  interval_seconds: number;
  timeout_seconds: number;
  enabled: boolean;
}

export interface CheckResultRead {
  id: number;
  check_id: number;
  ts: string;
  status: Status;
  latency_ms: number | null;
  detail: Record<string, unknown>;
  error: string | null;
}

export interface CheckStatusRead {
  check: CheckRead;
  latest: CheckResultRead | null;
}

export interface AssetStatusRead {
  id: number;
  name: string;
  address: string;
  kind: AssetKind;
  subnet: string | null;
  location: string | null;
  tags: string[];
  enabled: boolean;
  created_at: string;
  status: Status;
  latency_ms: number | null;
  last_seen: string | null;
  checks: CheckStatusRead[];
}

export interface OverviewRead {
  generated_at: string;
  total_assets: number;
  counts: Record<Status, number>;
  open_incidents: number;
  degraded_or_down: AssetStatusRead[];
}
