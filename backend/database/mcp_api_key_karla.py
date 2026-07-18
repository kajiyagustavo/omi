"""MCP API keys sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/mcp_api_key.py.

F4.5 (Omi self-hosted): as chaves de API MCP saem do Firestore (coleção de
topo `mcp_api_keys`) e moram no doc-store genérico do memory-service (via
`omi_docs_karla`), coleção `mcp_api_keys` (doc_id = `key_id`, o mesmo uuid
gerado pelo original). Este módulo replica as funções PÚBLICAS de
database/mcp_api_key.py falando com o cliente genérico. Ativação por env
`CONFIG_KARLA=true` (mesma flag de users_karla.py — ver rodapé de
database/mcp_api_key.py); com a flag off, nada muda (rollback = desligar).

⚠️ Flag própria: `is_enabled()` gateia por `CONFIG_KARLA` (idêntico ao
`users_karla.is_enabled()` — NÃO reusa `OMI_DOCS_KARLA`).

Diferenças de semântica vs Firestore (mesmo idioma de dev_api_key_karla.py):
  - `where("user_id", "==", user_id).order_by("created_at", DESC)` vira
    `k.listar(filtro={"user_id": ...})` + sort client-side por created_at
    desc.
  - `where("hashed_key", "==", hashed_key).limit(1)` vira `k.listar(filtro=
    {"hashed_key": ...}, limite=1)`.
  - `key_ref.update({"last_used_at": ...})` vira `k.patch` da chave de topo.
  - Caches Redis (`cache_mcp_api_key`/`get_cached_mcp_api_key_user_id`/
    `delete_cached_mcp_api_key`) preservados IDÊNTICOS.

Filosofia de erro: espelha o mold — autenticação é critical path, então
`get_user_id_by_api_key` devolve None em erro (== chave inválida do ponto de
vista do original), nunca raise.
"""

import os
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

import database.redis_db as redis_db
from database import omi_docs_karla as k
from models.mcp_api_key import McpApiKey
from utils.mcp_api_keys import generate_api_key, hash_api_key

_MCP_API_KEYS = "mcp_api_keys"


def is_enabled() -> bool:
    return (
        os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def create_mcp_key(user_id: str, name: str) -> Tuple[str, McpApiKey]:
    raw_key, hashed_key, key_prefix = generate_api_key()
    key_id = str(uuid.uuid4())
    now = datetime.utcnow()

    api_key_doc = {
        "id": key_id,
        "user_id": user_id,
        "name": name,
        "hashed_key": hashed_key,
        "key_prefix": key_prefix,
        "created_at": now,
        "last_used_at": None,
    }
    k.upsert(_MCP_API_KEYS, key_id, api_key_doc, criado_em=now)

    api_key_data = McpApiKey(
        id=key_id,
        name=name,
        key_prefix=key_prefix,
        created_at=now,
        last_used_at=None,
    )
    return raw_key, api_key_data


def get_mcp_keys_for_user(user_id: str) -> List[McpApiKey]:
    docs = k.listar(_MCP_API_KEYS, filtro={"user_id": user_id}, limite=100000)
    docs = [d for d in docs if isinstance(d, dict)]
    docs.sort(key=lambda d: d.get('created_at') or '', reverse=True)
    return [McpApiKey.model_validate(d) for d in docs]


def delete_mcp_key(user_id: str, key_id: str):
    key_data = k.obter(_MCP_API_KEYS, key_id)
    if key_data is None:
        return
    if key_data.get("user_id") == user_id:
        hashed_key = key_data.get("hashed_key")
        if hashed_key:
            redis_db.delete_cached_mcp_api_key(hashed_key)
        k.deletar(_MCP_API_KEYS, key_id)


def get_user_id_by_api_key(api_key: str) -> Optional[str]:
    if not api_key.startswith("omi_mcp_"):
        return None
    secret_part = api_key.replace("omi_mcp_", "", 1)
    hashed_key = hash_api_key(secret_part)

    user_id = redis_db.get_cached_mcp_api_key_user_id(hashed_key)
    if user_id:
        return user_id

    docs = k.listar(_MCP_API_KEYS, filtro={"hashed_key": hashed_key}, limite=1)
    if not docs or not isinstance(docs[0], dict):
        return None

    key_data = docs[0]
    key_id = key_data.get("id")
    user_id = key_data.get("user_id")

    if user_id:
        redis_db.cache_mcp_api_key(hashed_key, user_id)
        if key_id:
            k.patch(_MCP_API_KEYS, key_id, {"last_used_at": datetime.utcnow()})

    return user_id
