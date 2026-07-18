"""Daily summaries sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/daily_summaries.py.

F4.2/F4.4 (Omi self-hosted): os resumos diários (Omi nightly `daily_summaries`
— headline/overview/highlights/action_items/...) saem do Firestore e moram no
doc-store genérico do memory-service (via `omi_docs_karla`), coleção
`daily_summaries`. Este módulo replica as funções PÚBLICAS de
database/daily_summaries.py falando com o cliente genérico. Ativação por env
`OMI_DOCS_KARLA=true` (ver rodapé de database/daily_summaries.py); com a flag
off, nada muda (rollback = desligar).

Diferenças de semântica vs Firestore:
  - doc_id == summary_data['id'] (o original usa `document(summary_data['id'])`
    — o chamador já traz o id pronto). `create_daily_summary` devolve esse id,
    igual ao original.
  - `get_daily_summary_by_date`: o original filtra `date == <string>` (campo
    `date` é sempre string 'YYYY-MM-DD', nunca datetime) com limit(1). Aqui é
    `listar(filtro={"date": date}, limite=1)` — mesmo formato de campo.
  - `get_daily_summaries`: o original ordena por `date` (string, desc) com
    filtros opcionais `start_date`/`end_date` (>=/<=). A Karla não expõe
    range-filter genérico sobre campos arbitrários do doc-store (só
    `criado_de`/`criado_ate`, que são sobre o timestamp de criação do doc, não
    sobre o campo `date`); portanto o filtro por range de data é replicado
    client-side após a listagem (busca uma janela maior e filtra em Python).

Filosofia de erro: herdada do cliente — erros de rede logam (warning+sanitize)
e devolvem valor neutro, pra não derrubar o app.
"""

import logging
from typing import List, Optional

from database import omi_docs_karla as k

logger = logging.getLogger(__name__)

_SUMMARIES = "daily_summaries"


def create_daily_summary(uid: str, summary_data: dict) -> str:
    """Upsert em `daily_summaries` (doc_id = summary_data['id']). Devolve o id,
    igual ao original."""
    summary_id = summary_data['id']
    k.upsert(_SUMMARIES, summary_id, summary_data, criado_em=summary_data.get('created_at'))
    return summary_id


def get_daily_summary(uid: str, summary_id: str) -> Optional[dict]:
    """GET por id, ou None."""
    return k.obter(_SUMMARIES, summary_id)


def get_daily_summary_by_date(uid: str, date: str) -> Optional[dict]:
    """Primeiro resumo com `date` == date (string 'YYYY-MM-DD'), ou None."""
    docs = k.listar(_SUMMARIES, filtro={"date": date}, limite=1)
    return docs[0] if docs else None


def get_daily_summaries(
    uid: str,
    limit: int = 30,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[dict]:
    """Resumos do usuário, desc por `date`, com filtro opcional de range
    (start_date/end_date, strings 'YYYY-MM-DD', inclusivos).

    O doc-store ordena/pagina pelo timestamp de criação do doc, não pelo campo
    `date`; para replicar a semântica visível do original (ordenado por `date`
    desc, range filtrado, paginado), busca-se uma janela maior, filtra e
    ordena por `date` em Python, depois aplica offset/limit."""
    raw = k.listar(_SUMMARIES, ordem="desc", limite=max(limit + offset, 1000), offset=0)

    filtered = raw
    if start_date:
        filtered = [d for d in filtered if d.get('date') and d['date'] >= start_date]
    if end_date:
        filtered = [d for d in filtered if d.get('date') and d['date'] <= end_date]

    filtered.sort(key=lambda d: d.get('date') or '', reverse=True)
    return filtered[offset : offset + limit]


def delete_daily_summary(uid: str, summary_id: str) -> bool:
    """Deleta o resumo. True mesmo se não existir (mirror do original, que não
    checa existência antes de deletar)."""
    k.deletar(_SUMMARIES, summary_id)
    return True


def get_summaries_count(uid: str) -> int:
    """Contagem total de resumos do usuário."""
    return k.contar(_SUMMARIES)
