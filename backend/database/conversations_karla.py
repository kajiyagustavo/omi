"""Conversas sobre a MEMÓRIA UNIFICADA (Karla) — drop-in do database/conversations.py.

F4.1 (Omi self-hosted): as conversas saem do Firestore e moram no Postgres
próprio (memory-service, tabela `conversas_omi`, `dados` JSONB = o dict Omi
completo). Este módulo implementa as mesmas funções públicas consumidas pelos
routers, falando com a API REST `/u/<token>/conversas-omi`. Ativação por env
`CONVERSAS_KARLA=true` (ver rodapé de database/conversations.py); com a flag
off, nada muda (rollback = desligar).

Diferenças de semântica vs Firestore:
  - Karla guarda o dict Omi INTEIRO em `dados` (texto claro — sem data
    protection level / criptografia; `data_protection_level` é ignorado).
  - Photos vêm EMBUTIDAS em `dados['photos']` (o Firestore usava subcoleção +
    decorator `@with_photos`); portanto o retorno já traz photos, sem chamada
    extra. As leituras devolvem os dicts `dados` como estão (callers rodam o
    próprio model_validate; strings ISO parseiam bem no pydantic).
  - Datetimes dos dicts (vindos de `.dict()` de pydantic ou de leituras) são
    serializados pra ISO-8601 (com timezone) antes de POST/PATCH — nunca
    `default=str`, que perde a disciplina de formato/tz.

⚠️ Trap do merge RASO: o PATCH `dados_merge` substitui chaves de topo por
inteiro (não faz deep-merge). Qualquer função que atualiza uma estrutura
ANINHADA (model_transcripts, model_emotions, photos) precisa GET + merge
client-side + PATCH da chave de topo inteira. Está documentado função a função.

Filosofia de erro: a Karla é fonte de verdade, mas para NÃO derrubar o app em
caso de indisponibilidade da rede, erros são logados (logger.warning +
sanitize) e a função devolve um valor NEUTRO (lista vazia / None / 0), como o
mold memories_karla faz para leituras. Escritas idem: logam e retornam.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

import utils.other.hume as hume
from models.audio_file import AudioFile
from models.conversation_enums import PostProcessingModel
from utils.log_sanitizer import sanitize
from utils.other.storage import list_audio_chunks

logger = logging.getLogger(__name__)

_TIMEOUT = 30


def is_enabled() -> bool:
    return (
        os.getenv("CONVERSAS_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def _base() -> str:
    url = os.getenv("MEMORIA_UNIFICADA_URL", "").rstrip("/")
    tok = os.getenv("MEMORIA_UNIFICADA_TOKEN", "")
    return f"{url}/u/{tok}"


def _serializar(obj: Any) -> Any:
    """Deep-converte datetimes → ISO-8601 (com tz) preservando o resto. Usado
    antes de POST/PATCH. NÃO usar json default=str: perde a disciplina de tz."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serializar(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serializar(v) for v in obj]
    return obj


