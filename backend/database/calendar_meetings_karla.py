"""Calendar meetings sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/calendar_meetings.py.

F4.5 (Omi self-hosted): as reuniões sincronizadas de calendário saem do
Firestore (`users/{uid}/meetings/{auto_id}`) e moram no doc-store genérico do
memory-service (via `omi_docs_karla`), coleção `meetings` (doc_id = meeting
id — o original usa `.document()` sem argumento pra gerar um auto-id do
Firestore; aqui geramos um uuid4 client-side em `create_meeting`, mesma
função de doc_id único e opaco). Este módulo replica as funções PÚBLICAS de
database/calendar_meetings.py falando com o cliente genérico. Ativação por
env `CONFIG_KARLA=true` (mesma flag de users_karla.py — ver rodapé de
database/calendar_meetings.py); com a flag off, nada muda (rollback =
desligar).

⚠️ Flag própria: `is_enabled()` gateia por `CONFIG_KARLA` (idêntico ao
`users_karla.is_enabled()` — NÃO reusa `OMI_DOCS_KARLA`).

Diferenças de semântica vs Firestore:
  - `get_meeting`/`process_conversation` (consumidor externo): mantém a mesma
    forma de retorno — dict com `data['id'] = doc_id` injetado, IDÊNTICO ao
    original (`doc.to_dict()` + `data['id'] = doc.id`).
  - `get_meeting_id_by_calendar_event`: `where(calendar_event_id).where(
    calendar_source).limit(1)` vira `k.listar(filtro={...}, limite=1)`
    (jsonb containment com 2 campos — equivale ao AND do original).
  - `list_meetings`: `order_by('start_time', DESC)` + filtros opcionais de
    range (`>=`/`<=`) viram `k.listar` (sem filtro de range no doc-store) +
    filtro/sort CLIENT-SIDE por `start_time` (aceita string ISO ou datetime,
    comparação por ISO string funciona pois `_serializar` grava datetimes
    como ISO-8601 com tz, que ordena lexicograficamente igual a
    cronologicamente).
  - `get_meetings_in_time_range`: overlap query (`start_time < end`, `end_time
    > start`) vira filtro client-side idêntico, sort asc, limit 10 (como o
    original).
  - `delete_old_meetings`: `where('end_time', '<', before_date)` + batch
    delete vira `k.listar` + filtro client-side + `k.deletar` por doc_id (sem
    batch real no doc-store — chamado um a um, mesma contagem devolvida).
  - `update_meeting`: `doc_ref.update(...)` (falha se doc não existir) vira
    `k.patch` (que já devolve None se 404 — sem raise, fail-open igual ao
    resto do F4.5; o original deixaria a exceção do Firestore subir, então
    aqui documentamos a divergência: update em doc inexistente é NO-OP
    silencioso em vez de exception).

Filosofia de erro: herdada do mold — erros de rede logam (warning+sanitize no
cliente `omi_docs_karla`) e a função devolve o DEFAULT/NEUTRO do original
(None/[]/0), nunca raise.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from database import omi_docs_karla as k

_MEETINGS = "meetings"


def is_enabled() -> bool:
    return (
        os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def create_meeting(uid: str, meeting_data: Dict) -> str:
    """Cria uma reunião nova. doc_id = uuid4 gerado aqui (equivale ao
    auto-id do `.document()` sem argumento no original).

    NOTE: Times should already be in UTC before calling this function.
    """
    now = datetime.now(timezone.utc)
    meeting_data = dict(meeting_data)
    meeting_data['created_at'] = now
    meeting_data['synced_at'] = now

    meeting_id = str(uuid.uuid4())
    meeting_data['id'] = meeting_id
    k.upsert(_MEETINGS, meeting_id, meeting_data, criado_em=now)

    return meeting_id


def update_meeting(uid: str, meeting_id: str, meeting_data: Dict) -> None:
    """
    Update an existing calendar meeting.

    NOTE: Times should already be in UTC before calling this function.
    Divergência vs original: NO-OP silencioso se o doc não existir (o
    original deixaria `doc_ref.update()` lançar; aqui fail-open, como o
    resto dos shims F4.5)."""
    meeting_data = dict(meeting_data)
    meeting_data['synced_at'] = datetime.now(timezone.utc)
    k.patch(_MEETINGS, meeting_id, meeting_data)


def get_meeting(uid: str, meeting_id: str) -> Optional[Dict]:
    """Get a calendar meeting by its doc_id. Injeta `id` na resposta, como o
    original."""
    data = k.obter(_MEETINGS, meeting_id)
    if data is None:
        return None
    data = dict(data)
    data['id'] = meeting_id
    return data


def get_meeting_id_by_calendar_event(uid: str, calendar_event_id: str, calendar_source: str) -> Optional[str]:
    """
    Find a meeting by its external calendar event ID and source.
    Returns the doc_id if found, None otherwise.
    """
    docs = k.listar(
        _MEETINGS,
        filtro={"calendar_event_id": calendar_event_id, "calendar_source": calendar_source},
        limite=1,
    )
    if docs and isinstance(docs[0], dict):
        return docs[0].get('id')
    return None


def list_meetings(
    uid: str, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None, limit: int = 50
) -> List[Dict]:
    """
    List calendar meetings, optionally filtered by date range.
    Returns meetings sorted by start_time descending.
    """
    docs = k.listar(_MEETINGS, limite=100000)
    meetings = [dict(d) for d in docs if isinstance(d, dict)]

    if start_date is not None:
        start_iso = _iso(start_date)
        meetings = [m for m in meetings if _iso(m.get('start_time')) >= start_iso]
    if end_date is not None:
        end_iso = _iso(end_date)
        meetings = [m for m in meetings if _iso(m.get('start_time')) <= end_iso]

    meetings.sort(key=lambda m: _iso(m.get('start_time')), reverse=True)
    return meetings[:limit]


def delete_meeting(uid: str, meeting_id: str) -> None:
    """Delete a calendar meeting"""
    k.deletar(_MEETINGS, meeting_id)


def delete_old_meetings(uid: str, before_date: datetime) -> int:
    """
    Delete meetings that ended before a certain date.
    Returns the number of meetings deleted.
    """
    before_iso = _iso(before_date)
    docs = k.listar(_MEETINGS, limite=100000)

    deleted_count = 0
    for data in docs:
        if not isinstance(data, dict):
            continue
        if _iso(data.get('end_time')) < before_iso:
            meeting_id = data.get('id')
            if meeting_id and k.deletar(_MEETINGS, meeting_id):
                deleted_count += 1

    return deleted_count


def get_meetings_in_time_range(uid: str, start_time: datetime, end_time: datetime) -> List[Dict]:
    """
    Find meetings that overlap with the given time range.
    A meeting overlaps if: meeting.start_time < range.end_time AND meeting.end_time > range.start_time

    Returns meetings sorted by start_time ascending, capped at 10 (as the original).
    """
    start_iso = _iso(start_time)
    end_iso = _iso(end_time)

    docs = k.listar(_MEETINGS, limite=100000)
    meetings = []
    for data in docs:
        if not isinstance(data, dict):
            continue
        m_start = _iso(data.get('start_time'))
        m_end = _iso(data.get('end_time'))
        if m_start < end_iso and m_end > start_iso:
            meetings.append(data)

    meetings.sort(key=lambda m: _iso(m.get('start_time')))
    return meetings[:10]


def _iso(value) -> str:
    """Normaliza datetime/string/None pra string ISO comparável
    lexicograficamente (mesma convenção de `_serializar`/`_iso` do resto do
    F4.5 — datetimes gravados com tz ordenam igual a cronologicamente)."""
    if value is None:
        return ''
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
