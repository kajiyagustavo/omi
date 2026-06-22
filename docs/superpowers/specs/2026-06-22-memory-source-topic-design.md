# Design — Dois marcadores na memória: proveniência (`source`) + assunto (`topic`)

**Data:** 2026-06-22
**Branch:** `feat/airec-device` (fork Omi self-hosted, projeto airec-omi / Karla)
**Área:** projetos-pessoais/omi/whatsapp — Gateway 1 (WhatsApp → Omi)

## Problema

As conversas do Omi já exibem um marcador de origem no app (chip via `getTag()`:
"Screenpipe", "SD Card", etc.). As **memórias** (fatos extraídos) **não** — só têm um
link indireto pra conversa-mãe. O usuário quer que cada memória mostre **dois eixos
independentes**:

1. **Proveniência** — de *onde* veio: WhatsApp, gravação Omi, Plaud, email, manual.
2. **Assunto** — sobre *o que* é: trabalho, relacionamento, finanças, etc.

## Investigação (evidência que guiou o design)

Confirmado por leitura de código backend + Flutter:

- **Backend:** no caminho conversa→memória (`_extract_memories_inner`), `MemoryDB.app_id`
  fica `None` — `from_memory()` nunca recebe `app_id` nem `source`. A conversa-mãe, porém,
  já carrega `source` (`ConversationSource`) e `app_id` e `structured.category`.
- **`CategoryEnum` já existe** (`backend/models/conversation_enums.py`, 35 valores:
  work, finance, family, romantic, spiritual, health, education…). O eixo "assunto" não
  precisa de enum novo — herda esse.
- **`ConversationSource` já tem `plaud`** nativo; WhatsApp chega como `external_integration`
  + `app_id` do gateway (`01KTHGQSYBNTM6C8GAVKJS9N6X`).
- **App Flutter:** o model `Memory` (`app/lib/backend/schema/memory.dart`) **não tem**
  `app_id`/`source`; a UI (`memory_item.dart`) só mostra ícone-link pra conversa.
- **"Derivar da conversa no cliente" foi descartado:** o enum `ConversationSource` do Flutter
  nem inclui `external_integration`/`whatsapp` (`getTag()` cai no fallback de `category`,
  nunca diz "WhatsApp"); não há resolução `app_id`→nome de app no cliente; conversas são
  paginadas (50), então a conversa-mãe pode não estar em cache → derivar viraria 1 chamada
  de API por memória. Conclusão: **duplicar na memória é mais simples E mais robusto.**

## Decisão de arquitetura

**Duplicar `source` + `topic` na própria memória**, preenchidos na extração a partir da
conversa-mãe (dado já disponível no ponto de extração — custo de propagação ~zero, zero
LLM extra). Os dois eixos são independentes (ex: `source=whatsapp` + `topic=work`).

## Escopo — Backend (`feat/airec-device`)

### 1. Modelo — `backend/models/memories.py`

```python
class MemorySource(str, Enum):
    whatsapp  = "whatsapp"
    recording = "recording"   # gravação Omi nativa (ConversationSource.omi)
    plaud     = "plaud"
    email     = "email"
    manual    = "manual"
    other     = "other"       # fallback p/ origem não mapeada
```

Em `MemoryDB` (metadado de proveniência, junto de `app_id`/`conversation_id` — **não** em
`Memory`, que é conteúdo):

```python
source: Optional[MemorySource] = None
topic:  Optional[CategoryEnum] = None   # importado de conversation_enums
```

Ambos `Optional`/default `None` → memórias antigas continuam válidas (decisão: backfill só
daqui pra frente).

### 2. `MemoryDB.from_memory()` — params opcionais

Assinatura passa a:
```python
def from_memory(memory, uid, conversation_id, manually_added,
                source: Optional[MemorySource] = None,
                topic: Optional[CategoryEnum] = None) -> 'MemoryDB':
```
Atribui `source`/`topic` ao objeto. **Não-quebrante:** todos os call-sites atuais
(`routers/memories.py`, `utils/conversations/memories.py`, `process_twitter_memories`)
continuam funcionando passando os defaults `None`.