def _iso(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _iso_ts(ts) -> Optional[str]:
    """Unix timestamp (segundos) ou None → ISO UTC ou None."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _req(metodo: str, path: str, *, json_body=None, params=None):
    r = requests.request(metodo, _base() + path, json=json_body, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json() if r.status_code != 204 else None


def _get(cid: str) -> Optional[dict]:
    """GET /conversas-omi/{cid} cru → dados | None (404). NÃO neutraliza outros
    erros: usado internamente por funções de merge que precisam do doc real."""
    try:
        return _req("GET", f"/conversas-omi/{cid}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise


# ── READ ─────────────────────────────────────────────────────────────────────


def get_conversation(uid, conversation_id) -> Optional[dict]:
    """GET /conversas-omi/{id} → dados (photos já embutidas) | None."""
    try:
        return _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.get_conversation: {sanitize(str(e))}")
        return None


def _params_lista(
    include_discarded: bool,
    statuses,
    start_date,
    end_date,
    categories,
    folder_id,
    starred,
    limit,
    offset,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limite": limit, "offset": offset}
    params["incluir_descartadas"] = "true" if include_discarded else "false"
    if statuses:
        params["status_in"] = ",".join(str(getattr(s, "value", s)) for s in statuses)
    if categories:
        params["categorias"] = ",".join(str(getattr(c, "value", c)) for c in categories)
    if folder_id:
        params["folder_id"] = folder_id
    if starred is not None:
        params["starred"] = "true" if starred else "false"
    if start_date:
        params["criado_de"] = _iso(start_date)
    if end_date:
        params["criado_ate"] = _iso(end_date)
    return params


def get_conversations(
    uid: str,
    limit: int = 100,
    offset: int = 0,
    include_discarded: bool = False,
    statuses: List[str] = [],
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    categories: Optional[List[str]] = None,
    folder_id: Optional[str] = None,
    starred: Optional[bool] = None,
) -> List[dict]:
    """GET /conversas-omi. Photos já vêm embutidas em cada `dados`."""
    params = _params_lista(
        include_discarded, statuses, start_date, end_date, categories, folder_id, starred, limit, offset
    )
    try:
        r = _req("GET", "/conversas-omi", params=params)
        return r.get("conversas", [])
    except Exception as e:
        logger.warning(f"conversas_karla.get_conversations: {sanitize(str(e))}")
        return []


def get_conversations_without_photos(
    uid: str,
    limit: int = 100,
    offset: int = 0,
    include_discarded: bool = False,
    statuses: List[str] = [],
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    categories: Optional[List[str]] = None,
    folder_id: Optional[str] = None,
    starred: Optional[bool] = None,
) -> List[dict]:
    """Igual a get_conversations — na Karla photos vêm baratas embutidas em
    `dados`, então não há ganho em omiti-las; assinatura mantida por contrato."""
    return get_conversations(
        uid,
        limit=limit,
        offset=offset,
        include_discarded=include_discarded,
        statuses=statuses,
        start_date=start_date,
        end_date=end_date,
        categories=categories,
        folder_id=folder_id,
        starred=starred,
    )


def get_conversations_count(uid: str, include_discarded: bool = False, statuses: List[str] = []) -> int:
    params: Dict[str, Any] = {"incluir_descartadas": "true" if include_discarded else "false"}
    if statuses:
        params["status_in"] = ",".join(str(getattr(s, "value", s)) for s in statuses)
    try:
        r = _req("GET", "/conversas-omi/contar", params=params)
        return int(r.get("total", 0))
    except Exception as e:
        logger.warning(f"conversas_karla.get_conversations_count: {sanitize(str(e))}")
        return 0


def get_conversations_by_id(uid, conversation_ids) -> List[dict]:
    ids = [str(c) for c in conversation_ids]
    if not ids:
        return []
    try:
        r = _req("GET", "/conversas-omi", params={"ids": ",".join(ids)})
        # o original descarta descartadas
        return [c for c in r.get("conversas", []) if not c.get("discarded")]
    except Exception as e:
        logger.warning(f"conversas_karla.get_conversations_by_id: {sanitize(str(e))}")
        return []


def iter_all_conversations(uid: str, batch_size: int = 400, include_discarded: bool = True):
    """Generator paginando GET /conversas-omi (ordem desc, limite=batch_size)."""
    offset = 0
    while True:
        params: Dict[str, Any] = {
            "incluir_descartadas": "true" if include_discarded else "false",
            "limite": batch_size,
            "offset": offset,
            "ordem": "desc",
        }
        try:
            r = _req("GET", "/conversas-omi", params=params)
            batch = r.get("conversas", [])
        except Exception as e:
            logger.warning(f"conversas_karla.iter_all_conversations: {sanitize(str(e))}")
            return
        yield from batch
        if len(batch) < batch_size:
            break
        offset += batch_size


def get_in_progress_conversation(uid: str) -> Optional[dict]:
    try:
        r = _req("GET", "/conversas-omi", params={"status_in": "in_progress", "limite": 1})
        convs = r.get("conversas", [])
        return convs[0] if convs else None
    except Exception as e:
        logger.warning(f"conversas_karla.get_in_progress_conversation: {sanitize(str(e))}")
        return None


def get_processing_conversations(uid: str) -> List[dict]:
    try:
        r = _req("GET", "/conversas-omi", params={"status_in": "processing"})
        return r.get("conversas", [])
    except Exception as e:
        logger.warning(f"conversas_karla.get_processing_conversations: {sanitize(str(e))}")
        return []


def get_last_completed_conversation(uid: str) -> Optional[dict]:
    try:
        r = _req("GET", "/conversas-omi", params={"status_in": "completed", "limite": 1})
        convs = r.get("conversas", [])
        return convs[0] if convs else None
    except Exception as e:
        logger.warning(f"conversas_karla.get_last_completed_conversation: {sanitize(str(e))}")
        return None


def get_closest_conversation_to_timestamps(uid: str, start_timestamp: int, end_timestamp: int) -> Optional[dict]:
    """Porta a lógica do original: janela ±2min em `finished_at`/`started_at`,
    depois escolhe a conversa com menor diff de start/end. Como a Karla filtra
    por `criado_de/ate`, buscamos por janela ampla e refinamos client-side."""
    start_threshold = datetime.fromtimestamp(start_timestamp, tz=timezone.utc) - timedelta(minutes=2)
    end_threshold = datetime.fromtimestamp(end_timestamp, tz=timezone.utc) + timedelta(minutes=2)
    try:
        r = _req(
            "GET",
            "/conversas-omi",
            params={
                "criado_de": start_threshold.isoformat(),
                "criado_ate": end_threshold.isoformat(),
                "ordem": "desc",
            },
        )
        conversations = r.get("conversas", [])
    except Exception as e:
        logger.warning(f"conversas_karla.get_closest_conversation_to_timestamps: {sanitize(str(e))}")
        return None

    # filtra pela janela finished_at >= start_threshold e started_at <= end_threshold
    candidatos = []
    for c in conversations:
        fin = c.get("finished_at")
        sta = c.get("started_at")
        if fin is None or sta is None:
            continue
        fin_dt = _parse_dt(fin)
        sta_dt = _parse_dt(sta)
        if fin_dt is None or sta_dt is None:
            continue
        if fin_dt >= start_threshold and sta_dt <= end_threshold:
            candidatos.append((c, sta_dt, fin_dt))

    if not candidatos:
        return None

    closest = None
    min_diff = float("inf")
    for c, sta_dt, fin_dt in candidatos:
        diff1 = abs(sta_dt.timestamp() - start_timestamp)
        diff2 = abs(fin_dt.timestamp() - end_timestamp)
        if diff1 < min_diff or diff2 < min_diff:
            min_diff = min(diff1, diff2)
            closest = c
    return closest


def _parse_dt(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ── WRITE / CRUD ─────────────────────────────────────────────────────────────


def upsert_conversation(uid: str, conversation_data: dict):
    """POST /conversas-omi (upsert por dados['id']). Remove `audio_base64_url`
    e `photos` como o original; serializa datetimes → ISO."""
    data = dict(conversation_data)
    data.pop("audio_base64_url", None)
    data.pop("photos", None)
    corpo = _serializar(data)
    try:
        _req("POST", "/conversas-omi", json_body=corpo)
    except Exception as e:
        logger.warning(f"conversas_karla.upsert_conversation: {sanitize(str(e))}")


def update_conversation(uid: str, conversation_id: str, update_data: dict):
    """PATCH dados_merge (merge RASO — nested keys substituem por inteiro)."""
    corpo = _serializar(update_data)
    _patch_merge(conversation_id, corpo, "update_conversation")


def _patch_merge(conversation_id: str, merge: dict, ctx: str, silencioso_404: bool = True):
    try:
        _req("PATCH", f"/conversas-omi/{conversation_id}", json_body={"dados_merge": merge})
    except requests.HTTPError as e:
        if silencioso_404 and e.response is not None and e.response.status_code == 404:
            return
        logger.warning(f"conversas_karla.{ctx}: {sanitize(str(e))}")
    except Exception as e:
        logger.warning(f"conversas_karla.{ctx}: {sanitize(str(e))}")


def delete_conversation(uid, conversation_id):
    try:
        _req("DELETE", f"/conversas-omi/{conversation_id}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return
        logger.warning(f"conversas_karla.delete_conversation: {sanitize(str(e))}")
    except Exception as e:
        logger.warning(f"conversas_karla.delete_conversation: {sanitize(str(e))}")


def update_conversation_title(uid: str, conversation_id: str, title: str):
    """MERGE RASO trap: `dados_merge` faz merge raso de chaves de TOPO — o
    memory-service NÃO interpreta notação de ponto (`structured.title` viraria
    uma chave literal bogus, sem tocar o `structured.title` real). GET doc →
    merge sob structured.title client-side (preservando os demais sub-campos
    de structured) → PATCH a chave `structured` inteira."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.update_conversation_title: {sanitize(str(e))}")
        return
    if dados is None:
        return
    structured = dict(dados.get("structured", {}) or {})
    structured["title"] = title
    _patch_merge(conversation_id, {"structured": _serializar(structured)}, "update_conversation_title")


