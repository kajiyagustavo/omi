# Omi Ingestão Curada + Kill-Switch + Tarjas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inverter o fluxo de ingestão do Omi self-host — desligar a extração automática de memórias/tarefas/grafo, criar uma skill de curadoria com humano no loop, e remover as tarjas de debug do app Flutter.

**Architecture:** Três frentes independentes. (A) Backend: env var gateia o bloco de extração derivada em `process_conversation()`, mantendo transcrição/conversas/daily-summary intactos. (B) Skill Sonnet `omi-ingest`: lê WhatsApp/email/conversas via MCP, classifica, confirma com o usuário, faz PUT nos endpoints REST do Omi com auth ID-token Firebase. (C) App Flutter: duas edições isoladas em `main.dart` removem as tarjas DEBUG e STAGING.

**Tech Stack:** Python 3.11 / FastAPI (backend), Docker Compose (Karla), Flutter (app), Markdown+JSON (skill), MCP WhatsApp+Gmail.

## Global Constraints

- **Branch:** todo o trabalho de código vai em `feat/airec-device` (nunca trocar de branch).
- **Backend formatting:** `black --line-length 120 --skip-string-normalization <files>`.
- **Backend imports:** no in-function imports — todos no topo do módulo.
- **Backend logging:** nunca logar dado sensível cru; usar `logger` já definido (`process_conversation.py:89`). UIDs podem ficar visíveis.
- **Dart formatting:** `dart format --line-length 120 <files>`.
- **App l10n:** strings de usuário via `context.l10n.keyName` (não aplicável aqui — só removemos código).
- **Commits:** individuais por arquivo; commit local por padrão (não pushar/abrir PR sem pedido explícito).
- **Env var name (verbatim):** `OMI_AUTO_EXTRACTION_ENABLED`, default `'true'`, desliga só com valor `'false'` (case-insensitive).
- **Deploy backend:** push local → `git pull` na Karla (`/opt/omi-airec/omi`) → `docker build -f ../Dockerfile.patched -t omi-backend:local .` → `docker compose up -d`. Backup do compose antes de editar.

---

## Frente C — App Flutter: remover tarjas (fazer primeiro: isolado, valor visível)

### Task C1: Desligar a fita DEBUG

**Files:**
- Modify: `app/lib/main.dart:345`

**Interfaces:**
- Consumes: nada.
- Produces: nada (mudança de comportamento visual apenas).

- [ ] **Step 1: Editar a linha do banner**

Trocar a linha 345:

```dart
            debugShowCheckedModeBanner: F.env == Environment.dev,
```

por:

```dart
            debugShowCheckedModeBanner: false,
```

- [ ] **Step 2: Formatar**

Run: `cd app && dart format --line-length 120 lib/main.dart`
Expected: `Formatted 1 file (0 changed)` ou `(1 changed)`.

- [ ] **Step 3: Verificar que compila (analyze)**

Run: `cd app && flutter analyze lib/main.dart`
Expected: nenhum erro novo introduzido por esta linha (warnings pré-existentes no arquivo são aceitáveis).

- [ ] **Step 4: Commit**

```bash
git add app/lib/main.dart
git commit -m "fix(app): remove Flutter DEBUG banner in dev builds"
```

### Task C2: Remover o banner STAGING

**Files:**
- Modify: `app/lib/main.dart:393-424` (o bloco `if (Env.isUsingStagingApi) { ... }`)

**Interfaces:**
- Consumes: nada.
- Produces: nada.

- [ ] **Step 1: Remover o bloco condicional**

No `builder` do `MaterialApp` (após o `ErrorWidget.builder = ...`), o trecho atual é:

```dart
              if (Env.isUsingStagingApi) {
                final topPadding = MediaQuery.of(context).padding.top;
                return Column(
                  children: [
                    GestureDetector(
                      onTap: () {
                        MyApp.navigatorKey.currentState?.push(
                          MaterialPageRoute(builder: (context) => const DeveloperSettingsPage()),
                        );
                      },
                      child: Container(
                        width: double.infinity,
                        padding: EdgeInsets.only(top: topPadding + 4, bottom: 4),
                        color: Colors.orange.shade800,
                        child: Text(
                          context.l10n.staging.toUpperCase(),
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            decoration: TextDecoration.none,
                          ),
                        ),
                      ),
                    ),
                    Expanded(
                      child: MediaQuery.removePadding(context: context, removeTop: true, child: child!),
                    ),
                  ],
                );
              }
              return child!;
```

