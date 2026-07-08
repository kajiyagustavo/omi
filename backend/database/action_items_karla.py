"""Action items sobre a MEMÓRIA UNIFICADA (Karla) — drop-in do database/action_items.py.

F1.5 (decisão do Gustavo 08/07): tarefas saem do Firestore e moram no Postgres
próprio (memory-service, tabela `tarefas`, RLS por área). Este módulo implementa
as mesmas funções públicas consumidas pelo router/extração, falando com a API
REST `/u/<token>/tarefas`. Ativação por env `TAREFAS_KARLA=true` (ver o rodapé
de database/action_items.py); com a flag off, nada muda (rollback = desligar).

Mapeamento Omi ⇄ Karla:
  description⇄descricao · completed⇄concluida · priority⇄prioridade ·
  sort_order⇄ordem · due_at⇄due_at · parent_id⇄parent_id (str⇄int) ·
  id⇄str(id) · created_at⇄criado_em · updated_at⇄atualizado_em ·
  completed_at⇄concluida_em. Campos Omi-específicos (conversation_id,
  is_locked, exported, export_date, export_platform, apple_reminder_id,
  indent_level, sync_requested) fazem round-trip em `extras` JSONB.

uid é ignorado: o Omi é single-user e a identidade vem do token da Karla.
"""
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_CAMPOS_CORE = {"description", "completed", "priority", "parent_id", "due_at",
                "sort_order"}
_EXTRAS_CONHECIDOS = {"conversation_id", "is_locked", "exported", "export_date",
                      "export_platform", "apple_reminder_id", "indent_level",
                      "sync_requested"}


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


def _to_karla(data: dict) -> dict:
    """Payload Omi → corpo POST/PATCH da Karla (core + extras)."""
    extras = dict(data.get("extras") or {})
    for k, v in data.items():
        if k in _CAMPOS_CORE or k in ("id", "created_at", "updated_at",
                                      "completed_at", "extras"):
            continue
        extras[k] = _iso(v)
    corpo = {"extras": extras}
    if "description" in data:
        corpo["descricao"] = data["description"]
    if "completed" in data:
        corpo["concluida"] = bool(data["completed"])
    if "priority" in data:
        corpo["prioridade"] = data["priority"]
    if "sort_order" in data:
        corpo["ordem"] = int(data["sort_order"] or 0)
    if "due_at" in data:
        corpo["due_at"] = _iso(data["due_at"])
    if "parent_id" in data:
        corpo["parent_id"] = int(data["parent_id"]) if data["parent_id"] else None
    return corpo


def _to_omi(t: dict) -> dict:
    """Linha da Karla → dict no shape que o router/modelos do Omi esperam."""
    extras = t.get("extras") or {}
    out = {
        "id": str(t["id"]),
        "description": t.get("descricao", ""),
        "completed": bool(t.get("concluida")),
        "priority": t.get("prioridade"),
        "sort_order": t.get("ordem") or 0,
        "parent_id": str(t["parent_id"]) if t.get("parent_id") else None,
        "created_at": _dt(t.get("criado_em")),
        "updated_at": _dt(t.get("atualizado_em")),
        "due_at": _dt(t.get("due_at")),
        "completed_at": _dt(t.get("concluida_em")),
    }
    for k in _EXTRAS_CONHECIDOS:
        if k in extras:
            out[k] = _dt(extras[k]) if k == "export_date" else extras[k]
    out.setdefault("is_locked", False)
    out.setdefault("exported", False)
    out.setdefault("indent_level", 0)
    out.setdefault("conversation_id", None)
    return out


# ── CREATE ───────────────────────────────────────────────────────────────────

def create_action_item(uid: str, action_item_data: dict) -> str:
    t = _req("POST", "/tarefas", json_body=_to_karla(action_item_data))
    return str(t["id"])


def create_action_items_batch(uid: str, action_items_data: List[dict]) -> List[str]:
    return [create_action_item(uid, d) for d in action_items_data]


# ── READ ─────────────────────────────────────────────────────────────────────

def get_action_item(uid: str, action_item_id: str) -> Optional[dict]:
    # não há GET por id na API; lista e filtra (volume single-user é baixo)
    for t in _listar():
        if str(t["id"]) == str(action_item_id):
            return _to_omi(t)
    return None


