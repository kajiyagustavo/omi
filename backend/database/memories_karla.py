"""Memórias sobre a MEMÓRIA UNIFICADA (Karla) — drop-in do database/memories.py.

F4 (decisão do Gustavo 14/07): as memórias saem do Firestore/Pinecone e moram
no Postgres próprio (memory-service, tabela `fatos` evoluída, RLS por área).
Este módulo implementa as mesmas funções públicas consumidas pelos routers,
falando com a API REST `/u/<token>/memorias`. Ativação por env
`MEMORIAS_KARLA=true` (ver o rodapé de database/memories.py); com a flag off,
nada muda (rollback = desligar). Filosofia de erro = tarefas (F1.5): a Karla é
fonte de verdade — erros propagam, sem fallback silencioso.

Mapeamento Omi ⇄ Karla:
  content⇄texto · category⇄categoria · tags⇄tags · id⇄str(id) ·
  created_at⇄criado_em · updated_at⇄atualizado_em. Campos Omi-específicos
  (scoring, visibility, reviewed, user_review, manually_added, edited,
  conversation_id, memory_id, app_id, source, headline, is_locked,
  data_protection_level, kg_extracted) fazem round-trip em `extras` JSONB.
Ids legados (hash do conteúdo, document_id_from_seed) são resolvidos por
`origem = omi:<hash>`; memórias novas gravam a mesma origem — preserva o
dedup por conteúdo do Omi. uid não autentica nada (single-user; identidade =
token da Karla), mas É reinjetado nos dicts de saída — MemoryDB.model_validate
nos routers exige uid preenchido.
"""
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_EXTRAS_CONHECIDOS = {"scoring", "visibility", "reviewed", "user_review",
                      "manually_added", "edited", "conversation_id", "memory_id",
                      "app_id", "source", "headline", "is_locked",
                      "data_protection_level", "kg_extracted", "omi_id"}
# `topic` NÃO sai dos extras: fica preservado na Karla mas não é emitido no dict
# (CategoryEnum de conversas; caso real 35cfc6c4 tinha topic='obsidian:...' e o
# MemoryDB.model_validate do router DESCARTAVA a memória da lista).
_SOURCES_VALIDOS = {"whatsapp", "recording", "plaud", "email", "manual", "other"}
_CAMPOS_CORE = {"content", "category", "tags"}