def update_conversation_status(uid: str, conversation_id: str, status: str):
    _patch_merge(conversation_id, {"status": str(getattr(status, "value", status))}, "update_conversation_status")


def set_conversation_as_discarded(uid: str, conversation_id: str):
    _patch_merge(conversation_id, {"discarded": True}, "set_conversation_as_discarded")


def set_conversation_visibility(uid: str, conversation_id: str, visibility: str):
    _patch_merge(conversation_id, {"visibility": visibility}, "set_conversation_visibility")


def set_conversation_starred(uid: str, conversation_id: str, starred: bool):
    _patch_merge(conversation_id, {"starred": starred}, "set_conversation_starred")


def update_conversation_finished_at(uid: str, conversation_id: str, finished_at: datetime):
    _patch_merge(conversation_id, {"finished_at": _iso(finished_at)}, "update_conversation_finished_at")


def update_conversation_events(uid: str, conversation_id: str, events: List[dict]):
    """MERGE RASO trap (ver update_conversation_title): shallow-merge no serviço
    → GET+merge no cliente. GET doc → merge sob structured.events → PATCH a
    chave `structured` inteira, preservando os demais sub-campos."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.update_conversation_events: {sanitize(str(e))}")
        return
    if dados is None:
        return
    structured = dict(dados.get("structured", {}) or {})
    structured["events"] = _serializar(events)
    _patch_merge(conversation_id, {"structured": _serializar(structured)}, "update_conversation_events")


def update_conversation_action_items(uid: str, conversation_id: str, action_items: List[dict]):
    """MERGE RASO trap (ver update_conversation_title): shallow-merge no serviço
    → GET+merge no cliente. GET doc → merge sob structured.action_items → PATCH
    a chave `structured` inteira, preservando os demais sub-campos."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.update_conversation_action_items: {sanitize(str(e))}")
        return
    if dados is None:
        return
    structured = dict(dados.get("structured", {}) or {})
    structured["action_items"] = _serializar(action_items)
    _patch_merge(conversation_id, {"structured": _serializar(structured)}, "update_conversation_action_items")