Substituir TODO esse trecho (da linha `if (Env.isUsingStagingApi) {` até o `return child!;` final, inclusive) por apenas:

```dart
              return child!;
```

Resultado: o `builder` mantém `FlutterError.onError`, `ErrorWidget.builder`, e termina com `return child!;` — sem o banner laranja.

- [ ] **Step 2: Formatar**

Run: `cd app && dart format --line-length 120 lib/main.dart`
Expected: `(1 changed)`.

- [ ] **Step 3: Verificar imports órfãos**

`DeveloperSettingsPage` pode ficar sem uso após remover o bloco. Verificar:

Run: `cd app && grep -n "DeveloperSettingsPage" lib/main.dart`
Expected: se NÃO houver mais nenhuma ocorrência além do import, remover a linha de import correspondente (`import ...developer_settings_page.dart`). Se houver outras ocorrências, deixar o import.

Run: `cd app && flutter analyze lib/main.dart`
Expected: sem erro de import não usado e sem novo erro.

- [ ] **Step 4: Commit**

```bash
git add app/lib/main.dart
git commit -m "fix(app): remove STAGING banner overlay"
```

### Task C3: Verificar UI no app rodando

**Files:** nenhum (verificação).

**Interfaces:**
- Consumes: build dev do app com C1+C2 aplicados.
- Produces: evidência visual.

- [ ] **Step 1: Hot restart**

Run: `kill -SIGUSR2 $(pgrep -f "flutter run" | head -1)`
Expected: app reinicia. (Se não houver `flutter run` ativo, pular para Step 3 e buildar/rodar antes.)

- [ ] **Step 2: Reconectar o agente**

Run: `AGENT_FLUTTER_LOG=/tmp/flutter-run.log agent-flutter connect`
Expected: conecta.

- [ ] **Step 3: Screenshot de evidência**

Run: `agent-flutter screenshot /tmp/omi-no-banners.png`
Expected: imagem sem a fita vermelha "DEBUG" (canto sup. dir.) e sem o banner laranja "STAGING" (topo). Comparar visualmente.

- [ ] **Step 4: Sem commit** (verificação não altera código).

---

## Frente A — Backend: kill-switch da extração automática (fazer antes da skill)

### Task A1: Gatear o bloco de extração derivada com env var

**Files:**
- Modify: `backend/utils/conversations/process_conversation.py` (topo do módulo + bloco linhas 803-808)
- Test: `backend/tests/unit/test_auto_extraction_gate.py` (criar)

**Interfaces:**
- Consumes: `logger` (já definido em `process_conversation.py:89`), `critical_executor`, `_extract_memories`, `_extract_trends`, `_save_action_items`, `_update_goal_progress` (já no módulo).
- Produces: módulo-level `AUTO_EXTRACTION_ENABLED: bool` lido de `os.getenv('OMI_AUTO_EXTRACTION_ENABLED', 'true')`. Gate em torno das 4 submissões de extração derivada.

- [ ] **Step 1: Escrever o teste que falha**

Criar `backend/tests/unit/test_auto_extraction_gate.py`. O gate é uma função pura testável sem mockar o pipeline inteiro. Vamos extrair a decisão para uma função módulo-level e testá-la:

```python
import importlib
import os


def _reload_with_env(value):
    """Recarrega o módulo com OMI_AUTO_EXTRACTION_ENABLED setado para `value` (ou removido se None)."""
    if value is None:
        os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)
    else:
        os.environ['OMI_AUTO_EXTRACTION_ENABLED'] = value
    import backend.utils.conversations.process_conversation as pc
    importlib.reload(pc)
    return pc


def test_auto_extraction_enabled_by_default():
    pc = _reload_with_env(None)
    assert pc.auto_extraction_enabled() is True


def test_auto_extraction_disabled_when_false():
    pc = _reload_with_env('false')
    assert pc.auto_extraction_enabled() is False


def test_auto_extraction_disabled_case_insensitive():
    pc = _reload_with_env('FALSE')
    assert pc.auto_extraction_enabled() is False


def test_auto_extraction_enabled_for_other_values():
    pc = _reload_with_env('true')
    assert pc.auto_extraction_enabled() is True
    pc = _reload_with_env('yes')
    assert pc.auto_extraction_enabled() is True
```

