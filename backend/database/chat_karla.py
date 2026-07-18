"""Chat sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de database/chat.py.

F4.2/F4.3 (Omi self-hosted): mensagens, arquivos e sessões de chat saem do
Firestore e moram no doc-store genérico do memory-service (via
`omi_docs_karla`), nas coleções `messages`, `chat_files`, `chat_sessions`.
Este módulo replica as funções PÚBLICAS de database/chat.py falando com o
cliente genérico. Ativação por env `OMI_DOCS_KARLA=true` (ver rodapé de
database/chat.py); com a flag off, nada muda (rollback = desligar).

Diferenças de semântica vs Firestore:
  - Texto em CLARO: a criptografia (`_encrypt/_decrypt/_prepare_*`) NÃO é
    replicada — `data_protection_level` é ignorado nas leituras/escritas.
  - doc_id == message['id'] (o Firestore usava `.add()` com id auto-gerado e
    consultava pelo campo `id`; na Karla a chave é o próprio id do model).
    Portanto `get_message` devolve `(Message, message_id)` — o "doc_id" que os
    routers repassam a `report_message` é o próprio id, e continua funcionando.
  - `get_messages(include_conversations=True)` anexa conversas via
    `database.conversations` (import de MÓDULO, nunca `from ... import x`, pra
    pegar a versão rebindada quando CONVERSAS_KARLA também está on).

⚠️ Trap do merge RASO (omi_docs_karla.patch): substitui chaves de topo por
inteiro. Estruturas aninhadas / arrays (session.message_ids, session.file_ids)
exigem GET + append client-side + PATCH da chave inteira. Documentado função a
função.

Filosofia de erro: herdada do cliente — erros de rede logam (warning+sanitize)
e devolvem valor neutro, pra não derrubar o app.

Funções EXCLUÍDAS (migração de nível de criptografia, sem sentido na Karla):
`get_chats_to_migrate`, `migrate_chats_level_batch`, e os helpers privados
`_encrypt_chat_data`/`_decrypt_chat_data`/`_prepare_*`.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import conversations as conversations_db
from database import omi_docs_karla as k
from models.chat import Message

logger = logging.getLogger(__name__)

_MESSAGES = "messages"
_FILES = "chat_files"
_SESSIONS = "chat_sessions"


# *****************************
# ********** MESSAGES *********
# *****************************


def _upsert_message(message_data: dict):
    """Upsert de um dict-mensagem na coleção `messages` (doc_id=id, criado_em=
    created_at). Espelha o efeito de add_message/save_message do Firestore."""
    doc_id = message_data.get("id")
    if not doc_id:
        doc_id = str(uuid.uuid4())
        message_data["id"] = doc_id
    k.upsert(_MESSAGES, doc_id, message_data, criado_em=message_data.get("created_at"))


def add_message(uid: str, message_data: dict):
    """Upsert em `messages`. Espelha o original: remove `memories` (campo
    front-facing, não persistido) antes de gravar."""
    message_data.pop("memories", None)
    _upsert_message(message_data)
    return message_data


def add_app_message(text: str, app_id: str, uid: str, conversation_id: Optional[str] = None) -> Message:
    ai_message = Message(
        id=str(uuid.uuid4()),
        text=text,
        created_at=datetime.now(timezone.utc),
        sender='ai',
        app_id=app_id,
        from_external_integration=False,
        type='text',
        memories_id=[conversation_id] if conversation_id else [],
    )
    add_message(uid, ai_message.dict())
    return ai_message


def add_integration_chat_message(text: str, app_id: Optional[str], uid: str) -> Message:
    """Add a chat message from an external integration, linking it to the user's
    existing chat session so it appears in the chat feed."""
    chat_session = get_chat_session(uid, app_id=app_id)
    chat_session_id = chat_session['id'] if chat_session else None

    ai_message = Message(
        id=str(uuid.uuid4()),
        text=text,
        created_at=datetime.now(timezone.utc),
        sender='ai',
        app_id=app_id,
        from_external_integration=True,
        type='text',
        chat_session_id=chat_session_id,
    )
    add_message(uid, ai_message.dict())
    if chat_session_id:
        add_message_to_chat_session(uid, chat_session_id, ai_message.id)
    return ai_message


def add_summary_message(text: str, uid: str) -> Message:
    ai_message = Message(
        id=str(uuid.uuid4()),
        text=text,
        created_at=datetime.now(timezone.utc),
        sender='ai',
        app_id=None,
        from_external_integration=False,
        type='day_summary',
        memories_id=[],
    )
    add_message(uid, ai_message.dict())
    return ai_message


def _attach_conversations_and_files(uid: str, messages: List[dict]) -> List[dict]:
    """Espelha a lógica include_conversations do original: coleta memories_id /
    files_id, busca conversas (via módulo, pega rebind) e arquivos, e anexa
    `memories`/`files` a cada mensagem."""
    conversations_id = set()
    files_id = set()
    for message in messages:
        conversations_id.update(message.get('memories_id', []) or [])
        files_id.update(message.get('files_id', []) or [])

    conversations = {}
    if conversations_id:
        for conversation in conversations_db.get_conversations_by_id(uid, list(conversations_id)):
            cid = conversation.get('id')
            if cid is not None:
                conversations[cid] = conversation

    for message in messages:
        message['memories'] = [
            conversations[conversation_id]
            for conversation_id in (message.get('memories_id', []) or [])
            if conversation_id in conversations
        ]

    files = {}
    if files_id:
        for f in get_chat_files(uid, list(files_id)):
            fid = f.get('id')
            if fid is not None:
                files[fid] = f

    for message in messages:
        message['files'] = [files[file_id] for file_id in (message.get('files_id', []) or []) if file_id in files]

    return messages


def get_app_messages(uid: str, app_id: str, limit: int = 20, offset: int = 0, include_conversations: bool = False):
    """Mensagens de um app específico (plugin_id == app_id), desc por created_at,
    pulando reportadas. Anexa conversas se pedido."""
    raw = k.listar(_MESSAGES, filtro={"plugin_id": app_id}, ordem="desc", limite=limit, offset=offset)
    messages = [m for m in raw if m.get('reported') is not True]
    if not include_conversations:
        return messages
    # get_app_messages do original só anexa conversas (não arquivos).
    conversations_id = set()
    for message in messages:
        conversations_id.update(message.get('memories_id', []) or [])
    conversations = {}
    if conversations_id:
        for conversation in conversations_db.get_conversations_by_id(uid, list(conversations_id)):
            cid = conversation.get('id')
            if cid is not None:
                conversations[cid] = conversation
    for message in messages:
        message['memories'] = [
            conversations[conversation_id]
            for conversation_id in (message.get('memories_id', []) or [])
            if conversation_id in conversations
        ]
    return messages


def get_messages(
    uid: str,
    limit: int = 20,
    offset: int = 0,
    include_conversations: bool = False,
    app_id: Optional[str] = None,
    chat_session_id: Optional[str] = None,
):
    """Mensagens do usuário, desc por created_at, pulando reportadas.

    Espelha os where-clauses do original:
      - chat_session_id setado → filtra SÓ por chat_session_id (a sessão já
        determina o app);
      - senão → filtra por plugin_id == app_id (app_id None = chat principal;
        jsonb containment {"plugin_id": null} casa os docs com plugin_id nulo)."""
    logger.info(f'get_messages {uid} {limit} {offset} {app_id} {include_conversations}')
    if chat_session_id:
        filtro = {"chat_session_id": chat_session_id}
    else:
        filtro = {"plugin_id": app_id}

    raw = k.listar(_MESSAGES, filtro=filtro, ordem="desc", limite=limit, offset=offset)
    messages = [m for m in raw if m.get('reported') is not True]

    if not include_conversations:
        return messages
    return _attach_conversations_and_files(uid, messages)


def get_message_count(uid: str) -> int:
    """Total de mensagens do usuário."""
    return k.contar(_MESSAGES)


def iter_all_messages(uid: str, batch_size: int = 1000):
    """Generator de todas as mensagens (desc por created_at), paginado. Usado
    pelo export de dados. Texto já em claro (sem decrypt)."""
    offset = 0
    while True:
        batch = k.listar(_MESSAGES, ordem="desc", limite=batch_size, offset=offset)
        yield from batch
        if len(batch) < batch_size:
            break
        offset += batch_size


def get_message(uid: str, message_id: str) -> tuple[Message, str] | None:
    """GET por id → (Message, doc_id). Na Karla doc_id == message_id, então o
    "doc_id" devolvido é o próprio id (o que os routers repassam a
    report_message continua válido)."""
    dados = k.obter(_MESSAGES, message_id)
    if not dados:
        return None
    message = Message(**dados)
    return message, message_id


def report_message(uid: str, msg_doc_id: str):
    """Marca reported=True. msg_doc_id é o id (ver get_message)."""
    updated = k.patch(_MESSAGES, msg_doc_id, {"reported": True})
    if updated is None:
        return {"message": "Update failed: message not found"}
    return {"message": "Message reported"}


def update_message_rating(uid: str, message_id: str, rating: int | None):
    """Atualiza `rating` (1/-1/None). Devolve True/False como o original."""
    updated = k.patch(_MESSAGES, message_id, {"rating": rating})
    if updated is None:
        logger.warning(f"⚠️ Message {message_id} not found for user {uid}")
        return False
    logger.info(f"✅ Updated message {message_id} rating to {rating}")
    return True


def batch_delete_messages(
    parent_doc_ref, batch_size=450, app_id: Optional[str] = None, chat_session_id: Optional[str] = None
):
    """Deleta mensagens por filtro (plugin_id == app_id [+ chat_session_id]).
    `parent_doc_ref`/`batch_size` são mantidos por assinatura mas ignorados
    (não há batch Firestore na Karla)."""
    filtro: Dict[str, Any] = {"plugin_id": app_id}
    if chat_session_id:
        filtro["chat_session_id"] = chat_session_id
    logger.info(f'batch_delete_messages {app_id}')
    k.deletar_em_lote(_MESSAGES, filtro=filtro)


def clear_chat(uid: str, app_id: Optional[str] = None, chat_session_id: Optional[str] = None):
    """Deleta as mensagens do escopo. Espelha o retorno do original: None em
    sucesso, {"message": ...} em erro."""
    try:
        batch_delete_messages(None, app_id=app_id, chat_session_id=chat_session_id)
        return None
    except Exception as e:
        return {"message": str(e)}


def delete_messages(uid: str, app_id: str = None, session_id: str = None) -> int:
    """Deleta mensagens por app_id/session_id. Devolve a contagem deletada.

    Espelha os where-clauses do original:
      - session_id setado → filtra SÓ por chat_session_id;
      - senão → filtra por plugin_id == app_id."""
    if session_id:
        filtro = {"chat_session_id": session_id}
    else:
        filtro = {"plugin_id": app_id}
    return k.deletar_em_lote(_MESSAGES, filtro=filtro)


# *****************************
# ********** FILES ************
# *****************************


def add_multi_files(uid: str, files_data: list):
    for file_data in files_data:
        k.upsert(_FILES, file_data['id'], file_data, criado_em=file_data.get('created_at'))


def get_chat_files(uid: str, files_id: List[str] = []):
    """Sem ids → todos; com ids → só esses (via ?ids=)."""
    if len(files_id) == 0:
        return k.listar(_FILES, limite=100000)
    return k.listar(_FILES, ids=[str(f) for f in files_id], limite=100000)


def get_chat_files_desc(uid: str, files_id: List[str] = [], limit: int = 10):
    """Mais recentes por created_at desc, opcionalmente filtrados por ids."""
    if len(files_id) == 0:
        return k.listar(_FILES, ordem="desc", limite=limit)
    return k.listar(_FILES, ids=[str(f) for f in files_id], ordem="desc", limite=limit)


def delete_multi_files(uid: str, files_data: list):
    ids = [f["id"] for f in files_data]
    if ids:
        k.deletar_em_lote(_FILES, ids=ids)


# *****************************
# ******** SESSIONS **********
# *****************************


def add_chat_session(uid: str, chat_session_data: dict):
    k.upsert(
        _SESSIONS,
        chat_session_data['id'],
        chat_session_data,
        criado_em=chat_session_data.get('created_at'),
    )
    return chat_session_data


def get_chat_session(uid: str, app_id: Optional[str] = None):
    """Primeira sessão do app (plugin_id == app_id). None se não houver."""
    sessions = k.listar(_SESSIONS, filtro={"plugin_id": app_id}, limite=1)
    return sessions[0] if sessions else None


def get_chat_session_by_id(uid: str, chat_session_id: str):
    """Sessão específica por id, ou None."""
    return k.obter(_SESSIONS, chat_session_id)


def delete_chat_session(uid, chat_session_id, cascade_messages: bool = False):
    """Deleta a sessão. Com cascade_messages, deleta antes as mensagens da
    sessão (filtro chat_session_id) — como o batch-delete do original. Se a
    sessão não existir e cascade estiver ligado, devolve False (mirror)."""
    if cascade_messages:
        if k.obter(_SESSIONS, chat_session_id) is None:
            return False
        k.deletar_em_lote(_MESSAGES, filtro={"chat_session_id": chat_session_id})
    k.deletar(_SESSIONS, chat_session_id)


def add_message_to_chat_session(uid: str, chat_session_id: str, message_id: str):
    """Append em session.message_ids. MERGE RASO trap: GET + append client-side
    + PATCH da chave `message_ids` inteira (ArrayUnion do Firestore)."""
    dados = k.obter(_SESSIONS, chat_session_id)
    if dados is None:
        return
    message_ids = list(dados.get("message_ids", []) or [])
    if message_id not in message_ids:
        message_ids.append(message_id)
    k.patch(_SESSIONS, chat_session_id, {"message_ids": message_ids})


def add_files_to_chat_session(uid: str, chat_session_id: str, file_ids: List[str]):
    """Append em session.file_ids. MERGE RASO trap: GET + append client-side +
    PATCH da chave `file_ids` inteira (ArrayUnion do Firestore)."""
    if not file_ids:
        return
    dados = k.obter(_SESSIONS, chat_session_id)
    if dados is None:
        return
    existentes = list(dados.get("file_ids", []) or [])
    for fid in file_ids:
        if fid not in existentes:
            existentes.append(fid)
    k.patch(_SESSIONS, chat_session_id, {"file_ids": existentes})


def update_chat_session_openai_ids(uid: str, chat_session_id: str, thread_id: str, assistant_id: str):
    """Atualiza openai_thread_id/openai_assistant_id via patch (chaves de topo,
    merge raso é seguro aqui)."""
    update_data: Dict[str, Any] = {}
    if thread_id:
        update_data['openai_thread_id'] = thread_id
    if assistant_id:
        update_data['openai_assistant_id'] = assistant_id
    if update_data:
        k.patch(_SESSIONS, chat_session_id, update_data)
        logger.info(f"Updated session {chat_session_id} with thread {thread_id} and assistant {assistant_id}")


# ============================================================================
# CHAT SESSIONS (v2)
# ============================================================================


def create_chat_session(uid: str, title: str = None, app_id: str = None) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    doc = {
        'id': session_id,
        'title': title or 'New Chat',
        'preview': None,
        'created_at': now,
        'updated_at': now,
        'app_id': app_id,
        'plugin_id': app_id,  # Python chat.py queries chat_sessions by plugin_id
        'message_count': 0,
        'starred': False,
    }
    k.upsert(_SESSIONS, session_id, doc, criado_em=now)
    return doc


def acquire_chat_session(uid: str, app_id: str = None) -> str:
    """Get-or-create de uma sessão do app_id (None = chat principal). Consulta
    por plugin_id, cria se não houver. Devolve o session id."""
    sessions = k.listar(_SESSIONS, filtro={"plugin_id": app_id}, limite=1)
    if sessions:
        return sessions[0]['id']
    session = create_chat_session(uid, app_id=app_id)
    return session['id']


def get_chat_sessions(
    uid: str, app_id: str = None, limit: int = 50, offset: int = 0, starred: bool = None
) -> List[dict]:
    """Sessões do app (plugin_id == app_id), desc por created_at, opcionalmente
    filtradas por starred.

    Nota: o original ordena por `updated_at` (excluindo v1 legado sem esse
    campo); a Karla ordena pelo `criado_em` do doc-store. A semântica visível
    (sessões do app, mais recentes primeiro, filtro starred) é preservada."""
    filtro: Dict[str, Any] = {"plugin_id": app_id}
    if starred is not None:
        filtro["starred"] = starred
    return k.listar(_SESSIONS, filtro=filtro, ordem="desc", limite=limit, offset=offset)


def update_chat_session(uid: str, session_id: str, title: str = None, starred: bool = None) -> Optional[dict]:
    """Atualiza title/starred + updated_at. None se a sessão não existir."""
    if k.obter(_SESSIONS, session_id) is None:
        return None
    updates: Dict[str, Any] = {'updated_at': datetime.now(timezone.utc)}
    if title is not None:
        updates['title'] = title
    if starred is not None:
        updates['starred'] = starred
    result = k.patch(_SESSIONS, session_id, updates)
    if result is None:
        return None
    result['id'] = session_id
    return result


# ============================================================================
# MESSAGES (v2) — persistence-only writes
# ============================================================================


def save_message(
    uid: str, text: str, sender: str, app_id: str = None, session_id: str = None, metadata: str = None
) -> dict:
    """Persiste uma mensagem de chat (desktop). Auto-adquire sessão se ausente,
    e atualiza message_count/preview/updated_at da sessão."""
    msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if not session_id:
        session_id = acquire_chat_session(uid, app_id=app_id)

    doc = {
        'id': msg_id,
        'text': text,
        'created_at': now,
        'sender': sender,
        'type': 'text',
        'app_id': app_id,
        'plugin_id': app_id,  # chat.py queries messages by plugin_id
        'session_id': session_id,
        'chat_session_id': session_id,  # chat.py uses this field name
        'from_external_integration': False,
        'rating': None,
        'reported': False,
        'memories_id': [],
        'metadata': metadata,
    }
    k.upsert(_MESSAGES, msg_id, doc, criado_em=now)

    # Atualiza contagem/preview da sessão (skip se a sessão foi deletada).
    if session_id:
        sessao = k.obter(_SESSIONS, session_id)
        if sessao is not None:
            message_count = int(sessao.get('message_count', 0) or 0) + 1
            k.patch(
                _SESSIONS,
                session_id,
                {
                    'updated_at': now,
                    'message_count': message_count,
                    'preview': text[:100] if text else None,
                },
            )

    return {'id': msg_id, 'created_at': now.isoformat()}
