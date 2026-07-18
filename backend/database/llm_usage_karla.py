"""LLM usage (telemetria) sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/llm_usage.py.

F4.5 (Omi self-hosted): os buckets diários de uso de LLM saem do Firestore
(`users/{uid}/llm_usage/{YYYY-MM-DD}`) e moram no doc-store genérico do
memory-service (via `omi_docs_karla`), coleção `llm_usage` (doc_id = data,
mesma chave do original). Este módulo replica as funções PÚBLICAS de
database/llm_usage.py falando com o cliente genérico. Ativação por env
`CONFIG_KARLA=true` (mesma flag de users_karla.py — ver rodapé de
database/llm_usage.py); com a flag off, nada muda (rollback = desligar).

⚠️ Flag própria: `is_enabled()` gateia por `CONFIG_KARLA` (idêntico ao
`users_karla.is_enabled()` — NÃO reusa `OMI_DOCS_KARLA`).

Diferenças de semântica vs Firestore:
  - `firestore.Increment` em campos ANINHADOS (`{feature}.{model}.input_tokens`
    etc, `{bucket}.input_tokens` etc) vira GET+merge client-side: lê o doc do
    dia, navega/cria o sub-dict aninhado, soma os contadores, PATCH da chave
    de TOPO inteira (feature ou bucket) — o merge raso da Karla só substitui
    chaves de topo, então cada `record_*` faz PATCH de UMA chave de topo por
    vez com o sub-dict inteiro já mergeado.
  - `get_daily_usage`/`get_usage_summary`/`get_top_features`: leitura direta
    de `k.obter`/`k.listar`, mesma agregação client-side do original.
  - `get_global_top_features`: o original faz uma collection-group query
    CROSS-USER (`db.collection_group("llm_usage")`). Não mapeia — ver EXCLUÍDA
    abaixo (não replicada; único tenant na Karla torna a semântica
    "global" idêntica a "do usuário", mas a assinatura original não recebe
    uid, então fica documentado como não-portável e excluído do rebind).

Funções EXCLUÍDAS do shim (e do rodapé — ficam no Firestore, sem equivalente
1:1 na Karla): `get_global_top_features` (collection-group cross-user; a
Karla é single-tenant por token, então "global" não tem um segundo usuário
pra agregar, e a função nem recebe uid pra escopar — ambígua nesse contexto).

Filosofia de erro: telemetria — fail-open. Erros de rede logam (warning+
sanitize no cliente `omi_docs_karla`) e a função devolve o DEFAULT/NEUTRO do
ORIGINAL por função (nunca raise).
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from database import omi_docs_karla as k

_LLM_USAGE = "llm_usage"


def is_enabled() -> bool:
    return (
        os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def _day_doc_id(date: datetime) -> str:
    return f"{date.year}-{date.month:02d}-{date.day:02d}"


def record_llm_usage(
    uid: str,
    feature: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
):
    """GET+merge do doc do dia: soma os contadores existentes na chave
    `feature` (dict {model: {input_tokens, output_tokens, call_count}}) com
    os deltas recebidos, PATCH da chave `feature` inteira."""
    if input_tokens == 0 and output_tokens == 0:
        return

    now = datetime.now(timezone.utc)
    doc_id = _day_doc_id(now)

    if not isinstance(model, str) or not model:
        model = "unknown"

    safe_model = (
        model.replace(".", "_")
        .replace("/", "_")
        .replace("~", "_")
        .replace("*", "_")
        .replace("[", "_")
        .replace("]", "_")
        .replace("`", "_")
    )

    existing = k.obter(_LLM_USAGE, doc_id) or {}
    feature_data = dict(existing.get(feature, {}) or {})
    model_data = dict(feature_data.get(safe_model, {}) or {})
    model_data['input_tokens'] = model_data.get('input_tokens', 0) + input_tokens
    model_data['output_tokens'] = model_data.get('output_tokens', 0) + output_tokens
    model_data['call_count'] = model_data.get('call_count', 0) + 1
    feature_data[safe_model] = model_data

    updates = {
        feature: feature_data,
        'date': doc_id,
        'last_updated': now,
    }

    if existing:
        k.patch(_LLM_USAGE, doc_id, updates)
    else:
        k.upsert(_LLM_USAGE, doc_id, updates)


def get_daily_usage(uid: str, date: Optional[datetime] = None) -> Dict:
    if date is None:
        date = datetime.now(timezone.utc)
    doc_id = _day_doc_id(date)
    return k.obter(_LLM_USAGE, doc_id) or {}


def get_usage_summary(uid: str, days: int = 30) -> Dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_id = _day_doc_id(cutoff)

    docs = k.listar(_LLM_USAGE, limite=100000)
    summary: Dict[str, Dict[str, int]] = {}

    for data in docs:
        if not isinstance(data, dict):
            continue
        if str(data.get('date', '')) < cutoff_id:
            continue
        for feature, models in data.items():
            if feature in ('last_updated', 'date'):
                continue
            if not isinstance(models, dict):
                continue

            if feature not in summary:
                summary[feature] = {"input_tokens": 0, "output_tokens": 0, "call_count": 0}

            for model, tokens in models.items():
                if isinstance(tokens, dict):
                    summary[feature]["input_tokens"] += tokens.get("input_tokens", 0)
                    summary[feature]["output_tokens"] += tokens.get("output_tokens", 0)
                    summary[feature]["call_count"] += tokens.get("call_count", 0)

    return summary


def get_top_features(uid: str, days: int = 30, limit: int = 3) -> List[Dict]:
    summary = get_usage_summary(uid, days)

    features = []
    for feature, tokens in summary.items():
        total = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
        features.append(
            {
                "feature": feature,
                "input_tokens": tokens.get("input_tokens", 0),
                "output_tokens": tokens.get("output_tokens", 0),
                "total_tokens": total,
                "call_count": tokens.get("call_count", 0),
            }
        )

    features.sort(key=lambda x: x["total_tokens"], reverse=True)
    return features[:limit]


def record_llm_usage_bucket(
    uid: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    total_tokens: int = 0,
    cost_usd: float = 0.0,
    bucket: str = 'desktop_chat',
    account: str = 'omi',
) -> None:
    """GET+merge do doc do dia: soma os contadores existentes nas chaves
    `bucket` e `{bucket}_{account}` (dual-write, como o original) com os
    deltas recebidos, PATCH de ambas as chaves de topo."""
    today = _day_doc_id(datetime.now(timezone.utc))
    acct_key = f'{bucket}_{account}'

    existing = k.obter(_LLM_USAGE, today) or {}

    def _merged_bucket(key: str) -> dict:
        current = dict(existing.get(key, {}) or {})
        current['input_tokens'] = current.get('input_tokens', 0) + input_tokens
        current['output_tokens'] = current.get('output_tokens', 0) + output_tokens
        current['cache_read_tokens'] = current.get('cache_read_tokens', 0) + cache_read_tokens
        current['cache_write_tokens'] = current.get('cache_write_tokens', 0) + cache_write_tokens
        current['total_tokens'] = current.get('total_tokens', 0) + total_tokens
        current['cost_usd'] = current.get('cost_usd', 0.0) + cost_usd
        current['call_count'] = current.get('call_count', 0) + 1
        return current

    updates = {
        bucket: _merged_bucket(bucket),
        acct_key: _merged_bucket(acct_key),
        'date': today,
        'last_updated': datetime.now(timezone.utc),
    }

    if existing:
        k.patch(_LLM_USAGE, today, updates)
    else:
        k.upsert(_LLM_USAGE, today, updates)


def get_total_llm_cost(uid: str, bucket: str = 'desktop_chat') -> float:
    docs = k.listar(_LLM_USAGE, limite=100000)
    total = 0.0
    for data in docs:
        if not isinstance(data, dict):
            continue
        dc = data.get(bucket)
        if isinstance(dc, dict):
            total += dc.get('cost_usd', 0.0)
    return round(total, 6)