Nota: `importlib.reload` em `process_conversation` re-executa o módulo. Como ele importa pesado, este teste roda em `tests/unit/` mas pode precisar das mesmas env vars de import que os outros unit tests (`ENCRYPTION_SECRET`). Se o reload falhar por dependência de import, ver Step 1b (fallback).

- [ ] **Step 1b (fallback se o reload for pesado demais): testar a lógica isolada**

Se o `importlib.reload` quebrar por deps de import no ambiente de teste, substituir o teste acima por um que importa só a função helper, mantendo-a livre de side-effects de import. A função `auto_extraction_enabled()` lê `os.getenv` em tempo de chamada (não no import), então o teste pode setar `os.environ` e chamar direto sem reload:

```python
import os
from backend.utils.conversations.process_conversation import auto_extraction_enabled


def test_default_true():
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)
    assert auto_extraction_enabled() is True


def test_false_disables():
    os.environ['OMI_AUTO_EXTRACTION_ENABLED'] = 'false'
    assert auto_extraction_enabled() is True if False else (auto_extraction_enabled() is False)
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)


def test_false_case_insensitive():
    os.environ['OMI_AUTO_EXTRACTION_ENABLED'] = 'FALSE'
    assert auto_extraction_enabled() is False
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)
```

Implementar a função para LER em tempo de chamada (Step 3 reflete isso), o que torna o fallback o caminho preferido — mais simples e sem reload.

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `cd backend && python -m pytest tests/unit/test_auto_extraction_gate.py -v`
Expected: FAIL com `ImportError: cannot import name 'auto_extraction_enabled'` (a função ainda não existe).

- [ ] **Step 3: Implementar a função helper + o gate**

Em `backend/utils/conversations/process_conversation.py`, logo após a definição do `logger` (linha 89), adicionar a função (lê em tempo de chamada, sem estado de import):

```python
def auto_extraction_enabled() -> bool:
    """Gate global da extração automática derivada (memórias/tarefas/trends/grafo).

    Desligável via env var OMI_AUTO_EXTRACTION_ENABLED='false' no self-host.
    Default ligado (preserva comportamento upstream). Transcrição, criação da
    conversa, título/overview e save_structured_vector NÃO são afetados.
    """
    return os.getenv('OMI_AUTO_EXTRACTION_ENABLED', 'true').strip().lower() != 'false'
```

Depois, no bloco de submissão (atualmente linhas 803-808):

```python
        if not is_reprocess:
            critical_executor.submit(save_structured_vector, uid, conversation)
        critical_executor.submit(_extract_memories, uid, conversation)
        critical_executor.submit(_extract_trends, uid, conversation)
        critical_executor.submit(_save_action_items, uid, conversation)
        critical_executor.submit(_update_goal_progress, uid, conversation)
```

substituir por:

```python
        if not is_reprocess:
            critical_executor.submit(save_structured_vector, uid, conversation)
        if auto_extraction_enabled():
            critical_executor.submit(_extract_memories, uid, conversation)
            critical_executor.submit(_extract_trends, uid, conversation)
            critical_executor.submit(_save_action_items, uid, conversation)
            critical_executor.submit(_update_goal_progress, uid, conversation)
        else:
            logger.info(
                f'auto-extraction disabled (OMI_AUTO_EXTRACTION_ENABLED=false); '
                f'skipping memories/trends/action-items/goals for uid={uid} conversation={conversation.id}'
            )
```

`save_structured_vector` permanece FORA do gate (mantém a conversa buscável).

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `cd backend && python -m pytest tests/unit/test_auto_extraction_gate.py -v`
Expected: PASS (todos os casos do Step 1b).

- [ ] **Step 5: Registrar o teste no test.sh**

`tests/unit/` é coletado pelo runner, mas confirmar que `test.sh` roda o diretório (não lista arquivo a arquivo):

Run: `cd backend && grep -nE "tests/unit|pytest" test.sh | head`
Expected: ver se há um glob `tests/unit/` ou se cada arquivo é listado. Se for lista explícita, adicionar `tests/unit/test_auto_extraction_gate.py`. Se for glob de diretório, nada a fazer.

