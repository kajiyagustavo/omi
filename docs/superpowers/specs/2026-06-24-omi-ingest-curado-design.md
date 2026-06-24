# Omi — Ingestão Curada + Kill-Switch da Extração Automática + Remoção das Tarjas de Debug

**Data:** 2026-06-24
**Projeto:** pessoal / omi-core (self-host na Karla)
**Branch:** `feat/airec-device`

## Problema

A ingestão automática indiscriminada do Omi (toda conversa transcrita gera memórias,
tarefas, trends e knowledge graph via LLM) produz ruído: o conteúdo gerado não é
satisfatório, mistura sinal com lixo, e não há curadoria humana. O usuário quer inverter
o fluxo: **nada entra no Omi automaticamente; memórias, tarefas e grafos passam a vir de
um fluxo curado com humano no loop**, lendo fontes de alto sinal (WhatsApp, email, e as
próprias conversas do AIREC já transcritas).

Duas frentes secundárias acopladas: (a) desligar a extração automática quando o fluxo
curado estiver ativo; (b) remover as tarjas de debug/staging do app Flutter.

## Decisões tomadas (com o usuário)

- **Onde a skill roda:** aqui no chat, sob demanda (NÃO é daemon 24/7). Operada por Sonnet.
- **Fluxo:** eu trago o conteúdo + contexto, classifico, pergunto o que falta, o usuário
  completa/confirma, eu faço o PUT no Omi. Curadoria, não ingestão cega.
- **Output:** memória / tarefa / evento de agenda. Eu decido o tipo, o usuário confirma.
- **Seleção de fontes:** allowlist de contatos WhatsApp + remetentes de email, auto-aprendente
  (contato novo entra como `em_avaliacao`; eu sugiro fica/sai; o usuário decide; nunca removo
  sozinho).
- **Persistência da allowlist:** arquivo JSON na skill (estruturado, legível por uma UI futura).
- **Transcrições de ligação:** fora de escopo agora (fase 2). Gancho documentado, não implementado.
- **Nível de kill-switch:** Nível 1 — para SÓ a extração derivada (memórias/tarefas/grafo/trends);
  **transcrição e aba Conversas permanecem intactas** (matéria-prima curável).
- **Daily summary:** PERMANECE (é gerado por cron separado, não tocado pelo gate).
- **Controle do kill-switch:** env var global no compose da Karla (instância single-user).

---

## Frente A — Backend: kill-switch da extração automática

### Ponto de controle
`backend/utils/conversations/process_conversation.py`, função `process_conversation()`,
bloco de submissão ao `critical_executor` (linhas 805-808 confirmadas):

```python
critical_executor.submit(_extract_memories, uid, conversation)   # 805
critical_executor.submit(_extract_trends, uid, conversation)     # 806
critical_executor.submit(_save_action_items, uid, conversation)  # 807
critical_executor.submit(_update_goal_progress, uid, conversation) # 808
```

A transcrição, criação da conversa, título/overview e `save_structured_vector` (linha 804)
acontecem ANTES deste bloco e ficam intactos. O knowledge graph é gerado DENTRO de
`_extract_memories` (confirmado no CLAUDE.md do backend), então gatear memórias já mata o KG.

### Mudança
Adicionar gate por env var, default `true` (preserva o comportamento upstream; quem não
setar a var não percebe diferença):

```python
# topo do módulo (imports já têm `os`? confirmar — senão adicionar no topo, regra "no in-function imports")
AUTO_EXTRACTION_ENABLED = os.getenv('OMI_AUTO_EXTRACTION_ENABLED', 'true').lower() != 'false'
```

No bloco:

```python
if AUTO_EXTRACTION_ENABLED:
    critical_executor.submit(_extract_memories, uid, conversation)
    critical_executor.submit(_extract_trends, uid, conversation)
    critical_executor.submit(_save_action_items, uid, conversation)
    critical_executor.submit(_update_goal_progress, uid, conversation)
else:
    # fire-and-forget drop deve ser logado (regra do backend: "silent fire-and-forget drops")
    print(f'[process_conversation] auto-extraction disabled, skipping derived extraction uid={uid}')
```