def _listar(**params) -> List[dict]:
    return _req("GET", "/tarefas", params={k: v for k, v in params.items()
                                           if v is not None})["tarefas"]


def get_action_items(
    uid: str,
    conversation_id: Optional[str] = None,
    completed: Optional[bool] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    due_start_date: Optional[datetime] = None,
    due_end_date: Optional[datetime] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[dict]:
    import json as _json
    params = {
        "concluida": completed,
        "criado_de": _iso(start_date), "criado_ate": _iso(end_date),
        "due_de": _iso(due_start_date), "due_ate": _iso(due_end_date),
        "limite": limit or 200, "offset": offset,
    }
    if conversation_id is not None:
        params["extras_contem"] = _json.dumps({"conversation_id": conversation_id})
    itens = [_to_omi(t) for t in _listar(**params)]
    # mesma ordenação do original: due_at primeiro (sem due_at por último), depois criação desc
    itens.sort(key=lambda x: (
        x.get("due_at") is None,
        x.get("due_at") or datetime.max.replace(tzinfo=timezone.utc),
        -((x.get("created_at") or datetime.min.replace(tzinfo=timezone.utc)).timestamp()),
    ))
    return itens


def get_action_items_by_conversation(uid: str, conversation_id: str) -> List[dict]:
    return get_action_items(uid, conversation_id=conversation_id)


def get_action_items_by_ids(uid: str, action_item_ids: List[str]) -> List[dict]:
    ids = {str(i) for i in action_item_ids}
    return [_to_omi(t) for t in _listar(limite=1000) if str(t["id"]) in ids]


# ── UPDATE ───────────────────────────────────────────────────────────────────

def _patch(action_item_id: str, corpo: dict) -> Optional[dict]:
    try:
        return _req("PATCH", f"/tarefas/{int(action_item_id)}", json_body=corpo)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


def update_action_item(uid: str, action_item_id: str, update_data: dict) -> bool:
    corpo = _to_karla(update_data)
    if corpo.get("extras"):
        # merge com extras atuais (PATCH substitui o JSONB inteiro)
        atual = get_action_item(uid, action_item_id)
        if atual is None:
            return False
        extras = {k: _iso(atual.get(k)) for k in _EXTRAS_CONHECIDOS
                  if atual.get(k) is not None}
        extras.update(corpo["extras"])
        corpo["extras"] = extras
    else:
        corpo.pop("extras", None)
    return _patch(action_item_id, corpo) is not None


def batch_update_action_items(uid: str, items: list) -> None:
    for entry in items:
        update_action_item(uid, entry["id"], entry.get("data", entry))


def mark_action_item_completed(uid: str, action_item_id: str,
                               completed: bool = True) -> bool:
    return _patch(action_item_id, {"concluida": completed}) is not None


# ── DELETE ───────────────────────────────────────────────────────────────────

def delete_action_item(uid: str, action_item_id: str) -> bool:
    try:
        _req("DELETE", f"/tarefas/{int(action_item_id)}")
        return True
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return False
        raise


def delete_action_items_for_conversation(uid: str, conversation_id: str) -> int:
    itens = get_action_items_by_conversation(uid, conversation_id)
    for it in itens:
        delete_action_item(uid, it["id"])
    return len(itens)


# ── Apple Reminders sync (flags em extras) ──────────────────────────────────

def batch_set_sync_requested(uid: str, item_ids: List[str]) -> None:
    for i in item_ids:
        update_action_item(uid, i, {"sync_requested": True})


def get_pending_apple_reminders_sync(uid: str) -> dict:
    import json as _json
    pend = [_to_omi(t) for t in _listar(
        extras_contem=_json.dumps({"sync_requested": True}), limite=50)]
    pending_export = [p for p in pend if p.get("exported") is not True]
    synced = [_to_omi(t) for t in _listar(
        extras_contem=_json.dumps({"export_platform": "apple_reminders",
                                   "exported": True}), limite=100)]
    synced.sort(key=lambda x: x.get("updated_at")
                or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return {"pending_export": pending_export, "synced_items": synced}


def batch_sync_update_action_items(uid: str, updates: List[dict]) -> None:
    for entry in updates:
        data = dict(entry["data"])
        if data.get("exported") is True:
            data["sync_requested"] = False
        update_action_item(uid, entry["id"], data)
