"""Config de notificações sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de
database/notifications.py.

F4.5 (Omi self-hosted): a configuração de notificações (tokens FCM + prefs de
daily summary / mentor) sai do Firestore e mora no doc-store genérico do
memory-service (via `omi_docs_karla`), nas coleções:
  - `users`       → doc raiz (doc_id=uid, `dados` = o doc do usuário inteiro;
                     MESMO doc usado por `database/users_karla.py`);
  - `fcm_tokens`  → tokens de push (doc_id=device_key, `dados` inclui o
                     próprio `device_key` — necessário porque `k.listar` NÃO
                     devolve doc_id, então gravamos a chave dentro de `dados`
                     pra poder reconstruí-la depois de um listar+filtro, MESMO
                     idioma de `set_task_integration` gravando `app_key`).
Este módulo replica as funções PÚBLICAS de database/notifications.py falando
com o cliente genérico. Ativação por env `CONFIG_KARLA=true` (mesma flag de
users_karla.py — não é uma flag nova; ver rodapé de database/notifications.py).

⚠️ Flag própria: `is_enabled()` gateia por `CONFIG_KARLA` (idêntico ao
`users_karla.is_enabled()` — NÃO reusa `OMI_DOCS_KARLA`). O cliente
`omi_docs_karla` só gateia a própria `is_enabled` por `OMI_DOCS_KARLA`; suas
funções de I/O (`upsert/obter/patch/listar/deletar`) NÃO checam flag nenhuma —
chamadas diretamente aqui, gating todo no rodapé de notifications.py + neste
`is_enabled()`.

⚠️ Trap do merge RASO (omi_docs_karla.patch): substitui chaves de TOPO por
inteiro — sem deep-merge, sem dot-keys. Prefs do usuário (time_zone,
daily_summary_hour_local, daily_summary_enabled, mentor_notification_frequency,
fcm_token legado) vivem no doc `users` (mesmo doc de users_karla.py) e usam
GET+merge client-side + PATCH de chaves de topo — igual ao resto do F4.5.

⚠️ Single-user self-host: o original usa `collection_group('fcm_tokens')` e
queries `WHERE time_zone IN (...)` CROSS-USER (todos os usuários do Firestore).
Aqui o doc-store da Karla é escopado a UM tenant só (token da MEMÓRIA
UNIFICADA), então:
  - `remove_invalid_token`/`remove_bulk_tokens`: o "collection_group" vira
    trivial — listar a única coleção `fcm_tokens` (sem cruzar usuários) e
    filtrar client-side pelo valor do token, deletando por doc_id (recuperado
    do `device_key` gravado em `dados`, ver acima).
  - `get_users_for_daily_summary`/`_get_users_in_timezones`: o "query cross-user
    por time_zone" vira enumerar os docs da coleção `users` (só existe o(s)
    usuário(s) daquele tenant) e aplicar a MESMA lógica de janela horária do
    original a cada um. Como `k.listar` não devolve doc_id, cada doc `users`
    PRECISA ter uma chave `uid` dentro de `dados` pra sabermos de quem é —
    `_merge_user` deste módulo garante isso (seed `uid` no primeiro write,
    idempotente). Docs sem `uid` (ex: criados só por users_karla.py antes desta
    task) caem no fallback `_SELF_HOST_DEFAULT_UID` quando há exatamente 1 doc
    na coleção (caso real do self-host single-tenant); com >1 doc sem `uid`
    não há como atribuí-los com segurança — são pulados (logado).

Filosofia de erro: herdada do mold — erros de rede logam (warning+sanitize no
cliente) e a função devolve o DEFAULT/NEUTRO do ORIGINAL por função (nunca
raise), pra não derrubar o app se a Karla estiver indisponível.

Funções EXCLUÍDAS do shim (nenhuma — todas as funções públicas de
database/notifications.py têm equivalente aqui; ver rodapé de notifications.py
pro rebind completo).
"""

import logging
import os

from database import omi_docs_karla as k
from database.cache import get_memory_cache

logger = logging.getLogger(__name__)

_USERS = "users"
_FCM_TOKENS = "fcm_tokens"