- [ ] **Step 6: Formatar**

Run: `cd backend && black --line-length 120 --skip-string-normalization utils/conversations/process_conversation.py tests/unit/test_auto_extraction_gate.py`
Expected: arquivos formatados.

- [ ] **Step 7: Rodar o lint de async blockers (regra do backend)**

Run: `cd backend && python scripts/lint_async_blockers.py`
Expected: sem novas violações (não adicionamos `requests`/`time.sleep`/`Thread`).

- [ ] **Step 8: Commit (individual por arquivo)**

```bash
git add backend/utils/conversations/process_conversation.py
git commit -m "feat(backend): gate auto-extraction behind OMI_AUTO_EXTRACTION_ENABLED"
git add backend/tests/unit/test_auto_extraction_gate.py
git commit -m "test(backend): cover auto-extraction env gate"
```

### Task A2: Confirmar abrangência (pusher) e documentar a env var

**Files:**
- Modify: `backend/.env.template` (adicionar a var documentada)
- (verificação) `backend/pusher/` e caminho de produção da Karla

**Interfaces:**
- Consumes: a função `auto_extraction_enabled()` de A1.
- Produces: documentação da var + confirmação de que o gate cobre o caminho de produção.

- [ ] **Step 1: Verificar se o pusher chama process_conversation**

Run: `cd backend && grep -rnE "process_conversation\(|from utils.conversations.process_conversation" pusher/ routers/pusher.py`
Expected: identificar TODOS os call sites em produção. Se o pusher importa e chama `process_conversation()`, o gate de A1 JÁ o protege (o gate está dentro da função). Só é preciso garantir que a env var esteja no AMBIENTE do serviço pusher também (Task A3 cuida do compose).

- [ ] **Step 2: Verificar produtores fora de process_conversation**

Run: `cd backend && grep -rnE "_extract_memories|_save_action_items|extract_memories_from_text" pusher/`
Expected: se o pusher tiver um caminho de extração PRÓPRIO que NÃO passa por `process_conversation()`, anotar como gap e gatear igual (mesma função `auto_extraction_enabled()`). Se não houver, registrar "pusher extrai só via process_conversation — coberto".

- [ ] **Step 3: Documentar a env var no template**

Adicionar ao `backend/.env.template` (na seção de feature flags, ou ao fim):

```
# Quando 'false', desliga a extração automática derivada (memórias/tarefas/trends/grafo)
# ao processar conversas. Transcrição, criação da conversa e daily summary NÃO são afetados.
# Default ligado. Usado no self-host para curadoria manual via skill omi-ingest.
OMI_AUTO_EXTRACTION_ENABLED=true
```

- [ ] **Step 4: Commit**

```bash
git add backend/.env.template
git commit -m "docs(backend): document OMI_AUTO_EXTRACTION_ENABLED flag"
```

### Task A3: Deploy na Karla + validação E2E

**Files:**
- Modify (na Karla, via SSH): `/opt/omi-airec/docker-compose.yml`

**Interfaces:**
- Consumes: commits de A1/A2 na branch `feat/airec-device`.
- Produces: backend rodando com extração automática desligada; conversas/daily-summary intactos.

- [ ] **Step 1: Push local**

```bash
git push origin feat/airec-device
```
Expected: 2-3 commits subindo.

- [ ] **Step 2: Pull na Karla**

Via SSH na Karla: `cd /opt/omi-airec/omi && git pull`
Expected: traz os commits de A1/A2.

- [ ] **Step 3: Rebuild da imagem**

Na Karla: `cd /opt/omi-airec/omi && docker build -f ../Dockerfile.patched -t omi-backend:local .`
Expected: `BUILD_EXIT=0` (warning benigno `$LD_LIBRARY_PATH` é esperado).

- [ ] **Step 4: Backup + editar o compose**

Na Karla: backup `cp /opt/omi-airec/docker-compose.yml /opt/omi-airec/docker-compose.yml.bak-killswitch-20260624`.
Adicionar ao `environment:` do serviço `backend` (e do `pusher`, se A2 confirmou que ele extrai):

```yaml
      - OMI_AUTO_EXTRACTION_ENABLED=false
```

- [ ] **Step 5: Recriar os serviços**

Na Karla: `cd /opt/omi-airec && docker compose up -d backend` (e `pusher` se aplicável).
Expected: recria da imagem nova + lê a env var.