def set_postprocessing_status(
    uid: str,
    conversation_id: str,
    status,
    fail_reason: str = None,
    model=None,
):
    """MERGE RASO trap (ver update_conversation_title): shallow-merge no serviço
    → GET+merge no cliente. GET doc → merge sob postprocessing.{status,model,
    fail_reason} → PATCH a chave `postprocessing` inteira, preservando demais
    sub-campos. Se o doc não existir, loga e retorna silenciosamente (mirror do
    NotFound tolerance do original)."""
    if model is None:
        model = PostProcessingModel.fal_whisperx
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.set_postprocessing_status: {sanitize(str(e))}")
        return
    if dados is None:
        logger.warning(
            f"conversas_karla.set_postprocessing_status: conversa {sanitize(conversation_id)} não encontrada"
        )
        return
    postprocessing = dict(dados.get("postprocessing", {}) or {})
    postprocessing["status"] = str(getattr(status, "value", status))
    postprocessing["model"] = str(getattr(model, "value", model))
    postprocessing["fail_reason"] = fail_reason
    _patch_merge(conversation_id, {"postprocessing": _serializar(postprocessing)}, "set_postprocessing_status")


def update_conversation_segments(
    uid: str,
    conversation_id: str,
    segments: List[dict],
    finished_at: datetime = None,
    data_protection_level: str = None,
):
    """PATCH transcript_segments + transcript_segments_compressed=False. Ignora
    data_protection_level (Karla = texto claro). 404 → silencioso (mirror do
    NotFound swallow do original)."""
    merge: Dict[str, Any] = {
        "transcript_segments": _serializar(segments),
        "transcript_segments_compressed": False,
    }
    if finished_at:
        merge["finished_at"] = _iso(finished_at)
    _patch_merge(conversation_id, merge, "update_conversation_segments", silencioso_404=True)


