# Dashboard do NetPulse

Interface React que consome a API descrita no [README da raiz](../README.md).

## Desenvolver

A API precisa estar no ar primeiro — o dashboard não tem dados próprios:

```bash
cd ../backend
netpulse seed     # parque sintético + histórico, só na primeira vez
netpulse serve    # sobe a API em 127.0.0.1:8000
```

Em outro terminal:

```bash
npm install
npm run dev       # http://localhost:5173
```

O `vite.config.ts` faz proxy de `/api` para `127.0.0.1:8000`, então não é preciso
configurar variável de ambiente para desenvolver. Em produção o build é servido
pela própria API, na mesma origem.

## Build

```bash
npm run build     # tsc -b && vite build, saída em dist/
```

## Estrutura

```
src/
├── api/          # client.ts (fetch tipado) e types.ts (espelha os schemas do backend)
├── components/   # AssetGrid, OverviewBar, StatusBadge — cada um com seu CSS
├── hooks/        # usePolling: atualização automática, pausada em aba oculta
└── lib/          # formatação de latência, tempo relativo e rótulos
```

`src/api/types.ts` **não é gerado**: se um schema do backend mudar de forma, esse
arquivo precisa acompanhar à mão.

## Cores de estado

A paleta de status é fixa e validada para contraste sobre as superfícies escuras
do painel (`good` 5.75:1, `warning` 10.52:1, `critical` 4.02:1 contra `--panel`).
Nenhum estado é comunicado só por cor: toda badge leva glifo (`●`, `▲`, `✕`, `?`)
e rótulo textual, para não depender de visão de cor.
