"""Developer API keys sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/dev_api_key.py.

F4.5 (Omi self-hosted): as chaves de API de desenvolvedor saem do Firestore
(coleção de topo `dev_api_keys`) e moram no doc-store genérico do
memory-service (via `omi_docs_karla`), coleção `dev_api_keys` (doc_id =
`key_id`, o mesmo uuid gerado pelo original). Este módulo replica as funções
PÚBLICAS de database/dev_api_key.py falando com o cliente genérico. Ativação
por env `CONFIG_KARLA=true` (mesma flag de users_karla.py — ver rodapé de
database/dev_api_key.py); com a flag off, nada muda (rollback = desligar).

⚠️ Flag própria: `is_enabled()` gateia por `CONFIG_KARLA` (idêntico ao
`users_karla.is_enabled()` — NÃO reusa `OMI_DOCS_KARLA`).

Diferenças de semântica vs Firestore:
  - `db.collection("dev_api_keys").where("user_id", "==", user_id)`
    (cross-user por design no original — QUALQUER user_id pode existir na
    coleção de topo) vira `k.listar` da coleção inteira + filtro client-side
    por `user_id` (a Karla é single-tenant por token, então na prática há
    poucos docs; sem índice composto necessário). Ordenação por `created_at`
    desc replicada via sort client-side.
  - `where("hashed_key", "==", hashed_key).limit(1)` vira `k.listar(filtro=
    {"hashed_key": ...}, limite=1)` (jsonb containment sobre `dados`).
  - `key_ref.update({"last_used_at": ...})` vira `k.patch` da chave de topo.
  - Caches Redis (`cache_dev_api_key`/`get_cached_dev_api_key_data`/
    `delete_cached_dev_api_key`) preservados IDÊNTICOS — chamadas ao mesmo
    módulo `database.redis_db`, sem mudança de comportamento.
  - `id`/`user_id`/`hashed_key`/`key_prefix`/`created_at`/`last_used_at`/
    `scopes` gravados em `dados` como no original (o doc inteiro é o
    `api_key_doc`), permitindo reconstruir tudo via `k.listar`/`k.obter`
    sem depender de doc_id fora de `dados`.

Filosofia de erro: espelha o mold (erros de rede logam e devolvem
NEUTRO/DEFAULT do original) — mas autenticação por API key é PARTE do
critical path, então aqui `get_user_and_scopes_by_api_key`/
`get_user_id_by_api_key` devolvem None em erro (== chave inválida do ponto de
vista do original, mesmo comportamento de "não achou"), nunca raise.
"""

import logging
import os
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

import database.redis_db as redis_db
from database import omi_docs_karla as k
from models.dev_api_key import DevApiKey
from utils.dev_api_keys import generate_dev_api_key, hash_dev_api_key
from utils.scopes import READ_ONLY_SCOPES

logger = logging.getLogger(__name__)

_DEV_API_KEYS = "dev_api_keys"


def is_enabled() -> bool:
    return (
        os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def create_dev_key(user_id: str, name: str, scopes: Optional[List[str]] = None) -> Tuple[str, DevApiKey]:
    """Cria uma chave nova. `key_id` (uuid) é o doc_id, igual ao original."""
    raw_key, hashed_key, key_prefix = generate_dev_api_key()

    key_id = str(uuid.uuid4())
    now = datetime.utcnow()

    if scopes is None:
        scopes = READ_ONLY_SCOPES

    api_key_doc = {
        "id": key_id,
        "user_id": user_id,
        "name": name,
        "hashed_key": hashed_key,
        "key_prefix": key_prefix,
        "created_at": now,
        "last_used_at": None,
        "scopes": scopes,
    }

    k.upsert(_DEV_API_KEYS, key_id, api_key_doc, criado_em=now)

    api_key_data = DevApiKey(
        id=key_id,
        name=name,
        key_prefix=key_prefix,
        created_at=now,
        last_used_at=None,
        scopes=scopes,
    )
    return raw_key, api_key_data


def get_dev_keys_for_user(user_id: str) -> List[DevApiKey]:
    """Lista a coleção inteira e filtra client-side por `user_id` (o original
    faz `where` + `order_by` server-side; aqui replicamos via sort em
    Python por `created_at` desc)."""
    docs = k.listar(_DEV_API_KEYS, filtro={"user_id": user_id}, limite=100000)
    docs = [d for d in docs if isinstance(d, dict)]
    docs.sort(key=lambda d: d.get('created_at') or '', reverse=True)

    keys = []
    for key_dict in docs:
        key_dict = dict(key_dict)
        if "scopes" not in key_dict:
            key_dict["scopes"] = None
        keys.append(DevApiKey.model_validate(key_dict))
    return keys


def delete_dev_key(user_id: str, key_id: str):
    """Deleta a chave (se pertencer ao user_id) e invalida o cache Redis,
    igual ao original."""
    key_data = k.obter(_DEV_API_KEYS, key_id)
    if key_data is None:
        return
    if key_data.get("user_id") == user_id:
        hashed_key = key_data.get("hashed_key")
        if hashed_key:
            redis_db.delete_cached_dev_api_key(hashed_key)
        k.deletar(_DEV_API_KEYS, key_id)


def get_user_id_by_api_key(api_key: str) -> Optional[str]:
    user_data = get_user_and_scopes_by_api_key(api_key)
    return user_data.get("user_id") if user_data else None


def get_user_and_scopes_by_api_key(api_key: str) -> Optional[dict]:
    """Verifica a chave (cache Redis primeiro, depois Karla), atualiza
    `last_used_at` em cache miss — idêntico ao original."""
    if not api_key.startswith("omi_dev_"):
        return None
    secret_part = api_key.replace("omi_dev_", "", 1)
    hashed_key = hash_dev_api_key(secret_part)

    cached_data = redis_db.get_cached_dev_api_key_data(hashed_key)
    if cached_data:
        return cached_data

    docs = k.listar(_DEV_API_KEYS, filtro={"hashed_key": hashed_key}, limite=1)
    if not docs or not isinstance(docs[0], dict):
        return None

    key_data = docs[0]
    key_id = key_data.get("id")
    user_id = key_data.get("user_id")
    scopes = key_data.get("scopes")

    if user_id:
        redis_db.cache_dev_api_key(hashed_key, user_id, scopes)
        if key_id:
            k.patch(_DEV_API_KEYS, key_id, {"last_used_at": datetime.utcnow()})

    return {"user_id": user_id, "scopes": scopes}