def update_conversation_segment_text(uid: str, conversation_id: str, segment_id: str, text: str) -> str:
    """GET doc → edita o segment na lista → PATCH. Retorna os mesmos códigos do
    original: 'ok' | 'not_found' | 'locked' | 'segment_not_found'."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.update_conversation_segment_text: {sanitize(str(e))}")
        return "not_found"
    if not dados:
        return "not_found"
    if dados.get("is_locked", False):
        return "locked"
    segments = dados.get("transcript_segments", []) or []
    found = False
    for segment in segments:
        if isinstance(segment, dict) and segment.get("id") == segment_id:
            segment["text"] = text
            found = True
            break
    if not found:
        return "segment_not_found"
    _patch_merge(
        conversation_id,
        {"transcript_segments": _serializar(segments), "transcript_segments_compressed": False},
        "update_conversation_segment_text",
    )
    return "ok"


def unlock_all_conversations(uid: str):
    """GET filtro={"is_locked": true} → PATCH cada um is_locked=False."""
    try:
        r = _req("GET", "/conversas-omi", params={"filtro": json.dumps({"is_locked": True}), "limite": 1000})
        convs = r.get("conversas", [])
    except Exception as e:
        logger.warning(f"conversas_karla.unlock_all_conversations: {sanitize(str(e))}")
        return
    for c in convs:
        cid = c.get("id")
        if cid:
            _patch_merge(cid, {"is_locked": False}, "unlock_all_conversations")
    logger.info(f"conversas_karla: unlocked {len(convs)} conversations")


# ── ACTION ITEMS ─────────────────────────────────────────────────────────────


def get_action_items(
    uid: str,
    limit: int = 100,
    offset: int = 0,
    include_completed: bool = True,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
):
    """Porta a lógica do original: lista completed na janela, flatten de
    structured.action_items com metadados, ordena por conversa mais nova,
    pagina no fim."""
    params: Dict[str, Any] = {"status_in": "completed", "ordem": "desc", "limite": 10000}
    if start_date:
        params["criado_de"] = _iso(start_date)
    if end_date:
        params["criado_ate"] = _iso(end_date)
    try:
        r = _req("GET", "/conversas-omi", params=params)
        raw_conversations = r.get("conversas", [])
    except Exception as e:
        logger.warning(f"conversas_karla.get_action_items: {sanitize(str(e))}")
        return []

    conversations = []
    for conversation_data in raw_conversations:
        structured = conversation_data.get("structured", {}) or {}
        if structured.get("action_items", []):
            conversations.append(conversation_data)

    action_items = []
    for conversation in conversations:
        conversation_id = conversation["id"]
        conversation_title = (conversation.get("structured", {}) or {}).get("title", "Untitled")
        conv_created = _parse_dt(conversation["created_at"])
        conversation_created_at = _ensure_tz(conv_created) if conv_created else datetime.now(tz=timezone.utc)

        raw_items = (conversation.get("structured", {}) or {}).get("action_items", [])
        for idx, item in enumerate(raw_items):
            if isinstance(item, dict) and item.get("deleted", False):
                continue
            is_completed = item.get("completed", False) if isinstance(item, dict) else False
            if not include_completed and is_completed:
                continue

            created_at = None
            completed_at = None
            if isinstance(item, dict):
                created_at = _parse_dt(item.get("created_at")) if item.get("created_at") else None
                completed_at = _parse_dt(item.get("completed_at")) if item.get("completed_at") else None
            if created_at is not None:
                created_at = _ensure_tz(created_at)
            if completed_at is not None:
                completed_at = _ensure_tz(completed_at)
            if created_at is None:
                created_at = conversation_created_at
            if is_completed and completed_at is None:
                completed_at = conversation_created_at

            action_items.append(
                {
                    "id": f"{conversation_id}_{idx}",
                    "conversation_id": conversation_id,
                    "conversation_title": conversation_title,
                    "conversation_created_at": conversation_created_at,
                    "index": idx,
                    "description": item.get("description", item) if isinstance(item, dict) else item,
                    "completed": is_completed,
                    "deleted": item.get("deleted", False) if isinstance(item, dict) else False,
                    "created_at": created_at,
                    "completed_at": completed_at,
                }
            )

    action_items.sort(key=lambda x: -x["conversation_created_at"].timestamp())
    return action_items[offset : offset + limit]


# ── PHOTOS (OpenGlass) ───────────────────────────────────────────────────────


def get_conversation_photos(uid: str, conversation_id: str) -> List[dict]:
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.get_conversation_photos: {sanitize(str(e))}")
        return []
    if not dados:
        return []
    return dados.get("photos", []) or []


def store_conversation_photos(uid: str, conversation_id: str, photos):
    """MERGE RASO trap: GET doc → append em photos → PATCH a chave inteira.
    photos são pydantic → .dict()."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.store_conversation_photos: {sanitize(str(e))}")
        return
    existentes = (dados.get("photos", []) or []) if dados else []
    novas = []
    for photo in photos:
        data = photo.dict() if hasattr(photo, "dict") else dict(photo)
        if not data.get("id"):
            data["id"] = str(uuid.uuid4())
        novas.append(data)
    _patch_merge(conversation_id, {"photos": _serializar(existentes + novas)}, "store_conversation_photos")


