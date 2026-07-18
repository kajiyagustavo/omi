"""Journal summaries sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/journal_summaries.py.

F4.2/F4.4 (Omi self-hosted): o resumo diário do "Jornal do dia" (web UI) sai do
Firestore e mora no doc-store genérico do memory-service (via
`omi_docs_karla`), coleção `journal_summaries`. Este módulo replica as funções
PÚBLICAS de database/journal_summaries.py falando com o cliente genérico.
Ativação por env `OMI_DOCS_KARLA=true` (ver rodapé de
database/journal_summaries.py); com a flag off, nada muda (rollback =
desligar).

Diferença de semântica vs Firestore: doc_id == date ('YYYY-MM-DD') — o
original usa `.document(date)` diretamente, então o mapeamento é 1:1.

Filosofia de erro: herdada do cliente — erros de rede logam (warning+sanitize)
e devolvem valor neutro, pra não derrubar o app.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from database import omi_docs_karla as k

logger = logging.getLogger(__name__)

_JOURNAL_SUMMARIES = "journal_summaries"


def get_journal_summary(uid: str, date: str) -> Optional[dict]:
    """GET por date (doc_id), ou None."""
    return k.obter(_JOURNAL_SUMMARIES, date)


def set_journal_summary(uid: str, date: str, summary: str) -> dict:
    """Upsert em `journal_summaries` (doc_id = date). Devolve o doc gravado,
    igual ao original."""
    data = {
        'date': date,
        'summary': summary,
        'updated_at': datetime.now(timezone.utc),
    }
    k.upsert(_JOURNAL_SUMMARIES, date, data, criado_em=data['updated_at'])
    return data


def delete_journal_summary(uid: str, date: str) -> None:
    """Deleta o resumo do dia (doc_id = date)."""
    k.deletar(_JOURNAL_SUMMARIES, date)