- [ ] **Step 6: Confirmar a var dentro do container**

Na Karla: `docker compose exec backend printenv OMI_AUTO_EXTRACTION_ENABLED`
Expected: `false`.

- [ ] **Step 7: Validação E2E — extração OFF, conversa preserva**

Forçar o processamento de uma conversa (ex.: `PUT /v1/conversations/{id}/reprocess` autenticado com ID token do dono, OU falar com o AIREC se conectado). Depois:
- Confirmar no log do backend a linha `auto-extraction disabled (OMI_AUTO_EXTRACTION_ENABLED=false)`.
- Confirmar no Firestore que a conversa EXISTE com título/overview/transcrição, mas NÃO surgiram memórias nem action items novos a partir dela.
Expected: conversa intacta, zero extração derivada.

- [ ] **Step 8: Validação E2E — daily summary intacto**

Confirmar que o serviço `notifications-cron` continua gerando daily summary (não foi tocado). Checar via `GET /v1/users/daily-summaries` autenticado, ou rodar o job manualmente: `docker compose exec notifications-cron python -m modal.job`.
Expected: daily summary gerado normalmente (job EXIT 0; "No users" fora da janela das 22h é esperado).

- [ ] **Step 9: Sanidade do default (não quebramos o caminho ligado)**

Teste rápido: temporariamente setar `OMI_AUTO_EXTRACTION_ENABLED=true` num container de teste (ou remover a var) e confirmar que a extração volta a rodar. Reverter para `false` depois.
Expected: com `true`/ausente, memórias/tarefas voltam a ser geradas.

- [ ] **Step 10: Sem commit de código** (mudança é só no compose da Karla, fora do repo; o backup `.bak-killswitch-20260624` documenta).

---

## Frente B — Skill `omi-ingest` (depende de Frente A no ar)

### Task B1: Esqueleto da skill + allowlist vazia

**Files:**
- Create: `~/.claude/skills/omi-ingest/SKILL.md`
- Create: `~/.claude/skills/omi-ingest/sources.json`

**Interfaces:**
- Consumes: nada.
- Produces: skill invocável + arquivo `sources.json` com o schema acordado.

- [ ] **Step 1: Criar `sources.json` com schema inicial vazio**

Conteúdo de `~/.claude/skills/omi-ingest/sources.json`:

```json
{
  "last_run": null,
  "whatsapp": [],
  "email": []
}
```

Campos por origem: `whatsapp[]` = `{id, nome, status, uteis, ruido}`; `email[]` = `{remetente, status, uteis, ruido}`. `status` ∈ `ativo|em_avaliacao|excluido`.

- [ ] **Step 2: Criar `SKILL.md` com frontmatter e o protocolo**

Conteúdo de `~/.claude/skills/omi-ingest/SKILL.md`:

````markdown
---
name: omi-ingest
description: Use quando o usuário quiser puxar conteúdo curado de WhatsApp, email e conversas do AIREC para dentro do Omi self-host. Lê fontes da allowlist, classifica em memória/tarefa/evento, confirma com o usuário e faz PUT nos endpoints do Omi. Triggers — "puxar fontes do omi", "omi-ingest", "ingerir no omi", "curar memórias".
---

# Omi Ingest — curadoria com humano no loop

Você opera o fluxo de ingestão CURADA do Omi self-host (Karla, `omi.elevare.club`).
Nada entra no Omi sem aprovação explícita do usuário. Você lê fontes de alto sinal,
classifica, PERGUNTA o que falta, o usuário confirma/completa, e só então você faz o PUT.

A extração automática do backend está DESLIGADA (`OMI_AUTO_EXTRACTION_ENABLED=false`),
então esta skill é a única fonte de memórias/tarefas/eventos.

## Allowlist (`sources.json`, ao lado deste arquivo)

- `status: ativo` → leio sempre. `em_avaliacao` → leio e avalio. `excluido` → ignoro.
- Contato/remetente NOVO (não listado) entra como `em_avaliacao` e eu aviso o usuário.
- Contadores `uteis`/`ruido` por origem subsidiam a sugestão fica/sai.
- **NUNCA removo sozinho.** Só marco `excluido` quando o usuário confirma.

## Protocolo de uma rodada