def delete_conversation_photos(uid: str, conversation_id: str) -> int:
    """PATCH photos=[]. Retorna a contagem anterior (como o original retorna o
    número de deletadas)."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.delete_conversation_photos: {sanitize(str(e))}")
        return 0
    anteriores = len((dados.get("photos", []) or []) if dados else [])
    _patch_merge(conversation_id, {"photos": []}, "delete_conversation_photos")
    return anteriores


# ── POSTPROCESSING (model transcripts / emotions) ────────────────────────────


def store_model_segments_result(uid: str, conversation_id: str, model_name: str, segments):
    """MERGE RASO trap: GET doc → merge sob model_transcripts[model_name] →
    PATCH a chave model_transcripts inteira."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.store_model_segments_result: {sanitize(str(e))}")
        return
    if dados is None:
        return
    model_transcripts = dict(dados.get("model_transcripts", {}) or {})
    model_transcripts[model_name] = [s.dict() if hasattr(s, "dict") else dict(s) for s in segments]
    _patch_merge(
        conversation_id,
        {"model_transcripts": _serializar(model_transcripts)},
        "store_model_segments_result",
    )


def store_model_emotion_predictions_result(uid: str, conversation_id: str, model_name: str, predictions):
    """MERGE RASO trap: GET doc → merge sob model_emotions[model_name] → PATCH
    a chave model_emotions inteira."""
    now = datetime.now(tz=timezone.utc)
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.store_model_emotion_predictions_result: {sanitize(str(e))}")
        return
    if dados is None:
        return
    model_emotions = dict(dados.get("model_emotions", {}) or {})
    linhas = []
    for prediction in predictions:
        linhas.append(
            {
                "created_at": now.isoformat(),
                "start": prediction.time[0],
                "end": prediction.time[1],
                "emotions": json.dumps(hume.HumePredictionEmotionResponseModel.to_multi_dict(prediction.emotions)),
            }
        )
    model_emotions[model_name] = linhas
    _patch_merge(
        conversation_id,
        {"model_emotions": _serializar(model_emotions)},
        "store_model_emotion_predictions_result",
    )


