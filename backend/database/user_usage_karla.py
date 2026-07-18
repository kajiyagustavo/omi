"""Usage stats (telemetria) sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/user_usage.py.

F4.5 (Omi self-hosted): os buckets horários de uso saem do Firestore
(`users/{uid}/hourly_usage/{YYYY-MM-DD-HH}`) e moram no doc-store genérico do
memory-service (via `omi_docs_karla`), coleção `hourly_usage` (doc_id = o
mesmo id de hora do original, `{year}-{month:02d}-{day:02d}-{hour:02d}`).
Este módulo replica as funções PÚBLICAS de database/user_usage.py falando com
o cliente genérico. Ativação por env `CONFIG_KARLA=true` (mesma flag de
users_karla.py — ver rodapé de database/user_usage.py); com a flag off, nada
muda (rollback = desligar).

⚠️ Flag própria: `is_enabled()` gateia por `CONFIG_KARLA` (idêntico ao
`users_karla.is_enabled()` — NÃO reusa `OMI_DOCS_KARLA`). `omi_docs_karla` só
gateia a própria `is_enabled` por `OMI_DOCS_KARLA`; suas funções de I/O não
checam flag nenhuma — chamadas diretamente aqui, gating todo no rodapé de
user_usage.py + neste `is_enabled()`.

Diferenças de semântica vs Firestore:
  - `firestore.Increment` vira GET+merge client-side: lê o doc horário, soma
    os contadores existentes com os deltas recebidos, PATCH da chave inteira
    (single-user, sem corrida real — mesmo idioma dos outros shims F4.5).
  - `ArrayUnion(platforms)` vira união client-side (lista) antes do PATCH.
  - `doc_id` é injetado em `dados['id']` (idêntico ao original, que já grava
    `id` no doc) — necessário porque `k.listar` não devolve doc_id, e os
    agregados (`_aggregate_stats`, `get_hourly_history_for_today`, etc.)
    precisam ler `year`/`month`/`day`/`hour` de dentro de `dados`, que o
    original já grava — preservado aqui.
  - `get_monthly_chat_usage` itera `list_documents()` (metadados, sem 1
    read por doc) sobre `llm_usage` — não é usage.py mas mora aqui no
    original; SEM equivalente eficiente na Karla (doc-store não expõe
    "listar ids sem ler corpo"). Replicado via `k.listar('llm_usage',
    limite=100000)` lendo os `dados` inteiros (mais caro, mas único jeito
    de obter os mesmos campos) e filtrando client-side por prefixo de mês
    (usa o campo `date` gravado pelo shim de llm_usage).
  - Queries Firestore com múltiplos `where` (year/month/day) viram filtro
    jsonb via `k.listar(filtro={...})` (equality nos campos de topo) —
    replica exatamente os filtros do original.

Filosofia de erro: telemetria — fail-open. Erros de rede logam (warning+
sanitize no cliente `omi_docs_karla`) e a função devolve o DEFAULT/NEUTRO do
ORIGINAL por função (nunca raise), pra não derrubar o app se a Karla estiver
indisponível.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from database import omi_docs_karla as k
from models.user_usage import UsageStats

logger = logging.getLogger(__name__)

_HOURLY_USAGE = "hourly_usage"
_LLM_USAGE = "llm_usage"

_INCREMENT_KEYS = (
    'transcription_seconds',
    'words_transcribed',
    'insights_gained',
    'memories_created',
    'speech_seconds',
)


def is_enabled() -> bool:
    return (
        os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def _hour_doc_id(date: datetime) -> str:
    return f'{date.year}-{date.month:02d}-{date.day:02d}-{date.hour:02d}'


def update_hourly_usage(uid: str, date: datetime, updates: dict, platform: Optional[str] = None):
    """GET+merge do doc horário: soma os contadores existentes com os deltas
    recebidos (equivale a `firestore.Increment`), união client-side de
    `platforms` (equivale a `ArrayUnion`). Só grava se houver algum
    incremento positivo (como o original)."""
    increments = {key: value for key, value in updates.items() if key in _INCREMENT_KEYS and value > 0}
    if not increments:
        return

    doc_id = _hour_doc_id(date)
    existing = k.obter(_HOURLY_USAGE, doc_id) or {}

    merged = dict(existing)
    for key, value in increments.items():
        merged[key] = existing.get(key, 0) + value

    merged['last_updated'] = datetime.now(timezone.utc)
    merged['year'] = date.year
    merged['month'] = date.month
    merged['day'] = date.day
    merged['hour'] = date.hour
    merged['id'] = doc_id

    if platform in ('desktop', 'mobile'):
        platforms = list(existing.get('platforms', []) or [])
        if platform not in platforms:
            platforms.append(platform)
        merged['platforms'] = platforms

    if existing:
        k.patch(_HOURLY_USAGE, doc_id, merged)
    else:
        k.upsert(_HOURLY_USAGE, doc_id, merged)


def batch_update_hourly_usage(uid: str, hourly_updates: dict):
    """Idêntico ao original (batch), mas sem `firestore.Increment` — na Karla
    o corpo recebido já é o valor final a gravar (o original faz `batch.set`
    com merge=True SEM Increment aqui, diferente de `update_hourly_usage` —
    replicado igual: sobrescreve as chaves de topo do updates)."""
    for date, updates in hourly_updates.items():
        doc_id = _hour_doc_id(date)
        existing = k.obter(_HOURLY_USAGE, doc_id) or {}
        merged = dict(updates)
        merged['year'] = date.year
        merged['month'] = date.month
        merged['day'] = date.day
        merged['hour'] = date.hour
        merged['id'] = doc_id
        merged['last_updated'] = datetime.now(timezone.utc)
        if existing:
            k.patch(_HOURLY_USAGE, doc_id, merged)
        else:
            k.upsert(_HOURLY_USAGE, doc_id, merged)


def _aggregate_stats(docs: list) -> dict:
    stats = {key: 0 for key in _INCREMENT_KEYS}
    for data in docs:
        if not isinstance(data, dict):
            continue
        for key in _INCREMENT_KEYS:
            stats[key] += data.get(key, 0)
    return stats


def get_today_usage_stats(uid: str, date: datetime) -> dict:
    docs = k.listar(_HOURLY_USAGE, filtro={"year": date.year, "month": date.month, "day": date.day}, limite=100000)
    return _aggregate_stats(docs)


def get_monthly_usage_stats(uid: str, date: datetime) -> dict:
    docs = k.listar(_HOURLY_USAGE, filtro={"year": date.year, "month": date.month}, limite=100000)
    return _aggregate_stats(docs)


def get_monthly_usage_stats_since(uid: str, date: datetime, start_date: datetime) -> dict:
    """O original filtra por `id >= start_doc_id` (range query). Na Karla:
    filtro jsonb não suporta range, então filtramos client-side pelo `id`
    gravado em `dados` após buscar o mês inteiro."""
    start_doc_id = f'{start_date.year}-{start_date.month:02d}-{start_date.day:02d}-00'
    docs = k.listar(_HOURLY_USAGE, filtro={"year": date.year, "month": date.month}, limite=100000)
    docs = [d for d in docs if isinstance(d, dict) and d.get('id', '') >= start_doc_id]
    return _aggregate_stats(docs)


def get_yearly_usage_stats(uid: str, date: datetime) -> dict:
    docs = k.listar(_HOURLY_USAGE, filtro={"year": date.year}, limite=100000)
    return _aggregate_stats(docs)


def get_all_time_usage_stats(uid: str) -> dict:
    docs = k.listar(_HOURLY_USAGE, limite=100000)
    return _aggregate_stats(docs)


def get_hourly_history_for_today(uid: str, date: datetime) -> list[dict]:
    docs = k.listar(_HOURLY_USAGE, filtro={"year": date.year, "month": date.month, "day": date.day}, limite=100000)
    hourly_totals = {}
    for data in docs:
        if not isinstance(data, dict):
            continue
        hour = data.get('hour', 0)
        if hour not in hourly_totals:
            hourly_totals[hour] = {
                'transcription_seconds': 0,
                'words_transcribed': 0,
                'insights_gained': 0,
                'memories_created': 0,
            }
        hourly_totals[hour]['transcription_seconds'] += data.get('transcription_seconds', 0)
        hourly_totals[hour]['words_transcribed'] += data.get('words_transcribed', 0)
        hourly_totals[hour]['insights_gained'] += data.get('insights_gained', 0)
        hourly_totals[hour]['memories_created'] += data.get('memories_created', 0)

    history = [
        {'date': f"{date.year}-{date.month:02d}-{date.day:02d}T{hour:02d}:00:00Z", **stats}
        for hour, stats in hourly_totals.items()
    ]
    history.sort(key=lambda x: x['date'])
    return history


def get_daily_history_for_month(uid: str, date: datetime) -> list[dict]:
    docs = k.listar(_HOURLY_USAGE, filtro={"year": date.year, "month": date.month}, limite=100000)
    daily_totals = {}
    for data in docs:
        if not isinstance(data, dict):
            continue
        day = data.get('day', 0)
        if day not in daily_totals:
            daily_totals[day] = {
                'transcription_seconds': 0,
                'words_transcribed': 0,
                'insights_gained': 0,
                'memories_created': 0,
            }
        daily_totals[day]['transcription_seconds'] += data.get('transcription_seconds', 0)
        daily_totals[day]['words_transcribed'] += data.get('words_transcribed', 0)
        daily_totals[day]['insights_gained'] += data.get('insights_gained', 0)
        daily_totals[day]['memories_created'] += data.get('memories_created', 0)

    history = [{'date': f"{date.year}-{date.month:02d}-{day:02d}", **stats} for day, stats in daily_totals.items()]
    history.sort(key=lambda x: x['date'])
    return history


def get_monthly_history_for_year(uid: str, date: datetime) -> list[dict]:
    docs = k.listar(_HOURLY_USAGE, filtro={"year": date.year}, limite=100000)
    monthly_totals = {}
    for data in docs:
        if not isinstance(data, dict):
            continue
        month = data.get('month', 0)
        if month not in monthly_totals:
            monthly_totals[month] = {
                'transcription_seconds': 0,
                'words_transcribed': 0,
                'insights_gained': 0,
                'memories_created': 0,
            }
        monthly_totals[month]['transcription_seconds'] += data.get('transcription_seconds', 0)
        monthly_totals[month]['words_transcribed'] += data.get('words_transcribed', 0)
        monthly_totals[month]['insights_gained'] += data.get('insights_gained', 0)
        monthly_totals[month]['memories_created'] += data.get('memories_created', 0)

    history = [{'date': f"{date.year}-{month:02d}-01", **stats} for month, stats in monthly_totals.items()]
    history.sort(key=lambda x: x['date'])
    return history


def get_yearly_history(uid: str) -> list[dict]:
    docs = k.listar(_HOURLY_USAGE, limite=100000)
    yearly_totals = {}
    for data in docs:
        if not isinstance(data, dict):
            continue
        year = data.get('year', 0)
        if year not in yearly_totals:
            yearly_totals[year] = {
                'transcription_seconds': 0,
                'words_transcribed': 0,
                'insights_gained': 0,
                'memories_created': 0,
            }
        yearly_totals[year]['transcription_seconds'] += data.get('transcription_seconds', 0)
        yearly_totals[year]['words_transcribed'] += data.get('words_transcribed', 0)
        yearly_totals[year]['insights_gained'] += data.get('insights_gained', 0)
        yearly_totals[year]['memories_created'] += data.get('memories_created', 0)

    history = [{'date': f"{year}-01-01", **stats} for year, stats in yearly_totals.items()]
    history.sort(key=lambda x: x['date'])
    return history


def get_current_user_usage(uid: str, period: str) -> dict:
    now = datetime.now(timezone.utc)
    response = {}

    if period == 'today':
        response['today'] = UsageStats(**get_today_usage_stats(uid, now)).dict()
        response['history'] = get_hourly_history_for_today(uid, now)
    elif period == 'monthly':
        response['monthly'] = UsageStats(**get_monthly_usage_stats(uid, now)).dict()
        response['history'] = get_daily_history_for_month(uid, now)
    elif period == 'yearly':
        response['yearly'] = UsageStats(**get_yearly_usage_stats(uid, now)).dict()
        response['history'] = get_monthly_history_for_year(uid, now)
    elif period == 'all_time':
        response['all_time'] = UsageStats(**get_all_time_usage_stats(uid)).dict()
        response['history'] = get_yearly_history(uid)

    return response


def get_monthly_chat_usage(uid: str, now: Optional[datetime] = None) -> dict:
    """Espelha o original, mas via `k.listar` (sem equivalente eficiente a
    `list_documents()` no doc-store — lê `dados` inteiro de cada doc de
    `llm_usage` e filtra client-side por prefixo de mês no campo `date`)."""
    now = now or datetime.now(timezone.utc)
    month_prefix = f'{now.year}-{now.month:02d}-'

    docs = k.listar(_LLM_USAGE, limite=100000)
    questions = 0
    cost_usd = 0.0
    for data in docs:
        if not isinstance(data, dict):
            continue
        if not str(data.get('date', '')).startswith(month_prefix):
            continue
        for key, value in data.items():
            if not isinstance(value, (int, float)):
                continue
            if key.startswith('desktop_chat'):
                if key.endswith('.call_count'):
                    questions += int(value)
                elif key.endswith('.cost_usd'):
                    cost_usd += float(value)
            elif key.startswith('chat.') and key.endswith('.call_count'):
                questions += int(value)

    if now.month == 12:
        next_year, next_month = now.year + 1, 1
    else:
        next_year, next_month = now.year, now.month + 1
    reset_at = int(datetime(next_year, next_month, 1, tzinfo=timezone.utc).timestamp())

    return {
        'questions': questions,
        'cost_usd': round(cost_usd, 4),
        'reset_at': reset_at,
    }