Caminho manual (`routers/memories.py` POST `/v3/memories`): passar
`source=MemorySource.manual` explicitamente.

### 3. Derivação — `backend/utils/conversations/process_conversation.py`

Constante de config (módulo de config do backend, ex. `utils/conversations/` ou onde já
houver IDs de app airec):
```python
WHATSAPP_GATEWAY_APP_ID = "01KTHGQSYBNTM6C8GAVKJS9N6X"
```

Função pura:
```python
def derive_memory_source(conversation) -> MemorySource:
    src = conversation.source
    if src == ConversationSource.external_integration \
       and conversation.app_id == WHATSAPP_GATEWAY_APP_ID:
        return MemorySource.whatsapp
    if src == ConversationSource.plaud:
        return MemorySource.plaud
    if src == ConversationSource.omi:
        return MemorySource.recording
    return MemorySource.other
```

No `_extract_memories_inner` (~L503), na construção de cada `MemoryDB`:
```python
mem_source = derive_memory_source(conversation)
mem_topic  = conversation.structured.category if conversation.structured else None
memory_db_obj = MemoryDB.from_memory(
    memory, uid, conversation.id, False,
    source=mem_source, topic=mem_topic,
)
```
`topic` é o `CategoryEnum` da conversa; se ausente/inválido, fica `None`.

### 4. Filtro — `GET /v3/memories?source=<value>`

Param opcional `source: Optional[str] = None` em `get_memories`. Filtra **in-memory** na
lista já carregada (não cria índice Firestore novo):
```python
if source:
    valid_memories = [m for m in valid_memories if m.source and m.source.value == source]
```

## Escopo — App Flutter (`app/`)

### 5. Model — `app/lib/backend/schema/memory.dart`

Adiciona campos nullable `source` (String?) e `topic` (String?), com parse defensivo de
JSON ausente (memórias antigas sem os campos → `null`, sem crash). Serialização toJson
inclui os campos quando presentes.

### 6. UI — `app/lib/pages/memories/widgets/memory_item.dart`

Após a linha de conteúdo da memória, renderizar chip(s):
- **Chip de origem** quando `source != null` — label legível + emoji/ícone
  (ex: 💬 WhatsApp, 🎙️ Gravação, 📋 Plaud, ✉️ Email, ✍️ Manual). `other`/`null` → sem chip.
- **Chip de assunto** quando `topic != null` — capitaliza o `topic` (ex: "Work" → "Trabalho"
  conforme l10n disponível). Reusa o estilo visual dos chips de conversa.

Mapeamento label/ícone vive numa função/extensão no model ou num helper de UI — não
hardcoded espalhado.

## Não-objetivos (YAGNI)

- **Sem backfill retroativo.** Memórias antigas ficam `source=null` (app não mostra chip).
  A ingestão diária do gateway cobre as novas.
- **Sem índice Firestore novo.** Filtro é in-memory.
- **Sem re-classificação por memória.** `topic` é herdado da conversa, não reclassificado
  fato a fato.
- **Sem mexer no enum `ConversationSource` do Flutter** nem em resolução app_id→nome no
  cliente (era o custo da abordagem descartada).

## Testes

- **Backend unit:** `derive_memory_source` para cada caso (whatsapp via app_id correto;
  app_id errado em external_integration → other; plaud; omi → recording; source desconhecida
  → other). `from_memory` propaga `source`/`topic` e mantém compat com call-sites antigos
  (defaults None). Filtro `?source=` em `get_memories`. Adicionar arquivo a `test.sh`.
- **App:** parse de `memory.dart` com e sem os campos novos (JSON antigo → null sem crash).
- **Manual (verificação):** após deploy na Karla, conferir no app que uma memória nova de
  WhatsApp exibe o chip "WhatsApp"; verificar `GET /v3/memories?source=whatsapp` via API
  com ADMIN_KEY.

## Deploy

Backend é baked na imagem na Karla (não volume-mount). Mudança exige rebuild cached
(`docker build -f Dockerfile.patched`) + `docker compose up -d --force-recreate backend`.
App Flutter: build normal. Backfill: nenhum (só daqui pra frente).