def get_conversation_transcripts_by_model(uid: str, conversation_id: str) -> dict:
    """GET doc → model_transcripts, mapeando as chaves de coleção do Firestore
    (deepgram_streaming etc) pras chaves de retorno do original."""
    try:
        dados = _get(conversation_id)
    except Exception as e:
        logger.warning(f"conversas_karla.get_conversation_transcripts_by_model: {sanitize(str(e))}")
        dados = None
    mt = (dados.get("model_transcripts", {}) or {}) if dados else {}

    def _sorted(lst):
        return list(sorted(lst or [], key=lambda x: x.get("start", 0)))

    return {
        "deepgram": _sorted(mt.get("deepgram_streaming")),
        "soniox": _sorted(mt.get("soniox_streaming")),
        "speechmatics": _sorted(mt.get("speechmatics_streaming")),
        "whisperx": _sorted(mt.get("fal_whisperx")),
    }


# ── AUDIO FILES (lógica não-Firestore preservada) ────────────────────────────


def create_audio_files_from_chunks(uid: str, conversation_id: str) -> List[AudioFile]:
    """Idêntico ao original: agrupa chunks por gap > 90s. Não toca no
    Firestore/Karla — apenas monta AudioFile a partir dos chunks do storage."""
    chunks = list_audio_chunks(uid, conversation_id)
    if not chunks:
        return []

    audio_files = []
    current_group = []
    gap_threshold = 90

    for chunk in chunks:
        if not current_group:
            current_group.append(chunk)
        else:
            prev_chunk = current_group[-1]
            time_gap = chunk["timestamp"] - prev_chunk["timestamp"]
            if time_gap > gap_threshold:
                audio_file = _finalize_audio_file_group(uid, conversation_id, current_group, audio_files)
                if audio_file:
                    audio_files.append(audio_file)
                current_group = [chunk]
            else:
                current_group.append(chunk)

    if current_group:
        audio_file = _finalize_audio_file_group(uid, conversation_id, current_group, audio_files)
        if audio_file:
            audio_files.append(audio_file)

    return audio_files


def _finalize_audio_file_group(
    uid: str, conversation_id: str, chunk_group: List[dict], existing_files: List[AudioFile]
) -> Optional[AudioFile]:
    if not chunk_group:
        return None

    file_id = str(uuid.uuid4())
    timestamps = [chunk["timestamp"] for chunk in chunk_group]
    started_at = datetime.fromtimestamp(chunk_group[0]["timestamp"], tz=timezone.utc)
    last_chunk_start = datetime.fromtimestamp(chunk_group[-1]["timestamp"], tz=timezone.utc)
    last_chunk_size = chunk_group[-1].get("size", 0)
    last_chunk_duration = last_chunk_size / 16000.0 if last_chunk_size > 0 else 5.0
    duration = (last_chunk_start - started_at).total_seconds() + last_chunk_duration

    return AudioFile(
        id=file_id,
        uid=uid,
        conversation_id=conversation_id,
        chunk_timestamps=timestamps,
        provider="gcp",
        started_at=started_at,
        duration=duration,
    )


# ── HELPERS PRA VECTOR_DB GATES (Task 7) ─────────────────────────────────────


def buscar_conversas_ids(query: str, starts_at=None, ends_at=None, k: int = 5) -> List[str]:
    """Busca semântica de conversas → lista de ids. starts_at/ends_at são unix
    timestamps (ou None) → ISO UTC. Chama GET /conversas-omi/buscar."""
    params: Dict[str, Any] = {"q": query, "limite": k}
    de = _iso_ts(starts_at)
    ate = _iso_ts(ends_at)
    if de:
        params["criado_de"] = de
    if ate:
        params["criado_ate"] = ate
    try:
        r = _req("GET", "/conversas-omi/buscar", params=params)
        return [str(i) for i in r.get("ids", [])]
    except Exception as e:
        logger.warning(f"conversas_karla.buscar_conversas_ids: {sanitize(str(e))}")
        return []