# Fallback do único uid do tenant self-host quando um doc `users` legado (sem
# `uid` gravado em `dados`) precisa ser atribuído em get_users_for_daily_summary
# / _get_users_in_timezones. Mesmo default usado nos scripts de export F4.1/F4.2
# (scripts/memoria/export_*_f4*.py) — documentado, não inventado aqui.
_SELF_HOST_DEFAULT_UID = "oMFDcKdDLBUSHIDP182t5eoHqMA2"

# Default: 22:00 local time (10 PM) — mesmo default do original.
DEFAULT_DAILY_SUMMARY_HOUR_LOCAL = 22

# Default: 0 (disabled) — mesmo default do original.
DEFAULT_MENTOR_NOTIFICATION_FREQUENCY = 0


def is_enabled() -> bool:
    return (
        os.getenv("CONFIG_KARLA", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("MEMORIA_UNIFICADA_URL"))
        and bool(os.getenv("MEMORIA_UNIFICADA_TOKEN"))
    )


def _get_user(uid: str) -> dict:
    """GET do doc raiz `users` → dados (dict), ou {} se ausente/erro."""
    return k.obter(_USERS, uid) or {}


def _merge_user(uid: str, updates: dict) -> None:
    """PATCH raso do doc `users` com chaves de TOPO (equivale a set(merge=True)
    do Firestore quando as chaves são de topo). Cria o doc se não existir.
    Sempre semeia `uid` no corpo (idempotente) pra permitir reconstruir o uid
    depois de um `k.listar` sem doc_id (ver docstring do módulo)."""
    updates = dict(updates)
    updates.setdefault('uid', uid)
    if k.obter(_USERS, uid) is None:
        k.upsert(_USERS, uid, updates)
    else:
        k.patch(_USERS, uid, updates)


# ****************************** fcm_tokens ******************************


def save_token(uid: str, data: dict):
    """
    Grava o token na coleção `fcm_tokens` (doc_id=device_key). Também mantém
    `time_zone` no doc `users` (paridade com o comentário do original de
    "backward compatibility e queries eficientes"). Migra `fcm_token` legado
    do doc `users` pra `fcm_tokens/unknown_default`, igual ao original.

    Réplica passo a passo do original (save_token em database/notifications.py):
      1. Migra token legado (`users.fcm_token`) pra `fcm_tokens/unknown_default`
         se ainda não existir na coleção, e remove o campo legado do doc `users`.
      2. Se o novo token tem device_key "de verdade" (≠ unknown_default) e o
         doc `unknown_default` tem o MESMO token, apaga o `unknown_default`
         (evita duplicata quando o client finalmente manda um device_key real).
      3. Salva o novo token em `fcm_tokens/{device_key}` (upsert/merge).
    """
    device_key = data.get('device_key', 'unknown_default')
    token = data.get('fcm_token')
    time_zone = data.get('time_zone')

    # Passo 1: migra token legado.
    user_data = k.obter(_USERS, uid)
    if user_data is not None:
        legacy_token = user_data.get('fcm_token')
        if legacy_token:
            existing_tokens = [d.get('token') for d in k.listar(_FCM_TOKENS, limite=100000) if isinstance(d, dict)]
            if legacy_token not in existing_tokens:
                k.upsert(
                    _FCM_TOKENS,
                    'unknown_default',
                    {
                        'device_key': 'unknown_default',
                        'token': legacy_token,
                        'time_zone': user_data.get('time_zone'),
                    },
                )
            # Remove campo legado (equivalente ao DELETE_FIELD do original).
            user_data.pop('fcm_token', None)
            k.upsert(_USERS, uid, {**user_data, 'uid': uid})

    # Passo 2: promove unknown_default → device_key real (apaga o duplicado).
    if device_key != 'unknown_default':
        unknown_doc = k.obter(_FCM_TOKENS, 'unknown_default')
        if unknown_doc is not None and unknown_doc.get('token') == token:
            k.deletar(_FCM_TOKENS, 'unknown_default')

    # Passo 3: salva o novo token (upsert = "set merge=True" do original).
    existing_token_doc = k.obter(_FCM_TOKENS, device_key)
    if existing_token_doc is None:
        k.upsert(_FCM_TOKENS, device_key, {'device_key': device_key, 'token': token, 'time_zone': time_zone})
    else:
        k.patch(_FCM_TOKENS, device_key, {'device_key': device_key, 'token': token, 'time_zone': time_zone})

    # time_zone também no doc `users` (backward compat / queries eficientes).
    if time_zone:
        _merge_user(uid, {'time_zone': time_zone})