1. Read `sources.json`. Pega `last_run` e origens `ativo`+`em_avaliacao`.
2. Varre as fontes (janela = desde `last_run`; se `null`, pergunta a janela ao usuário):
   - WhatsApp via MCP `whatsapp` (ferramentas `whatsapp_listar_conversas`,
     `whatsapp_ler_mensagens`, `whatsapp_buscar_mensagens`) — só dos `id` da allowlist.
   - Email via MCP Gmail (`search_threads`, `get_thread`) — só dos remetentes da allowlist.
   - Conversas do AIREC: `GET /v1/conversations` (já transcritas; janela desde `last_run`).
3. Detecta origens novas → marca `em_avaliacao`, avisa o usuário.
4. Classifica cada item candidato: memória / tarefa / evento. Monta resumo curado por origem.
5. Apresenta ao usuário e PERGUNTA o que falta / pede confirmação. Ele ajusta/completa/descarta.
6. Faz PUT só do APROVADO (ver Endpoints). Auth = ID token Firebase no Bearer (ver Auth).
7. Atualiza `sources.json`: incrementa `uteis`/`ruido` por origem; grava novo `last_run` (ISO 8601 UTC).
8. Para origens `em_avaliacao` improdutivas (ex.: `ruido>=3 e uteis==0`), SUGERE remover.
   O usuário decide. Só então marca `excluido`.

## Endpoints (base `https://omi.elevare.club`)

| Tipo | Método/Path | Body |
|---|---|---|
| Memória | `POST /v3/memories` | `{"content": str, "tags": [str], "headline": str, "visibility": "private"}` |
| Tarefa | `POST /v1/action-items` | `{"description": str, "completed": false, "due_at": iso?, "priority": "high|medium|low"?, "parent_id": str?}` |
| Evento | `POST /v1/integrations/google_calendar/events` | título/dia/hora/local (ver schema no router) |
| Ler conversas | `GET /v1/conversations` | (paginação/janela) |

O backend força `category=manual`, `source=manual`, `manually_added=True` no `POST /v3/memories`.

## Auth (ID token Firebase)

Header `Authorization: Bearer <ID token>`. Mintar na Karla (mesma técnica das rodadas da web UI):
`get_user_by_email('gustavokjamjoy@gmail.com')` → custom token → ID token. Documentar o comando
exato usado na primeira execução e reusar. PEGADINHA conhecida: variáveis do browser
(`auth`/`api`) são escopo de módulo — validar persistência via o endpoint/token mint, não pelo console.

## Fora de escopo (fase 2)

- Transcrição de ligações telefônicas (fonte ainda inexistente).
- Interface gráfica de gestão da allowlist.
````

- [ ] **Step 3: Validar o JSON**

Run: `python3 -c "import json; json.load(open('$HOME/.claude/skills/omi-ingest/sources.json')); print('json ok')"`
Expected: `json ok`.

- [ ] **Step 4: Verificar que a skill é descoberta**

Reiniciar a sessão de skills não é possível inline; em vez disso confirmar a estrutura:
Run: `ls -la ~/.claude/skills/omi-ingest/`
Expected: `SKILL.md` e `sources.json` presentes. A skill aparece na próxima sessão / ao recarregar.

- [ ] **Step 5: Commit**

A skill mora em `~/.claude/skills/` (fora do repo omi). Versionar conforme a convenção do usuário para skills (geralmente git próprio em `~/.claude`). Se `~/.claude` for um repo git:

```bash
cd ~/.claude && git add skills/omi-ingest/ && git commit -m "feat(skill): omi-ingest curated ingestion skeleton"
```
Se não for repo, registrar a criação na memória via `/registrar-etapa` (não commitar no repo omi).

### Task B2: Documentar o mint de ID token (o ponto mais frágil)

**Files:**
- Modify: `~/.claude/skills/omi-ingest/SKILL.md` (preencher a seção Auth com o comando real)

**Interfaces:**
- Consumes: acesso SSH à Karla + Firebase Admin SDK no container backend.
- Produces: comando reproduzível de mint de ID token documentado na skill.

- [ ] **Step 1: Descobrir o caminho de mint dentro do container backend**

