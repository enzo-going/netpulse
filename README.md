# NetPulse

[![CI](https://github.com/enzo-going/netpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/enzo-going/netpulse/actions/workflows/ci.yml)

Monitoramento de ativos de rede com correlação de falhas e análise de incidentes assistida por IA.

Um monitor comum trata cada host como um problema isolado: quando o uplink de uma
filial cai, ele dispara um alerta por equipamento e enterra o operador em ruído.
O NetPulse agrupa falhas simultâneas da mesma sub-rede em um único incidente e
pede ao modelo uma hipótese de causa a partir do contexto — o histórico do ativo,
o que caiu junto e o que continuou de pé.

> **Status:** em construção. O núcleo de coleta está pronto e testado; a API, o
> dashboard e a camada de IA são os próximos passos — veja o [roteiro](#roteiro).

## Como rodar

O modo demo simula um parque de 20 ativos e **não toca em nenhuma rede**. Não
precisa de inventário, credencial nem VPN.

```bash
python -m venv .venv && . .venv/Scripts/activate   # Linux/macOS: . .venv/bin/activate
pip install -e "backend[dev]"
cd backend
netpulse seed     # cria o parque sintético com 24h de histórico
netpulse run      # executa um ciclo de coleta
netpulse status   # mostra o estado atual
netpulse watch    # coleta continuamente
netpulse serve    # sobe a API em http://127.0.0.1:8000
```

O `seed` já gera a série histórica das últimas 24 horas, senão o sistema abriria
com um único ponto por check e nada para olhar. O histórico é **reproduzível**:
cada resultado é derivado de `endereço:minuto`, então é exatamente o que a coleta
real teria registrado naquele minuto — incluindo a queda roteirizada da filial.
Ajuste com `--horas N`, ou desligue com `--horas 0`.

Para monitorar uma rede de verdade, copie `.env.example` para `.env` e troque
`NETPULSE_MODE` para `live`.

## API

Com `netpulse serve` no ar, a documentação interativa fica em
[`/docs`](http://127.0.0.1:8000/docs) — gerada do próprio código, sem
manutenção paralela.

| Rota | O que faz |
|---|---|
| `GET /api/overview` | Resumo do parque: contadores por estado e a lista do que está com problema, do pior para o melhor |
| `GET /api/assets` | Ativos com o estado consolidado; filtra por `estado`, `subnet` e `busca` |
| `POST /api/assets` | Cria um ativo, opcionalmente já com seus checks |
| `PATCH /api/assets/{id}` | Atualiza; trocar o endereço recalcula a sub-rede |
| `POST /api/assets/{id}/checks` | Adiciona um check |
| `GET /api/checks/{id}/history` | Série histórica para o gráfico de latência |
| `GET /api/incidents` | Incidentes abertos e resolvidos |

Dois detalhes de implementação que o `GET /api/overview` esconde:

- **O estado de um ativo é o pior dos seus checks.** Um host que responde ao ping
  mas perdeu a porta do serviço precisa aparecer como problema, não como sucesso
  parcial.
- **Uma consulta, não uma por check.** O último resultado de cada check sai de um
  `ROW_NUMBER() OVER (PARTITION BY check_id)` — a tela inteira custa duas
  consultas, independente do tamanho do parque.

## O que ele coleta

| Check | O que mede | Observações |
|---|---|---|
| `ping` | Alcançabilidade e latência ICMP | Usa o `ping` do sistema, não socket raw — roda sem privilégio de administrador |
| `tcp` | Porta aberta e tempo de handshake | Exige o parâmetro `port` |
| `ssl` | Validade da cadeia e dias até o vencimento | Certificado perto de vencer já entra como degradado |
| `snmp` | GET em um OID | Dependência opcional: `pip install -e "backend[snmp]"` |

Qualquer check aceita `degraded_above_ms`: respondeu, mas devagar, vira
`degraded` em vez de `up`. É o aviso que costuma anteceder a queda.

## Arquitetura

```
coletor (asyncio)  →  SQLite (WAL)  →  API (FastAPI)  →  dashboard (React)
       ↑                                     ↓
  4 tipos de check              motor de incidentes → análise por IA
```

O coletor e a API são processos independentes que só se falam pelo banco: a
coleta continua rodando se a API cair, e vice-versa.

Decisões que valem explicação:

- **A sessão do banco nunca fica aberta durante a rede.** O coletor lê o que
  vencer, fecha a sessão, dispara os checks em paralelo e só então reabre para
  gravar — transações curtas, que é o que o SQLite prefere.
- **Um laço só, não um agendador por ativo.** Cada check guarda o próprio
  intervalo; o laço acorda a cada tick e roda o que venceu.
- **Um check quebrado não derruba o ciclo.** Exceção inesperada vira um resultado
  `unknown` gravado, não uma coleta perdida.
- **A IA é opcional.** Sem `ANTHROPIC_API_KEY`, tudo funciona igual e apenas o
  parecer do incidente não é gerado.

## Roteiro

- [x] Modelo de dados, 4 tipos de check, coletor assíncrono e agendador
- [x] Modo demo com parque sintético e CLI (`seed`, `run`, `watch`, `status`)
- [x] API REST com série histórica, resumo do parque e leitura de incidentes
- [x] Histórico sintético reproduzível no `seed`, para a demo abrir com dados
- [ ] Motor de incidentes com correlação por sub-rede
- [ ] Dashboard React (grade de ativos, latência, linha do tempo)
- [ ] Análise de incidente por IA
- [ ] `docker compose up` para a demo

## Desenvolvimento

```bash
cd backend
ruff check . && ruff format --check . && pytest -q
```

A CI roda os três em Python 3.11, 3.12 e 3.13.

## Sobre os dados

Todos os endereços do modo demo vêm das faixas reservadas para documentação da
[RFC 5737](https://datatracker.ietf.org/doc/html/rfc5737) (`192.0.2.0/24`,
`198.51.100.0/24`, `203.0.113.0/24`) — nenhum deles roteia para uma rede real. O
diretório `data/` é ignorado pelo Git: inventário de produção, IPs e números de
série nunca chegam ao repositório.

## Licença

[MIT](LICENSE).