def get_user_time_zone(uid: str):
    """Timezone do doc raiz `users`."""
    data = k.obter(_USERS, uid)
    if data is not None:
        return data.get('time_zone')
    return None


def get_all_tokens(uid: str) -> list[str]:
    """Todos os device tokens do usuário: coleção `fcm_tokens` + campo legado
    `fcm_token` no doc `users` (paridade com o original)."""
    tokens = []

    for d in k.listar(_FCM_TOKENS, limite=100000):
        if isinstance(d, dict) and d.get('token'):
            tokens.append(d['token'])

    user_data = k.obter(_USERS, uid)
    if user_data is not None:
        legacy_token = user_data.get('fcm_token')
        if legacy_token and legacy_token not in tokens:
            tokens.append(legacy_token)

    return tokens


def remove_invalid_token(token: str):
    """Equivalente single-user do `collection_group('fcm_tokens')` do original:
    lista a única coleção `fcm_tokens` (já escopada ao tenant), filtra pelo
    valor do token e deleta o PRIMEIRO doc encontrado (mesmo `.limit(1)` do
    original). doc_id recuperado do `device_key` gravado em `dados`."""
    docs = k.listar(_FCM_TOKENS, filtro={"token": token}, limite=1)
    for d in docs:
        if not isinstance(d, dict):
            continue
        device_key = d.get('device_key')
        if device_key:
            k.deletar(_FCM_TOKENS, device_key)
        return


def remove_bulk_tokens(tokens: list[str]):
    """Equivalente single-user do batch-delete cross-user do original: lista
    `fcm_tokens` inteira uma vez, filtra client-side pelos tokens do lote, e
    deleta cada match por doc_id (`device_key`)."""
    if not tokens:
        return

    token_set = set(tokens)
    docs = k.listar(_FCM_TOKENS, limite=100000)
    for d in docs:
        if not isinstance(d, dict):
            continue
        if d.get('token') in token_set:
            device_key = d.get('device_key')
            if device_key:
                k.deletar(_FCM_TOKENS, device_key)


# **************************************
# *** Daily Summary Time Preferences ***
# **************************************


def get_daily_summary_hour_local(uid: str) -> int | None:
    """Hora preferida (local) do daily summary. None se não setado."""
    data = k.obter(_USERS, uid)
    if data is not None:
        return data.get('daily_summary_hour_local')
    return None


def set_daily_summary_hour_local(uid: str, hour_local: int) -> bool:
    if not (0 <= hour_local <= 23):
        raise ValueError(f"Invalid hour: {hour_local}. Must be 0-23.")
    _merge_user(uid, {'daily_summary_hour_local': hour_local})
    return True


def get_daily_summary_enabled(uid: str) -> bool:
    """Habilitado por padrão (mesmo default do original)."""
    data = k.obter(_USERS, uid)
    if data is not None:
        return data.get('daily_summary_enabled', True)
    return True


def set_daily_summary_enabled(uid: str, enabled: bool) -> bool:
    _merge_user(uid, {'daily_summary_enabled': enabled})
    return True


# **************************************
# *** Mentor Notification Frequency ***
# **************************************


def get_mentor_notification_frequency(uid: str) -> int:
    """
    Preferência de frequência de notificação do mentor (0-5). Preserva o
    cache in-memory (30s TTL) do original — MESMO `get_memory_cache()`
    compartilhado, MESMA chave `mentor_frequency:{uid}`, pra manter paridade de
    comportamento (inclusive invalidação cruzada se o processo também rodar o
    caminho Firestore em algum outro fluxo).
    """
    cache = get_memory_cache()

    def fetch():
        data = k.obter(_USERS, uid)
        if data is not None:
            return data.get('mentor_notification_frequency', DEFAULT_MENTOR_NOTIFICATION_FREQUENCY)
        return DEFAULT_MENTOR_NOTIFICATION_FREQUENCY

    return cache.get_or_fetch(f"mentor_frequency:{uid}", fetch, ttl=30)