Na Karla, dentro do container backend (que tem Firebase Admin + `SERVICE_ACCOUNT_JSON`), montar o comando que: pega o uid por email, cria custom token, troca por ID token. Investigar a função existente:
Run (na Karla): `docker compose exec backend python -c "import firebase_admin; from firebase_admin import auth; print(auth.get_user_by_email.__doc__)"`
Expected: confirma que `get_user_by_email` está disponível. A troca custom→ID token usa a REST API do Identity Toolkit (`signInWithCustomToken`) com a Web API key do projeto.

- [ ] **Step 2: Registrar o comando exato no SKILL.md**

Substituir a frase genérica da seção Auth pelo procedimento concreto descoberto no Step 1 (uid, custom token, endpoint `signInWithCustomToken` + Web API key, onde a key vive). Incluir um snippet copiável.

- [ ] **Step 3: Validar o mint end-to-end**

Mintar um ID token e bater num endpoint de leitura:
Run: `curl -s -H "Authorization: Bearer <ID_TOKEN>" https://omi.elevare.club/v3/memories?limit=1 | head -c 200`
Expected: JSON de memória (não 401). Confirma que o token serve para os PUTs.

- [ ] **Step 4: Commit** (mesma convenção de B1 Step 5).

### Task B3: Primeira rodada real (dry-run controlado)

**Files:**
- Modify: `~/.claude/skills/omi-ingest/sources.json` (popular com as primeiras origens reais)

**Interfaces:**
- Consumes: skill B1 + auth B2 + Frente A no ar.
- Produces: allowlist semeada + prova de que o ciclo completo funciona (1 item curado entra no Omi).

- [ ] **Step 1: Semear a allowlist com 1-2 origens de alto sinal**

Com o usuário, escolher 1 contato WhatsApp e 1 remetente email reais e adicionar como `ativo` no `sources.json` (com `uteis:0, ruido:0`).

- [ ] **Step 2: Rodar o protocolo da skill com janela curta**

Invocar a skill com janela "últimas 24h". Ela varre só as origens semeadas, classifica, e apresenta o resumo curado.

- [ ] **Step 3: Curar e aprovar 1 item**

O usuário confirma/ajusta. Fazer o PUT de exatamente 1 item (ex.: 1 memória) via `POST /v3/memories`.
Expected: HTTP 200/201.

- [ ] **Step 4: Validar no Omi**

Confirmar na web UI (`https://omi.elevare.club/web/`, aba Memórias) ou via `GET /v3/memories?limit=1` que a memória curada apareceu com `manually_added=True`.
Expected: item presente.

- [ ] **Step 5: Confirmar que `sources.json` foi atualizado**

Run: `python3 -c "import json; d=json.load(open('$HOME/.claude/skills/omi-ingest/sources.json')); print(d['last_run'], d['whatsapp'], d['email'])"`
Expected: `last_run` preenchido (ISO 8601), contador `uteis` da origem que rendeu o item incrementado.

- [ ] **Step 6: Commit** (mesma convenção de B1 Step 5).

---

## Self-Review (preenchido)

**Spec coverage:**
- Frente A (kill-switch, env var, gate 805-808, daily-summary preservado, pusher, deploy) → Tasks A1, A2, A3. ✓
- Frente B (skill Sonnet, allowlist auto-aprendente, sources.json, 3 fontes incl. AIREC, classificação, confirmação, PUT, sugestão fica/sai, auth ID token) → Tasks B1, B2, B3. ✓
- Frente C (main.dart:345 DEBUG, 393-424 STAGING, verificação UI) → Tasks C1, C2, C3. ✓
- Ordem sugerida no spec (C → A → B) → refletida na ordem das frentes no plano. ✓
- Fora de escopo (transcrição de ligação, UI da allowlist) → documentado no SKILL.md. ✓

**Placeholder scan:** sem TBD/TODO; todo passo de código mostra o código; comandos têm output esperado. O único ponto deliberadamente descoberto-em-runtime é o comando exato de mint de token (B2), porque depende de inspecionar o container — e isso está estruturado como passos de descoberta + documentação, não como placeholder.

**Type consistency:** `auto_extraction_enabled()` usado igual em A1 (def) e A2 (consumo). Env var `OMI_AUTO_EXTRACTION_ENABLED` idêntica em A1/A2/A3. Schema de `sources.json` (`status/uteis/ruido`) idêntico em B1/B3. Endpoints idênticos entre spec e SKILL.md.
