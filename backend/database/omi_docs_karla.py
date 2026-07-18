"""Cliente genérico do doc-store REST da MEMÓRIA UNIFICADA (Karla).

F4.2/F4.3 (Omi self-hosted): o memory-service expõe um armazém genérico de
documentos JSON keyed por coleção (`/u/<token>/omi-docs/<colecao>`), onde cada
doc é `{"doc_id", "dados", "criado_em", "atualizado_em"}` e `dados` é um JSONB
livre. Este módulo é o cliente HTTP síncrono desse contrato, consumido pelos
shims por-domínio (ex: `chat_karla.py`) que traduzem as funções públicas do
Firestore pra chamadas aqui.

Coleções válidas: messages, chat_files, chat_sessions, daily_summaries,
journal_summaries, folders, goals, goal_history, knowledge_nodes,
knowledge_edges.

⚠️ Trap do merge RASO: `patch()` (dados_merge) substitui chaves de TOPO por
inteiro — não faz deep-merge nem interpreta notação de ponto. Qualquer atualização
de estrutura ANINHADA (arrays como message_ids/file_ids) precisa GET + merge
client-side + PATCH da chave de topo inteira. Feito nos shims, não aqui.

Datetimes em `dados` são serializados pra ISO-8601 (com tz) via `_serializar`
(reusado de conversas_karla) antes de POST/PATCH — nunca `default=str`.

Filosofia de erro: erros de rede são logados (logger.warning + sanitize) e a
função devolve um valor NEUTRO ([], None, 0, False), pra NÃO derrubar o app em
caso de indisponibilidade da Karla. Espelha conversas_karla / memories_karla.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from database.conversations_karla import _base, _iso, _serializar
from utils.log_sanitizer import sanitize

logger = logging.getLogger(__name__)

_TIMEOUT = 30


def is_enabled() -> bool:
    return (
        os.getenv("OMI_DOCS_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def _req(metodo: str, path: str, *, json_body=None, params=None):
    r = requests.request(metodo, _base() + path, json=json_body, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json() if r.status_code != 204 else None


# ── WRITE ────────────────────────────────────────────────────────────────────


def upsert(colecao: str, doc_id: str, dados: dict, criado_em=None) -> bool:
    """POST /omi-docs/{colecao} (upsert por doc_id). `dados` é serializado
    (datetimes → ISO). `criado_em` opcional (datetime ou ISO string) → 201.
    Devolve True em sucesso, False em erro (logado)."""
    corpo: Dict[str, Any] = {"doc_id": doc_id, "dados": _serializar(dados)}
    if criado_em is not None:
        corpo["criado_em"] = _iso(criado_em)
    try:
        _req("POST", f"/omi-docs/{colecao}", json_body=corpo)
        return True
    except Exception as e:
        logger.warning(f"omi_docs_karla.upsert[{colecao}]: {sanitize(str(e))}")
        return False


def patch(colecao: str, doc_id: str, dados_merge: dict) -> Optional[dict]:
    """PATCH /omi-docs/{colecao}/{doc_id} com {"dados_merge": ...}. Devolve o
    `dados` atualizado, ou None em 404 (logado).

    ⚠️ Merge RASO: substitui chaves de TOPO por inteiro (sem deep-merge nem
    notação de ponto). Pra atualizar estrutura aninhada, o chamador deve
    GET + merge client-side + PATCH da chave inteira."""
    try:
        r = _req("PATCH", f"/omi-docs/{colecao}/{doc_id}", json_body={"dados_merge": _serializar(dados_merge)})
        return (r or {}).get("dados") if isinstance(r, dict) else None
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            logger.warning(f"omi_docs_karla.patch[{colecao}]: doc {sanitize(doc_id)} não encontrado (404)")
            return None
        logger.warning(f"omi_docs_karla.patch[{colecao}]: {sanitize(str(e))}")
        return None
    except Exception as e:
        logger.warning(f"omi_docs_karla.patch[{colecao}]: {sanitize(str(e))}")
        return None


def deletar(colecao: str, doc_id: str) -> bool:
    """DELETE /omi-docs/{colecao}/{doc_id} → 204. True em sucesso, False em
    404 ou erro (logado)."""
    try:
        _req("DELETE", f"/omi-docs/{colecao}/{doc_id}")
        return True
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return False
        logger.warning(f"omi_docs_karla.deletar[{colecao}]: {sanitize(str(e))}")
        return False
    except Exception as e:
        logger.warning(f"omi_docs_karla.deletar[{colecao}]: {sanitize(str(e))}")
        return False


def deletar_em_lote(colecao: str, filtro: Optional[dict] = None, ids: Optional[List[str]] = None) -> int:
    """DELETE /omi-docs/{colecao} com ?filtro= (jsonb containment) ou ?ids= (CSV)
    → {"deletados": N}. Devolve N, ou 0 em erro (logado)."""
    params: Dict[str, Any] = {}
    if filtro is not None:
        params["filtro"] = json.dumps(_serializar(filtro))
    if ids is not None:
        params["ids"] = ",".join(str(i) for i in ids)
    try:
        r = _req("DELETE", f"/omi-docs/{colecao}", params=params)
        return int((r or {}).get("deletados", 0))
    except Exception as e:
        logger.warning(f"omi_docs_karla.deletar_em_lote[{colecao}]: {sanitize(str(e))}")
        return 0


# ── READ ─────────────────────────────────────────────────────────────────────


def obter(colecao: str, doc_id: str) -> Optional[dict]:
    """GET /omi-docs/{colecao}/{doc_id} → o `dados` como armazenado, ou None
    (404 ou erro, logado). Não injeta doc_id: os shims cuidam de ids."""
    try:
        r = _req("GET", f"/omi-docs/{colecao}/{doc_id}")
        return (r or {}).get("dados") if isinstance(r, dict) else None
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        logger.warning(f"omi_docs_karla.obter[{colecao}]: {sanitize(str(e))}")
        return None
    except Exception as e:
        logger.warning(f"omi_docs_karla.obter[{colecao}]: {sanitize(str(e))}")
        return None


def listar(
    colecao: str,
    filtro: Optional[dict] = None,
    ids: Optional[List[str]] = None,
    criado_de=None,
    criado_ate=None,
    ordem: str = "desc",
    limite: int = 100,
    offset: int = 0,
) -> List[dict]:
    """GET /omi-docs/{colecao} → lista de `dados`. `filtro` é jsonb containment
    sobre dados; `ids` é CSV de doc_id; `criado_de`/`criado_ate` são ISO (aceita
    datetime). Devolve [] em erro (logado)."""
    params: Dict[str, Any] = {"ordem": ordem, "limite": limite, "offset": offset}
    if filtro is not None:
        params["filtro"] = json.dumps(_serializar(filtro))
    if ids is not None:
        params["ids"] = ",".join(str(i) for i in ids)
    if criado_de is not None:
        params["criado_de"] = _iso(criado_de)
    if criado_ate is not None:
        params["criado_ate"] = _iso(criado_ate)
    try:
        r = _req("GET", f"/omi-docs/{colecao}", params=params)
        docs = (r or {}).get("docs", []) if isinstance(r, dict) else []
        return [d.get("dados", d) if isinstance(d, dict) else d for d in docs]
    except Exception as e:
        logger.warning(f"omi_docs_karla.listar[{colecao}]: {sanitize(str(e))}")
        return []


def contar(colecao: str, filtro: Optional[dict] = None) -> int:
    """GET /omi-docs/{colecao}/contar → {"total": N}. 0 em erro (logado)."""
    params: Dict[str, Any] = {}
    if filtro is not None:
        params["filtro"] = json.dumps(_serializar(filtro))
    try:
        r = _req("GET", f"/omi-docs/{colecao}/contar", params=params)
        return int((r or {}).get("total", 0))
    except Exception as e:
        logger.warning(f"omi_docs_karla.contar[{colecao}]: {sanitize(str(e))}")
        return 0