def set_mentor_notification_frequency(uid: str, frequency: int) -> bool:
    if not (0 <= frequency <= 5):
        raise ValueError(f"Invalid frequency: {frequency}. Must be 0-5.")
    _merge_user(uid, {'mentor_notification_frequency': frequency})
    # Invalida o cache local pra essa instância enxergar o update imediatamente.
    get_memory_cache().delete(f"mentor_frequency:{uid}")
    return True


# **************************************
# *** Daily summary — user enumeration (single-tenant) ***
# **************************************


def _tokens_for_uid(uid: str, user_data: dict) -> list[str]:
    """Tokens da coleção `fcm_tokens` (tenant inteiro, single-user) + legado."""
    tokens = []
    for d in k.listar(_FCM_TOKENS, limite=100000):
        if isinstance(d, dict) and d.get('token'):
            tokens.append(d['token'])
    legacy_token = user_data.get('fcm_token')
    if legacy_token and legacy_token not in tokens:
        tokens.append(legacy_token)
    return tokens


def _list_users_with_uid() -> list[tuple[str, dict]]:
    """Enumera a coleção `users` devolvendo (uid, dados) pra cada doc. Como
    `k.listar` não devolve doc_id, usa o campo `uid` gravado em `dados` (ver
    `_merge_user`). Docs legados sem `uid`: se houver EXATAMENTE 1 doc sem
    `uid` na coleção inteira, atribui a `_SELF_HOST_DEFAULT_UID` (caso real do
    self-host single-tenant); com mais de 1, são pulados e logados (não há como
    atribuí-los com segurança)."""
    docs = k.listar(_USERS, limite=100000)
    with_uid: list[tuple[str, dict]] = []
    without_uid: list[dict] = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        uid = d.get('uid')
        if uid:
            with_uid.append((uid, d))
        else:
            without_uid.append(d)

    if without_uid:
        if len(without_uid) == 1 and not with_uid:
            with_uid.append((_SELF_HOST_DEFAULT_UID, without_uid[0]))
        else:
            logger.warning(
                "notifications_karla: %d doc(s) `users` sem campo `uid` não puderam ser "
                "atribuídos com segurança (ambíguo) — pulados.",
                len(without_uid),
            )

    return with_uid


async def get_users_token_in_timezones(timezones: list[str]):
    return await _get_users_in_timezones(timezones, 'fcm_token')


async def get_users_id_in_timezones(timezones: list[str]):
    return await _get_users_in_timezones(timezones, 'id')


async def get_users_for_daily_summary(timezones: list[str], target_local_hour: int):
    """
    Equivalente single-tenant do original: enumera os docs `users` do tenant
    (em vez de uma query Firestore cross-user por time_zone) e aplica a MESMA
    lógica de janela horária:
      1. time_zone do usuário precisa estar em `timezones`;
      2. daily_summary_enabled não pode ser False;
      3. daily_summary_hour_local (ou default 22) precisa bater com
         target_local_hour;
      4. usuário precisa ter pelo menos 1 token.

    Devolve lista de (uid, [tokens], time_zone) — mesmo shape do original.
    """
    if not timezones:
        return []

    tz_set = set(timezones)
    users = []

    for uid, user_data in _list_users_with_uid():
        time_zone = user_data.get('time_zone')
        if time_zone not in tz_set:
            continue

        if user_data.get('daily_summary_enabled') is False:
            continue

        user_hour = user_data.get('daily_summary_hour_local', DEFAULT_DAILY_SUMMARY_HOUR_LOCAL)
        if user_hour != target_local_hour:
            continue

        tokens = _tokens_for_uid(uid, user_data)
        if not tokens:
            continue

        users.append((uid, tokens, time_zone))

    return users


async def _get_users_in_timezones(timezones: list[str], filter: str):
    """Equivalente single-tenant do original: enumera os docs `users` do
    tenant filtrando por time_zone (em vez de query Firestore cross-user)."""
    if not timezones:
        return []

    tz_set = set(timezones)
    users = []

    for uid, user_data in _list_users_with_uid():
        time_zone = user_data.get('time_zone')
        if time_zone not in tz_set:
            continue

        tokens = _tokens_for_uid(uid, user_data)
        if not tokens:
            continue

        if filter == 'fcm_token':
            users.extend(tokens)
        else:
            users.append((uid, tokens, time_zone))

    return users
