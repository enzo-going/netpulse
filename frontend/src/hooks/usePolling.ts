import { useCallback, useEffect, useRef, useState } from "react";

interface PollingState<T> {
  data: T | null;
  error: Error | null;
  /** true só na primeira carga — evita piscar loading a cada atualização automática. */
  loading: boolean;
  refresh: () => void;
}

/**
 * Busca `fetcher` uma vez e depois a cada `intervalMs`. Um monitor de rede
 * mentiroso (dado parado na tela) é pior que um monitor lento — por isso
 * atualização automática é o padrão, não uma opção.
 *
 * O intervalo pausa quando a aba fica oculta e retoma ao voltar, em vez de
 * acumular chamadas perdidas.
 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  // Guarda o fetcher numa ref para que o intervalo nao seja recriado a cada
  // render quando o chamador passa uma funcao inline. A atribuicao fica num
  // efeito, nao no corpo do componente: mutar ref durante o render nao e
  // seguro sob renderizacao concorrente.
  //
  // Este efeito e declarado antes do efeito de polling de proposito — na
  // montagem ele roda primeiro, entao a ref ja aponta para o fetcher atual
  // quando a primeira busca dispara.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const run = useCallback(async () => {
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    run();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") run();
    }, intervalMs);

    const onVisible = () => {
      if (document.visibilityState === "visible") run();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [run, intervalMs]);

  return { data, error, loading, refresh: run };
}
