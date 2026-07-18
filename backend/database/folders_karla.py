"""Folders sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de database/folders.py.

F4.2/F4.4 (Omi self-hosted): as pastas do usuário saem do Firestore e moram no
doc-store genérico do memory-service (via `omi_docs_karla`), coleção
`folders`. Este módulo replica as funções PÚBLICAS de database/folders.py
falando com o cliente genérico. Ativação por env `OMI_DOCS_KARLA=true` (ver
rodapé de database/folders.py); com a flag off, nada muda (rollback =
desligar).

⚠️ Pastas são acopladas a conversas: operações que movem/desassociam
conversas (delete_folder, move_conversation_to_folder,
bulk_move_conversations_to_folder, update_folder_conversation_count) chamam
`database.conversations` como MÓDULO (`from database import conversations as
conversations_db`), nunca `from database.conversations import func` — isso
garante que, se `CONVERSAS_KARLA=true` também estiver ligada, pegamos as
versões rebindadas (Karla) em vez das versões Firestore congeladas. As duas
flags são independentes: com só `OMI_DOCS_KARLA=true` (e `CONVERSAS_KARLA`
off), as pastas moram na Karla mas as operações de conversas ainda vão pro
Firestore via `conversations_db` — funciona igual, só que através de dois
backends diferentes.

Diferenças de semântica vs Firestore:
  - `folders`: doc_id == folder_data['id'] (uuid4, como no original).
  - `get_folders`: o original faz `order_by('order')` no Firestore. Aqui:
    `listar` + sort client-side por `order` asc.
  - `create_folder`: o original pega o maior `order` existente via query
    DESCENDING + limit(1). Aqui: `listar` de todas as pastas + max() client-side
    (doc-store genérico não expõe order_by por campo arbitrário).
  - `reorder_folders`: o original faz um batch de updates de `order`. Aqui:
    um `patch` por pasta (PATCH é merge raso, mas `order`/`updated_at` são
    campos de topo — não há aninhamento a preservar).
  - `delete_folder`: mesma lógica do original (mover conversas pro
    move_to_folder_id ou pra pasta default; se nenhum, só apaga a pasta),
    mas via `conversations_db.get_conversations(..., folder_id=...)` +
    `conversations_db.update_conversation(...)` em vez de batch Firestore.
  - `update_folder_conversation_count`: conta via
    `conversations_db.get_conversations_count` não existe filtro por
    folder_id nessa função no módulo de conversas — replicado aqui via
    `conversations_db.get_conversations(uid, folder_id=fid, include_discarded=False,
    limit=<alto>)` + `len(...)`, e persiste em `conversation_count` (campo de
    topo) via `patch`.

Filosofia de erro: herdada do cliente — erros de rede logam (warning+sanitize)
e devolvem valor neutro, pra não derrubar o app.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import conversations as conversations_db
from database import omi_docs_karla as k

logger = logging.getLogger(__name__)

_FOLDERS = "folders"

# Espelha SYSTEM_FOLDERS / CATEGORY_TO_FOLDER_MAPPING do database/folders.py original.
SYSTEM_FOLDERS = [
    {
        'name': 'Work',
        'category_mapping': 'work',
        'icon': '💼',
        'color': '#3B82F6',
        'description': 'Work, business, professional, and career-related conversations',
    },
    {
        'name': 'Personal',
        'category_mapping': 'personal',
        'icon': '👤',
        'color': '#10B981',
        'description': 'Personal life, family, health, hobbies, and self-improvement',
    },
    {
        'name': 'Social',
        'category_mapping': 'social',
        'icon': '👥',
        'color': '#8B5CF6',
        'description': 'Friends, social gatherings, entertainment, and casual conversations',
    },
]

_HIGH_LIMIT = 100000


def get_folders(uid: str) -> List[Dict[str, Any]]:
    """Todas as pastas do usuário, ordenadas por `order` asc."""
    docs = k.listar(_FOLDERS, limite=_HIGH_LIMIT)
    docs.sort(key=lambda f: f.get('order') if f.get('order') is not None else 0)
    return docs


def get_folder(uid: str, folder_id: str) -> Optional[Dict[str, Any]]:
    """Uma pasta específica, ou None se não existir."""
    return k.obter(_FOLDERS, folder_id)


def create_folder(
    uid: str,
    name: str,
    description: Optional[str] = None,
    color: Optional[str] = None,
    icon: Optional[str] = None,
) -> Dict[str, Any]:
    """Cria uma pasta custom nova pro usuário."""
    existing_folders = get_folders(uid)
    max_order = max((f.get('order') or 0 for f in existing_folders), default=0)

    folder_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    folder_data = {
        'id': folder_id,
        'name': name,
        'description': description,
        'color': color or '#6B7280',
        'icon': icon or '📁',
        'created_at': now,
        'updated_at': now,
        'order': max_order + 1,
        'is_default': False,
        'is_system': False,
        'category_mapping': None,
        'conversation_count': 0,
    }

    k.upsert(_FOLDERS, folder_id, folder_data, criado_em=now)
    return folder_data


def update_folder(uid: str, folder_id: str, update_data: Dict[str, Any]) -> bool:
    """Atualiza metadados de uma pasta. True em sucesso (espelha o original,
    que sempre devolve True — Firestore .update() não checa existência)."""
    update_data = dict(update_data)
    update_data['updated_at'] = datetime.now(timezone.utc)
    k.patch(_FOLDERS, folder_id, update_data)
    return True


def delete_folder(uid: str, folder_id: str, move_to_folder_id: Optional[str] = None) -> bool:
    """Deleta uma pasta e move suas conversas pra outra pasta.
    Se move_to_folder_id não for informado, move pra pasta default (is_default)."""
    target_folder_id = move_to_folder_id
    if not target_folder_id:
        folders = get_folders(uid)
        default_folder = next((f for f in folders if f.get('is_default')), None)
        if default_folder:
            target_folder_id = default_folder['id']

    if target_folder_id:
        conversations = conversations_db.get_conversations(
            uid, limit=_HIGH_LIMIT, include_discarded=True, folder_id=folder_id
        )
        for conv in conversations:
            conv_id = conv.get('id')
            if conv_id:
                conversations_db.update_conversation(uid, conv_id, {'folder_id': target_folder_id})

        update_folder_conversation_count(uid, target_folder_id)

    k.deletar(_FOLDERS, folder_id)
    return True


def reorder_folders(uid: str, folder_ids: List[str]) -> bool:
    """Reordena pastas dada uma lista ordenada de folder_ids."""
    now = datetime.now(timezone.utc)
    for i, folder_id in enumerate(folder_ids):
        k.patch(_FOLDERS, folder_id, {'order': i, 'updated_at': now})
    return True


def initialize_system_folders(uid: str) -> List[Dict[str, Any]]:
    """Cria as pastas de sistema pra um usuário novo (ou sem pastas ainda).
    Idempotente: se já existir alguma pasta, devolve get_folders(uid)."""
    existing = k.listar(_FOLDERS, limite=1)
    if existing:
        return get_folders(uid)

    created_folders = []
    now = datetime.now(timezone.utc)

    for i, folder_config in enumerate(SYSTEM_FOLDERS):
        folder_id = str(uuid.uuid4())
        folder_data = {
            'id': folder_id,
            'name': folder_config['name'],
            'description': folder_config['description'],
            'color': folder_config['color'],
            'icon': folder_config['icon'],
            'created_at': now,
            'updated_at': now,
            'order': i,
            'is_default': folder_config['category_mapping'] == 'other',
            'is_system': True,
            'category_mapping': folder_config['category_mapping'],
            'conversation_count': 0,
        }
        k.upsert(_FOLDERS, folder_id, folder_data, criado_em=now)
        created_folders.append(folder_data)

    return created_folders


def get_conversations_in_folder(
    uid: str,
    folder_id: str,
    limit: int = 100,
    offset: int = 0,
    include_discarded: bool = False,
) -> List[Dict[str, Any]]:
    """Todas as conversas de uma pasta específica."""
    return conversations_db.get_conversations(
        uid,
        limit=limit,
        offset=offset,
        include_discarded=include_discarded,
        folder_id=folder_id,
    )


def move_conversation_to_folder(
    uid: str,
    conversation_id: str,
    folder_id: Optional[str],
) -> bool:
    """Move uma conversa pra outra pasta. False se a conversa não existir."""
    conversations = conversations_db.get_conversations_by_id(uid, [conversation_id])
    if not conversations:
        return False

    old_folder_id = conversations[0].get('folder_id')

    conversations_db.update_conversation(uid, conversation_id, {'folder_id': folder_id})

    if old_folder_id:
        update_folder_conversation_count(uid, old_folder_id)
    if folder_id:
        update_folder_conversation_count(uid, folder_id)

    return True


def bulk_move_conversations_to_folder(
    uid: str,
    conversation_ids: List[str],
    folder_id: str,
) -> int:
    """Move várias conversas pra uma pasta. Devolve a contagem de conversas movidas."""
    if not conversation_ids:
        return 0

    conversations = conversations_db.get_conversations_by_id(uid, conversation_ids)

    affected_folders = set()
    moved = 0

    for conv in conversations:
        conv_id = conv.get('id')
        if not conv_id:
            continue

        old_folder_id = conv.get('folder_id')
        if old_folder_id:
            affected_folders.add(old_folder_id)

        conversations_db.update_conversation(uid, conv_id, {'folder_id': folder_id})
        moved += 1

    affected_folders.add(folder_id)
    for fid in affected_folders:
        update_folder_conversation_count(uid, fid)

    return moved


def update_folder_conversation_count(uid: str, folder_id: str) -> int:
    """Recalcula e persiste conversation_count de uma pasta."""
    conversations = conversations_db.get_conversations(
        uid, limit=_HIGH_LIMIT, include_discarded=False, folder_id=folder_id
    )
    count = len(conversations)
    k.patch(_FOLDERS, folder_id, {'conversation_count': count})
    return count


def get_folder_by_category_mapping(uid: str, category_mapping: str) -> Optional[Dict[str, Any]]:
    """Pasta cujo category_mapping bate com o informado, ou None."""
    folders = get_folders(uid)
    return next((f for f in folders if f.get('category_mapping') == category_mapping), None)