`save_structured_vector` (804) fica FORA do gate — é indexação da conversa em si, não
extração derivada, e mantém a conversa buscável.

### Deploy
Mesmo fluxo já praticado (atividade 21/06):
1. Commit local na branch `feat/airec-device`.
2. `git push` → `git pull` na Karla (`/opt/omi-airec/omi`).
3. `docker build -f ../Dockerfile.patched -t omi-backend:local .`
4. Setar `OMI_AUTO_EXTRACTION_ENABLED=false` no `environment:` do serviço `backend`
   (e `pusher`, se o pusher também roda extração — VER nota abaixo) no
   `/opt/omi-airec/docker-compose.yml` (backup `.bak-killswitch-<data>`).
5. `docker compose up -d backend` (recria da imagem nova + lê a env var).

### Nota — pusher
O CLAUDE.md do backend diz que o pusher "runs LLM-powered conversation analysis (memories,
action items, insights)". O código de extração é COMPARTILHADO (mesmos módulos `utils/`).
**Tarefa de verificação no plano:** confirmar se o caminho de produção da Karla dispara
`process_conversation()` pelo backend, pelo pusher, ou ambos — e garantir que a env var
chegue a todos os serviços que importam esse módulo. O gate na função protege todos os
chamadores, mas a env var precisa estar setada no ambiente de cada serviço que o executa.

### Validação E2E
1. Com a var `false`: gerar/forçar uma conversa → confirmar no Firestore que NÃO surgiram
   memórias nem action items novos, mas a conversa EXISTE (título/overview/transcrição).
2. Confirmar que o daily summary do cron continua sendo gerado (não foi afetado).
3. Reverter a var pra `true` num teste de fumaça e confirmar que a extração volta (não
   quebramos o caminho default).

---

## Frente B — Skill `omi-ingest` (curadoria com humano no loop)

### Natureza
Skill operada por Sonnet, invocada sob demanda (`/omi-ingest` ou linguagem natural tipo
"puxa as fontes do Omi"). NÃO é serviço; roda no chat, com o usuário no loop.

### Artefatos
- `~/.claude/skills/omi-ingest/SKILL.md` — instruções da skill.
- `~/.claude/skills/omi-ingest/sources.json` — allowlist + estado de aprendizado.
- (opcional, fase futura) um helper de mint do ID token documentado na skill.

### `sources.json` (formato)
```json
{
  "last_run": "2026-06-24T12:00:00Z",
  "whatsapp": [
    {"id": "5585999999999", "nome": "Maria", "status": "ativo", "uteis": 7, "ruido": 1}
  ],
  "email": [
    {"remetente": "joao@jamjoy.com", "status": "ativo", "uteis": 3, "ruido": 0},
    {"remetente": "newsletter@x.com", "status": "em_avaliacao", "uteis": 0, "ruido": 5}
  ]
}
```
`status`: `ativo` | `em_avaliacao` | `excluido`. `uteis`/`ruido`: contadores acumulados
para subsidiar a sugestão fica/sai.

### Fluxo de uma rodada
1. **Lê** `sources.json`; pega `last_run` e a lista de origens `ativo` + `em_avaliacao`.
2. **Varre** as fontes via MCPs já conectados nesta sessão (WhatsApp + Gmail), filtrando
   pelas origens da allowlist e por janela de tempo desde `last_run`.
3. **AIREC (3ª fonte):** lê conversas novas do Omi via `GET /v1/conversations` desde `last_run`
   (já transcritas; o kill-switch parou só a extração, não a transcrição) como matéria-prima.
4. **Detecta origens novas** (contato/remetente não visto) → entra como `em_avaliacao`; avisa o usuário.
5. **Classifica** cada item candidato: memória / tarefa / evento. Monta um resumo curado por
   origem ("do email da Maria: 1 tarefa 'enviar proposta', 1 memória 'ela prefere PIX'").
