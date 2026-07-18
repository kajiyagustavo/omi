"""Goals sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de database/goals.py.

F4.2/F4.4 (Omi self-hosted): metas do usuário e seu histórico de progresso
saem do Firestore e moram no doc-store genérico do memory-service (via
`omi_docs_karla`), coleções `goals` (metas) e `goal_history` (pontos de
progresso — subcoleção no original, achatada aqui com `goal_id` injetado em
`dados` pra ficar filtrável). Este módulo replica as funções PÚBLICAS de
database/goals.py falando com o cliente genérico. Ativação por env
`OMI_DOCS_KARLA=true` (ver rodapé de database/goals.py); com a flag off, nada
muda (rollback = desligar).

Diferenças de semântica vs Firestore:
  - `goals`: doc_id == goal_data['id'] (gerado como no original:
    `goal_{YYYYmmddHHMMSS}` se ausente).
  - `goal_history` era subcoleção `goals/{goal_id}/goal_history` no original,
    com doc_id = data ('YYYY-MM-DD') e `set(..., merge=True)` — upsert de UM
    ponto por dia por meta. Achatada aqui: doc_id = `{goal_id}_{date}` (mesma
    semântica de upsert-por-dia-por-meta), com `goal_id` injetado em `dados`
    pra permitir `listar(filtro={"goal_id": ...})`.
  - `get_goal_history`: o original ordena por `date` (string) desc, limit=days
    (nota: `days` é usado como limit de N pontos, não como janela temporal, no
    original). Aqui replica-se via `listar(filtro={"goal_id": gid}, limite=days)`
    + sort client-side por `date` desc, pra não depender de ordenação por
    campo arbitrário do doc-store.
  - `get_all_goals(include_inactive=True)`: ordena por `created_at` desc.
    `include_inactive=False` (default): filtra `is_active == True`. Ambos
    replicados via `listar` + sort client-side.

Filosofia de erro: herdada do cliente — erros de rede logam (warning+sanitize)
e devolvem valor neutro, pra não derrubar o app.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import omi_docs_karla as k

logger = logging.getLogger(__name__)

_GOALS = "goals"
_GOAL_HISTORY = "goal_history"


def get_user_goal(uid: str) -> Optional[Dict[str, Any]]:
    """Primeira meta ativa do usuário (backward-compat), ou None."""
    docs = k.listar(_GOALS, filtro={"is_active": True}, limite=1)
    return docs[0] if docs else None


def get_user_goals(uid: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Metas ativas do usuário (até `limit`), ordenadas por created_at asc
    (espelha o sort do original, que ordena em Python pra evitar índice
    composto)."""
    docs = k.listar(_GOALS, filtro={"is_active": True}, limite=limit)
    docs.sort(key=lambda g: g.get('created_at') or '', reverse=False)
    return docs


def create_goal(uid: str, goal_data: Dict[str, Any], max_goals: int = 4) -> Dict[str, Any]:
    """Cria uma meta nova. Se já houver `max_goals` ativas, desativa a mais
    antiga (por created_at) antes de criar."""
    active_goals = k.listar(_GOALS, filtro={"is_active": True}, limite=max_goals + 1)

    if len(active_goals) >= max_goals:
        active_goals_sorted = sorted(
            active_goals,
            key=lambda g: g.get('created_at') or datetime.min.replace(tzinfo=timezone.utc).isoformat(),
        )
        oldest = active_goals_sorted[0]
        oldest_id = oldest.get('id')
        if oldest_id:
            k.patch(_GOALS, oldest_id, {'is_active': False, 'ended_at': datetime.now(timezone.utc)})

    goal_id = goal_data.get('id') or f"goal_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    goal_data['id'] = goal_id
    goal_data['is_active'] = True
    now = datetime.now(timezone.utc)
    goal_data['created_at'] = now
    goal_data['updated_at'] = now

    k.upsert(_GOALS, goal_id, goal_data, criado_em=now)
    return goal_data


def update_goal(uid: str, goal_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Atualiza uma meta existente. None se não existir."""
    if k.obter(_GOALS, goal_id) is None:
        return None
    updates = dict(updates)
    updates['updated_at'] = datetime.now(timezone.utc)
    result = k.patch(_GOALS, goal_id, updates)
    if result is None:
        return None
    result.setdefault('id', goal_id)
    return result


def update_goal_progress(uid: str, goal_id: str, current_value: float) -> Optional[Dict[str, Any]]:
    """Atualiza o valor de progresso corrente e salva um ponto no histórico.
    None se a meta não existir."""
    if k.obter(_GOALS, goal_id) is None:
        return None

    result = k.patch(_GOALS, goal_id, {'current_value': current_value, 'updated_at': datetime.now(timezone.utc)})

    save_goal_progress_history(uid, goal_id, current_value)

    if result is None:
        return None
    result.setdefault('id', goal_id)
    return result


def save_goal_progress_history(uid: str, goal_id: str, value: float):
    """Upsert de um ponto de progresso do dia (doc_id = `{goal_id}_{date}`,
    espelhando o `set(merge=True)` sobre `document(today)` do original).
    `goal_id` é injetado em `dados` pra `get_goal_history` conseguir filtrar."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    doc_id = f"{goal_id}_{today}"
    now = datetime.now(timezone.utc)
    k.upsert(
        _GOAL_HISTORY, doc_id, {'goal_id': goal_id, 'date': today, 'value': value, 'recorded_at': now}, criado_em=now
    )


def get_goal_history(uid: str, goal_id: str, days: int = 30) -> List[Dict[str, Any]]:
    """Histórico de progresso da meta, até `days` pontos, ordenado por `date`
    desc (o original usa `days` como limit de N pontos, não como janela de
    calendário — replicado igual)."""
    docs = k.listar(_GOAL_HISTORY, filtro={"goal_id": goal_id}, limite=days)
    docs.sort(key=lambda h: h.get('date') or '', reverse=True)
    return docs


def get_all_goals(uid: str, include_inactive: bool = False) -> List[Dict[str, Any]]:
    """Todas as metas do usuário. include_inactive=True → todas, ordenadas por
    created_at desc. False (default) → só as ativas (sem ordenação, espelha o
    original)."""
    if include_inactive:
        docs = k.listar(_GOALS, limite=100000)
        docs.sort(key=lambda g: g.get('created_at') or '', reverse=True)
        return docs
    return k.listar(_GOALS, filtro={"is_active": True}, limite=100000)


def delete_goal(uid: str, goal_id: str) -> bool:
    """Deleta a meta. False se não existir."""
    if k.obter(_GOALS, goal_id) is None:
        return False
    return k.deletar(_GOALS, goal_id)