def is_enabled() -> bool:
    return (os.getenv("MEMORIAS_KARLA", "").lower() in ("1", "true", "yes")
            and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
            and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN")))


def _base() -> str:
    url = os.getenv("MEMORIA_UNIFICADA_URL", "").rstrip("/")
    tok = os.getenv("MEMORIA_UNIFICADA_TOKEN", "")
    return f"{url}/u/{tok}"


def _req(metodo: str, path: str, *, json_body=None, params=None):
    r = requests.request(metodo, _base() + path, json=json_body,
                         params=params, timeout=30)
    r.raise_for_status()
    return r.json() if r.status_code != 204 else None


def _dt(v):
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _iso(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _to_omi(m: dict, uid: str) -> dict:
    """⚠️ uid é OBRIGATÓRIO no dict: GET /v3/memories valida cada item com
    MemoryDB.model_validate() e DESCARTA silenciosamente o que falhar
    (routers/memories.py:160-166) — uid=None zeraria a lista inteira."""
    extras = m.get("extras") or {}
    out = {
        "id": str(m["id"]),
        "uid": uid,
        "content": m.get("texto", ""),
        "category": m.get("categoria", "system"),
        "tags": list(m.get("tags") or []),
        "created_at": _dt(m.get("criado_em")),
        "updated_at": _dt(m.get("atualizado_em")),
    }
    for k in _EXTRAS_CONHECIDOS:
        if extras.get(k) is not None:
            out[k] = extras[k]
    # enums: valor fora do MemorySource derruba a memória no model_validate do
    # router (bug real em prod 14/07: source='obsidian-backfill-cliente')
    if out.get("source") not in _SOURCES_VALIDOS:
        out.pop("source", None)
    out.setdefault("visibility", "private")
    out.setdefault("reviewed", False)
    out.setdefault("user_review", None)
    out.setdefault("manually_added", out.get("category") == "manual")
    out.setdefault("edited", False)
    out.setdefault("is_locked", False)
    out.setdefault("scoring", None)
    out.setdefault("app_id", None)
    out.setdefault("conversation_id", None)
    out.setdefault("memory_id", out.get("conversation_id"))
    out.setdefault("data_protection_level", "standard")
    out.setdefault("kg_extracted", False)
    return out


def _extras_de(data: dict) -> dict:
    extras = {}
    for k, v in data.items():
        if k in _CAMPOS_CORE or k in ("id", "uid", "created_at", "updated_at"):
            continue
        if v is None:  # None não entra: no round-trip sobrescreveria os defaults
            continue
        extras[k] = _iso(v)
    return extras


def _resolver_id(memory_id: str) -> Optional[int]:
    """Karla usa id inteiro; o Omi legado usa hash do conteúdo. Dígitos → id
    direto; senão procura por origem=omi:<hash> (memória migrada/criada)."""
    s = str(memory_id)
    if s.isdigit():
        return int(s)
    r = _req("GET", "/memorias", params={"origem": f"omi:{s}",
                                         "incluir_rejeitadas": "true", "limite": 1})
    mems = r.get("memorias") or []
    return int(mems[0]["id"]) if mems else None


# ── READ ─────────────────────────────────────────────────────────────────────

def get_memories(uid: str, limit: int = 100, offset: int = 0,
                 categories: Optional[List] = None,
                 start_date: Optional[datetime] = None,
                 end_date: Optional[datetime] = None) -> List[dict]:
    params: Dict[str, Any] = {"limite": limit, "offset": offset}
    if categories:
        params["categorias"] = ",".join(str(getattr(c, "value", c)) for c in categories)
    if start_date:
        params["criado_de"] = _iso(start_date)
    if end_date:
        params["criado_ate"] = _iso(end_date)
    r = _req("GET", "/memorias", params=params)
    return [_to_omi(m, uid) for m in r.get("memorias", [])]


def get_user_public_memories(uid: str, limit: int = 100, offset: int = 0) -> List[dict]:
    r = _req("GET", "/memorias", params={"limite": limit, "offset": offset,
                                         "extras_contem": '{"visibility": "public"}'})
    return [_to_omi(m, uid) for m in r.get("memorias", [])]


def get_non_filtered_memories(uid: str, limit: int = 100, offset: int = 0) -> List[dict]:
    r = _req("GET", "/memorias", params={"limite": limit, "offset": offset,
                                         "incluir_rejeitadas": "true"})
    return [_to_omi(m, uid) for m in r.get("memorias", [])]


def get_memory(uid: str, memory_id: str) -> Optional[dict]:
    kid = _resolver_id(memory_id)
    if kid is None:
        return None
    try:
        return _to_omi(_req("GET", f"/memorias/{kid}"), uid)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


def get_memories_by_ids(uid: str, memory_ids: List[str]) -> List[dict]:
    numericos = [i for i in map(str, memory_ids) if i.isdigit()]
    out: List[dict] = []
    if numericos:
        r = _req("GET", "/memorias", params={"ids": ",".join(numericos),
                                             "incluir_rejeitadas": "true",
                                             "limite": len(numericos)})
        out.extend(_to_omi(m, uid) for m in r.get("memorias", []))
    for i in map(str, memory_ids):
        if not i.isdigit():
            m = get_memory(uid, i)
            if m:
                out.append(m)
    return out


def get_memory_ids_for_conversation(uid: str, conversation_id: str) -> List[str]:
    r = _req("GET", "/memorias", params={
        "extras_contem": json.dumps({"conversation_id": conversation_id}),
        "incluir_rejeitadas": "true", "limite": 1000})
    return [str(m["id"]) for m in r.get("memorias", [])]


def find_similar(content: str, threshold: float = 0.85, limit: int = 5) -> List[dict]:
    """Substituto do vector_db.find_similar_memories (mesma shape de retorno)."""
    r = _req("GET", "/memorias/similares",
             params={"q": content, "limiar": threshold, "limite": limit})
    return [{"memory_id": str(s["id"]), "category": s["categoria"],
             "score": float(s["sim"])} for s in r.get("similares", [])]


# ── WRITE ────────────────────────────────────────────────────────────────────

def create_memory(uid: str, data: dict) -> None:
    corpo = {
        "texto": data.get("content", ""),
        "categoria": str(getattr(data.get("category"), "value", data.get("category"))
                         or "manual"),
        "tags": list(data.get("tags") or []),
        "origem": f"omi:{data['id']}" if data.get("id") else None,
        "criado_em": _iso(data.get("created_at")),
        "extras": _extras_de(data),
    }
    if corpo["categoria"] not in ("system", "manual", "interesting"):
        corpo["categoria"] = "system"
    _req("POST", "/memorias", json_body=corpo)


def save_memories(uid: str, data: List[dict]) -> None:
    for d in data:
        create_memory(uid, d)


def edit_memory(uid: str, memory_id: str, value: str) -> None:
    kid = _resolver_id(memory_id)
    if kid is None:
        return
    _req("PATCH", f"/memorias/{kid}",
         json_body={"texto": value, "extras_merge": {"edited": True}})


def update_memory_fields(uid: str, memory_id: str, data: dict) -> None:
    kid = _resolver_id(memory_id)
    if kid is None:
        return
    corpo: Dict[str, Any] = {}
    if "content" in data:
        corpo["texto"] = data["content"]
    if "category" in data:
        corpo["categoria"] = str(getattr(data["category"], "value", data["category"]))
    if "tags" in data:
        corpo["tags"] = list(data["tags"] or [])
    extras = _extras_de(data)
    if extras:
        corpo["extras_merge"] = extras
    if corpo:
        _req("PATCH", f"/memorias/{kid}", json_body=corpo)


def review_memory(uid: str, memory_id: str, value: bool) -> None:
    kid = _resolver_id(memory_id)
    if kid is None:
        return
    _req("PATCH", f"/memorias/{kid}",
         json_body={"extras_merge": {"reviewed": True, "user_review": value}})


def change_memory_visibility(uid: str, memory_id: str, value: str) -> None:
    kid = _resolver_id(memory_id)
    if kid is None:
        return
    _req("PATCH", f"/memorias/{kid}", json_body={"extras_merge": {"visibility": value}})


def set_memory_kg_extracted(uid: str, memory_id: str) -> None:
    kid = _resolver_id(memory_id)
    if kid is None:
        return
    _req("PATCH", f"/memorias/{kid}", json_body={"extras_merge": {"kg_extracted": True}})


def delete_memory(uid: str, memory_id: str) -> None:
    kid = _resolver_id(memory_id)
    if kid is None:
        return
    try:
        _req("DELETE", f"/memorias/{kid}")
    except requests.HTTPError as e:
        if not (e.response is not None and e.response.status_code == 404):
            raise


def delete_memories(uid: str) -> None:
    r = _req("DELETE", "/memorias", params={"origem_prefixo": "omi:"})
    logger.warning(f"memorias_karla: wipe omi:* -> {r.get('deletadas')} deletadas")


def delete_all_memories(uid: str) -> None:
    delete_memories(uid)


def delete_memories_for_conversation(uid: str, memory_id: str) -> None:
    for mid in get_memory_ids_for_conversation(uid, memory_id):
        delete_memory(uid, mid)


def unlock_all_memories(uid: str) -> None:
    return None  # single-user self-hosted: não há memórias bloqueadas por plano