6. **Pergunta** o que falta / pede confirmação. O usuário ajusta, completa ou descarta.
7. **PUT** só do aprovado, com auth = ID token Firebase no Bearer (mesmo da web UI):
   - Memória → `POST /v3/memories` — body `{content, category?, visibility, tags, headline}`
     (o backend força `category=manual`, `source=manual`, `manually_added=True`).
   - Tarefa → `POST /v1/action-items` — body `{description, completed, due_at?, priority?, parent_id?}`.
   - Evento → `POST /v1/integrations/google_calendar/events` (já integrado, 7ª rodada).
8. **Atualiza** `sources.json`: incrementa `uteis`/`ruido` por origem; grava novo `last_run`.
9. **Sugere fica/sai** para origens `em_avaliacao` improdutivas → o usuário decide. A skill
   NUNCA remove sozinha (só marca `excluido` quando o usuário confirma).

### Endpoints (confirmados no código)
| Tipo | Método/Path | Body principal |
|---|---|---|
| Memória | `POST /v3/memories` | `content`, `tags`, `headline`, `visibility` |
| Tarefa | `POST /v1/action-items` | `description`, `due_at`, `priority`, `parent_id` |
| Evento | `POST /v1/integrations/google_calendar/events` | título/dia/hora/local |
| Ler conversas | `GET /v1/conversations` | (janela de tempo) |

Auth: `Authorization: Bearer <ID token Firebase>`. Mint via `get_user_by_email` +
custom→ID token na Karla (mesma pegadinha documentada nas rodadas anteriores: `auth`/`api`
são escopo de módulo no browser; validação de persistência via token mint, não pelo console).

### Por que resolve a ingestão indiscriminada
- Nada entra sem aprovação explícita do usuário.
- Ruído filtrado em duas camadas: allowlist (origem) + confirmação item a item.
- A allowlist aprende quais fontes valem a pena (contadores `uteis`/`ruido` + decisão humana).

### Fora de escopo (fase 2, gancho documentado)
- Transcrição de ligações telefônicas (fonte ainda inexistente).
- Interface gráfica de gestão da allowlist (o JSON estruturado já viabiliza, mas não será
  construído agora).

---

## Frente C — App Flutter: remover tarjas de debug/staging

`app/lib/main.dart`:
- **Linha 345:** `debugShowCheckedModeBanner: F.env == Environment.dev` → `false`
  (mata a fita diagonal vermelha "DEBUG").
- **Linhas 393-424:** bloco do banner laranja "STAGING" (`if (Env.isUsingStagingApi)`) →
  remover/desativar, preservando o layout (o banner está num `Column`/`Expanded`; ao remover,
  garantir que o child Expanded continue renderizando a árvore normal).

Risco baixo, edições isoladas. Verificar UI via `agent-flutter` após a mudança (regra do
CLAUDE.md: verificar programaticamente, não só hot restart). Build dev não deve mais mostrar
nenhuma das duas tarjas.

---

## Ordem de implementação sugerida

1. **Frente C (tarjas)** — menor, isolada, sem dependência. Entrega rápida de valor visível.
2. **Frente A (kill-switch)** — backend + rebuild. Precisa estar no ar ANTES de a skill virar
   a única fonte, senão a extração automática continua poluindo em paralelo.
3. **Frente B (skill)** — depende de A estar ativo para fazer sentido (senão duplica
   memórias/tarefas com o pipeline automático).

## Riscos / pontos de atenção

- **Verificação pusher vs backend** (Frente A): garantir que a env var cobre todo caminho
  de produção. Bloqueador se o pusher extrair por fora e não receber a var.
- **Reversibilidade:** o kill-switch é uma env var — `true` restaura tudo. Nenhuma deleção
  de dado. Conversas antigas e suas memórias/tarefas pré-existentes ficam intactas.
- **Mint de ID token na skill:** o fluxo de auth precisa estar documentado e reproduzível;
  é o ponto mais frágil da skill (já mapeado nas rodadas anteriores).
- **Rebuild da imagem:** Frente A exige o ciclo push→pull→build→up que já foi feito 3x;
  custo conhecido (~minutos, liblc3).
