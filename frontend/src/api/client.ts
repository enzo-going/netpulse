import type { AssetStatusRead, OverviewRead, Status } from "./types";

/**
 * Em produção o dashboard é servido pela mesma origem da API (ver README —
 * `netpulse serve` monta o build estático), então a base fica vazia. Em
 * desenvolvimento, o Vite roda em outra porta e precisa do host completo; ver
 * vite.config.ts para a origem usada.
 */
const BASE_URL = import.meta.env.VITE_API_URL ?? "";

export class ApiError extends Error {
  // Campo declarado explicitamente: o atalho de propriedade no construtor gera
  // codigo em tempo de execucao, e o projeto compila com erasableSyntaxOnly.
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`);
  } catch {
    throw new ApiError("Não foi possível falar com a API do NetPulse.", 0);
  }

  if (!response.ok) {
    throw new ApiError(`A API respondeu ${response.status} em ${path}.`, response.status);
  }

  return (await response.json()) as T;
}

export interface AssetListParams {
  estado?: Status;
  subnet?: string;
  busca?: string;
}

export function listAssets(params: AssetListParams = {}): Promise<AssetStatusRead[]> {
  const query = new URLSearchParams();
  if (params.estado) query.set("estado", params.estado);
  if (params.subnet) query.set("subnet", params.subnet);
  if (params.busca) query.set("busca", params.busca);

  const qs = query.toString();
  return request<AssetStatusRead[]>(`/api/assets${qs ? `?${qs}` : ""}`);
}

export function getOverview(): Promise<OverviewRead> {
  return request<OverviewRead>("/api/overview");
}
