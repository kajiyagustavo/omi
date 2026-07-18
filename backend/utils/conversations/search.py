import logging
import math
import os
from datetime import datetime
from typing import Dict

import typesense

import database.conversations as conversations_db
from database import conversations_karla

logger = logging.getLogger(__name__)

# AIREC self-host: Typesense (busca full-text) é opcional. O upstream instancia o
# client no import e quebra o boot quando TYPESENSE_API_KEY não está definido
# (ConfigError). Guard no mesmo estilo do `database/vector_db.py` (Pinecone): sem
# chave, client=None e a busca fica desligada — não afeta o caminho de áudio ao vivo.
if os.getenv('TYPESENSE_API_KEY'):
    client = typesense.Client(
        {
            'nodes': [
                {'host': os.getenv('TYPESENSE_HOST'), 'port': os.getenv('TYPESENSE_HOST_PORT'), 'protocol': 'https'}
            ],
            'api_key': os.getenv('TYPESENSE_API_KEY'),
            'connection_timeout_seconds': 2,
        }
    )
else:
    client = None

# Cap defensivo pra janela de busca client-side (page * per_page) — evita pedir
# uma lista de ids gigante pra Karla quando o caller pagina muito fundo.
_MAX_KARLA_SEARCH_WINDOW = 100


def _search_conversations_karla_fallback(
    uid: str,
    query: str,
    page: int,
    per_page: int,
    start_date: int = None,
    end_date: int = None,
) -> Dict:
    """AIREC self-host sem Typesense: busca semântica via MEMÓRIA UNIFICADA
    (Karla). Paginação client-side sobre a lista de ids rankeados — a Karla não
    tem um endpoint de paginação por página, então buscamos uma janela (cap
    _MAX_KARLA_SEARCH_WINDOW) e fatiamos localmente."""
    window = min(page * per_page, _MAX_KARLA_SEARCH_WINDOW)
    ids = conversations_karla.buscar_conversas_ids(query, starts_at=start_date, ends_at=end_date, k=window)

    start_idx = (page - 1) * per_page
    end_idx = page * per_page
    page_ids = ids[start_idx:end_idx]

    docs = conversations_db.get_conversations_by_id(uid, page_ids)
    docs_by_id = {str(d.get('id')): d for d in docs}

    memories = []
    for cid in page_ids:
        doc = docs_by_id.get(cid)
        if doc is None:
            continue
        # Exclude locked conversations entirely to prevent inference leaks (same rule as Typesense path).
        if doc.get('is_locked', False):
            continue
        memories.append(doc)

    # Karla docs já carregam created_at/started_at/finished_at como strings ISO
    # (ver database/conversations_karla.py) — nada a converter aqui, ao contrário
    # do caminho Typesense (que guarda unix timestamps).

    has_more = len(ids) > page * per_page or len(ids) >= _MAX_KARLA_SEARCH_WINDOW
    return {
        'items': memories,
        'total_pages': page + 1 if has_more else page,
        'current_page': page,
        'per_page': per_page,
    }


def search_conversations(
    uid: str,
    query: str,
    page: int = 1,
    per_page: int = 10,
    include_discarded: bool = True,
    start_date: int = None,
    end_date: int = None,
) -> Dict:
    if client is None:
        if conversations_karla.is_enabled():
            return _search_conversations_karla_fallback(
                uid, query, page, per_page, start_date=start_date, end_date=end_date
            )
        logger.warning('busca de conversas desligada (sem Typesense e sem Karla)')
        return {
            'items': [],
            'total_pages': page,
            'current_page': page,
            'per_page': per_page,
        }

    try:

        filter_by = f'userId:={uid}'
        if not include_discarded:
            filter_by = filter_by + ' && discarded:=false'

        # Add date range filters if provided
        if start_date is not None:
            filter_by = filter_by + f' && created_at:>={start_date}'
        if end_date is not None:
            filter_by = filter_by + f' && created_at:<={end_date}'

        search_parameters = {
            'q': query,
            'query_by': 'structured.overview, structured.title',
            'filter_by': filter_by,
            'sort_by': 'created_at:desc',
            'per_page': per_page,
            'page': page,
        }

        results = client.collections['conversations'].documents.search(search_parameters)
        memories = []
        for item in results['hits']:
            doc = item['document']
            # Exclude locked conversations entirely to prevent inference leaks
            if doc.get('is_locked', False):
                continue
            doc['created_at'] = datetime.utcfromtimestamp(doc['created_at']).isoformat()
            doc['started_at'] = datetime.utcfromtimestamp(doc['started_at']).isoformat()
            doc['finished_at'] = datetime.utcfromtimestamp(doc['finished_at']).isoformat()
            memories.append(doc)
        # Derive total_pages only from visible (unlocked) items to prevent inference leaks.
        # is_locked is not a Typesense filter field, so exact global count is unavailable.
        has_more = len(memories) >= per_page
        return {
            'items': memories,
            'total_pages': page + 1 if has_more else page,
            'current_page': page,
            'per_page': per_page,
        }
    except Exception as e:
        raise Exception(f"Failed to search conversations: {str(e)}")
