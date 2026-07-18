"""Config do usuário sobre a MEMÓRIA UNIFICADA (Karla) — drop-in de database/users.py.

F4.5 (Omi self-hosted): a configuração do usuário sai do Firestore e mora no
doc-store genérico do memory-service (via `omi_docs_karla`), nas coleções:
  - `users`         → doc raiz (doc_id=uid, `dados` = o doc do usuário inteiro);
  - `people`        → contatos/pessoas (doc_id=person_id);
  - `integrations`  → conexões de integração (doc_id=app_key);
  - `task_integrations` → conexões de task-integration (doc_id=app_key).
Este módulo replica as funções PÚBLICAS de config de database/users.py falando
com o cliente genérico. Ativação por env `CONFIG_KARLA=true` (ver rodapé de
database/users.py); com a flag off, nada muda (rollback = desligar).

⚠️ Flag própria: `is_enabled()` gateia por `CONFIG_KARLA` (NÃO reusa
`OMI_DOCS_KARLA`). O cliente `omi_docs_karla` só gateia a própria `is_enabled`
por `OMI_DOCS_KARLA`; suas funções de I/O (`upsert/obter/patch/listar/contar/
deletar/deletar_em_lote`) NÃO checam flag nenhuma — então são chamadas
diretamente aqui, e o gating fica todo no rodapé de users.py + neste
`is_enabled()`.

⚠️ Trap do merge RASO (omi_docs_karla.patch): substitui chaves de TOPO por
inteiro (sem deep-merge, sem notação de ponto, sem DELETE_FIELD / ArrayUnion /
transação). Toda atualização de estrutura ANINHADA ou array (transcription_
preferences.*, speech_samples[], assistant_settings deep-merge, platforms_used
ArrayUnion, default_task_integration delete) vira GET + merge client-side +
PATCH da chave de TOPO inteira. Documentado função a função.

Diferenças de semântica vs Firestore (documentadas):
  - `set_user_deletion_feedback` no original grava numa coleção de topo
    `account_deletions` (pra sobreviver ao delete do user). Aqui, SIMPLIFICAÇÃO:
    grava na chave `deletion_feedback` do próprio doc `users`.
  - `speech_samples` do original usa TRANSAÇÃO Firestore pra manter os 2 arrays
    (`speech_samples` + `speech_sample_transcripts`) alinhados. Aqui vira
    GET+merge preservando o mesmo alinhamento e o `speech_samples_version`.
  - `DELETE_FIELD` (clear_person_speaker_embedding, delete de default_task_
    integration, migração que limpa embedding) vira remoção da chave no merge
    client-side + PATCH do doc inteiro (o merge raso não apaga chaves sozinho,
    então re-escrevemos o doc todo via upsert).

Filosofia de erro: herdada do mold — erros de rede logam (warning+sanitize no
cliente) e a função devolve o DEFAULT/NEUTRO do ORIGINAL por função (nunca
raise), pra não derrubar o app se a Karla estiver indisponível.

Funções EXCLUÍDAS do shim (e do rodapé — ficam no Firestore): delete_user_data,
set_migration_status, finalize_migration, get_user_by_stripe_customer_id, todas
as de pagamento (stripe_account_id / paypal_details / default_payment_method /
stripe_customer_id) e as de analytics/ratings (set/get_conversation_summary_
rating_score, set_chat_message_rating_score, get_all_ratings).
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from database import omi_docs_karla as k
from database.redis_db import try_acquire_user_platform_write_lock
from models.users import Subscription, PlanType, SubscriptionStatus

# NOTE (circular import): `utils.subscription` ⇄ `database.users`, and the
# database.users CONFIG_KARLA footer imports THIS module. Safe ordering is
# deferred to the BOTTOM of this file (see the EOF import) — after every public
# function is defined — so any re-entry via the users.py footer finds a fully
# built module. get_default_basic_subscription is wired there.

logger = logging.getLogger(__name__)

_USERS = "users"
_PEOPLE = "people"
_INTEGRATIONS = "integrations"
_TASK_INTEGRATIONS = "task_integrations"

BYOK_HEARTBEAT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

# `_PLATFORM_ALIASES` / `_normalize_platform` são copiados do original (mesma
# normalização coarse desktop/mobile/web) pra `record_user_platform` funcionar
# idêntico via GET+merge.
_PLATFORM_ALIASES = {
    'macos': 'desktop',
    'mac': 'desktop',
    'mac os x': 'desktop',
    'desktop': 'desktop',
    'ios': 'mobile',
    'iphone os': 'mobile',
    'android': 'mobile',
    'mobile': 'mobile',
    'web': 'web',
    'browser': 'web',
}


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
    Sempre semeia `uid` no corpo (idempotente, sem sobrescrever um valor
    diferente já gravado — single-tenant, então é sempre igual ou ausente) pra
    permitir reconstruir o uid depois de um `k.listar` sem doc_id — mesmo
    idioma de `notifications_karla._merge_user` (ver docstring lá: cron de
    daily summary depende de `uid` em `dados` pra não cair no fallback
    `_SELF_HOST_DEFAULT_UID`)."""
    updates = dict(updates)
    updates.setdefault('uid', uid)
    if k.obter(_USERS, uid) is None:
        k.upsert(_USERS, uid, updates)
    else:
        k.patch(_USERS, uid, updates)


def _normalize_platform(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not raw or not isinstance(raw, str):
        return None, None
    os_value = raw.strip().lower()
    if not os_value:
        return None, None
    coarse = _PLATFORM_ALIASES.get(os_value)
    return coarse, os_value


# ****************************** DOC RAIZ (users) ******************************


def record_user_platform(uid: str, raw_platform: Optional[str]) -> None:
    """Espelha o original: normaliza a plataforma, THROTTLE via Redis
    (try_acquire_user_platform_write_lock), e faz o write dos campos de
    telemetria. ArrayUnion(`platforms_used`) e set_once(`signup_platform`)
    viram GET+merge client-side + PATCH. Fail-open: erros logam e são engolidos."""
    coarse, os_value = _normalize_platform(raw_platform)
    if not coarse:
        return

    try:
        if not try_acquire_user_platform_write_lock(uid, coarse):
            return

        now = datetime.now(timezone.utc)
        data = k.obter(_USERS, uid)
        exists = data is not None
        data = data or {}

        updates = {
            'last_active_platform': coarse,
            'last_active_os': os_value,
            'last_active_at': now,
            f'last_active_at_{coarse}': now,
        }

        # ArrayUnion(platforms_used) → union client-side.
        platforms_used = list(data.get('platforms_used', []) or [])
        if coarse not in platforms_used:
            platforms_used.append(coarse)
        updates['platforms_used'] = platforms_used

        # signup_platform é set_once.
        if exists:
            if not data.get('signup_platform'):
                updates['signup_platform'] = coarse
                updates['signup_os'] = os_value
                updates['signup_platform_at'] = data.get('created_at') or now
        else:
            updates['signup_platform'] = coarse
            updates['signup_os'] = os_value
            updates['signup_platform_at'] = now

        _merge_user(uid, updates)
    except Exception as e:  # noqa: BLE001
        logger.warning("record_user_platform (karla) failed for uid=%s: %s", uid, e)


def is_exists_user(uid: str) -> bool:
    return k.obter(_USERS, uid) is not None


def get_user_profile(uid: str) -> dict:
    """Doc raiz inteiro (ou {})."""
    return _get_user(uid)


def get_user_store_recording_permission(uid: str):
    return _get_user(uid).get('store_recording_permission', False)


def set_user_store_recording_permission(uid: str, value: bool):
    _merge_user(uid, {'store_recording_permission': value})


def get_user_private_cloud_sync_enabled(uid: str) -> bool:
    return _get_user(uid).get('private_cloud_sync_enabled', True)


def set_user_private_cloud_sync_enabled(uid: str, value: bool):
    _merge_user(uid, {'private_cloud_sync_enabled': value})


def set_user_cancellation_feedback(uid: str, reason: str, reason_details: Optional[str] = None):
    _merge_user(
        uid,
        {
            'cancellation_feedback': {
                'reason': reason,
                'reason_details': reason_details or '',
                'timestamp': datetime.now(timezone.utc),
            }
        },
    )


# ── BYOK ──────────────────────────────────────────────────────────────────────


def get_byok_state(uid: str) -> dict:
    return _get_user(uid).get('byok', {})


def is_byok_active(uid: str) -> bool:
    state = get_byok_state(uid)
    if not state.get('active'):
        return False
    last_seen = state.get('last_seen_at')
    if not last_seen:
        return False
    # Na Karla `last_seen_at` volta como string ISO — parseia; datetime também ok.
    if isinstance(last_seen, str):
        try:
            last_seen = datetime.fromisoformat(last_seen)
        except ValueError:
            return False
    if isinstance(last_seen, datetime):
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last_seen).total_seconds()
    else:
        return False
    return age <= BYOK_HEARTBEAT_TTL_SECONDS


def set_byok_active(uid: str, fingerprints: dict):
    _merge_user(
        uid,
        {
            'byok': {
                'active': True,
                'fingerprints': fingerprints,
                'last_seen_at': datetime.now(timezone.utc),
            }
        },
    )


def clear_byok_active(uid: str):
    _merge_user(
        uid,
        {
            'byok': {
                'active': False,
                'fingerprints': {},
                'last_seen_at': datetime.now(timezone.utc),
            }
        },
    )


def set_user_deletion_feedback(uid: str, reason: Optional[str], reason_details: Optional[str] = None):
    """SIMPLIFICAÇÃO vs original: grava na chave `deletion_feedback` do doc
    `users` (o original usava a coleção de topo `account_deletions` pra
    sobreviver ao delete do user; na Karla o doc-store não tem essa coleção
    allowlistada, então guardamos no próprio doc)."""
    _merge_user(
        uid,
        {
            'deletion_feedback': {
                'uid': uid,
                'reason': reason or '',
                'reason_details': reason_details or '',
                'timestamp': datetime.now(timezone.utc),
            }
        },
    )


# ── Speaker embedding (do próprio usuário) ────────────────────────────────────


def set_user_speaker_embedding(uid: str, embedding: list) -> bool:
    _merge_user(
        uid,
        {
            'speaker_embedding': embedding,
            'speaker_embedding_updated_at': datetime.now(timezone.utc),
        },
    )
    return True


def get_user_speaker_embedding(uid: str) -> Optional[list]:
    data = k.obter(_USERS, uid)
    if data is None:
        return None
    return data.get('speaker_embedding')


# ── Data Protection ───────────────────────────────────────────────────────────


def get_data_protection_level(uid: str) -> str:
    """'enhanced' ou 'e2ee'. Default 'enhanced' (doc ausente idem)."""
    data = k.obter(_USERS, uid)
    if data is not None:
        return data.get('data_protection_level', 'enhanced')
    return 'enhanced'


def set_data_protection_level(uid: str, level: str) -> None:
    if level not in ['enhanced', 'e2ee']:
        raise ValueError("Invalid data protection level. Only 'enhanced' or 'e2ee' are supported.")
    _merge_user(uid, {'data_protection_level': level})


# ── Language (HOT) ────────────────────────────────────────────────────────────


def get_user_language_preference(uid: str) -> str:
    """Código de idioma ou '' se não setado (doc ausente idem)."""
    data = k.obter(_USERS, uid)
    if data is not None:
        return data.get('language', '')
    return ''


def set_user_language_preference(uid: str, language: str) -> None:
    _merge_user(uid, {'language': language})


# ── Onboarding ────────────────────────────────────────────────────────────────


def get_user_onboarding_state(uid: str) -> dict:
    data = k.obter(_USERS, uid)
    if data is not None:
        return data.get('onboarding', {})
    return {}


def set_user_onboarding_state(uid: str, onboarding_data: dict) -> None:
    """Merge com o existente (o original usa set(merge=True) na chave inteira)."""
    _merge_user(uid, {'onboarding': onboarding_data})


# ── Subscription ──────────────────────────────────────────────────────────────


def update_user_subscription(uid: str, subscription_data: dict):
    """Remove campos dinâmicos (features/limits) antes de gravar, como o original."""
    subscription_data_to_store = subscription_data.copy()
    subscription_data_to_store.pop('features', None)
    subscription_data_to_store.pop('limits', None)
    _merge_user(uid, {'subscription': subscription_data_to_store})


def get_user_subscription(uid: str) -> Subscription:
    """Cria e persiste um default free se ausente (replica o original)."""
    data = k.obter(_USERS, uid)
    if data is not None and 'subscription' in data:
        sub_data = data['subscription']
        # Migração do identificador antigo 'free'.
        if sub_data.get('plan') == 'free':
            sub_data['plan'] = PlanType.basic.value
            update_user_subscription(uid, sub_data)
        return Subscription(**sub_data)

    # Cria default free e persiste.
    default_subscription = get_default_basic_subscription()
    sub_to_store = default_subscription.dict()
    sub_to_store.pop('features', None)
    sub_to_store.pop('limits', None)
    _merge_user(uid, {'subscription': sub_to_store})
    return default_subscription


def get_user_valid_subscription(uid: str) -> Optional[Subscription]:
    """Idêntico ao original (lógica de validade), reusando o get_user_subscription
    deste módulo."""
    subscription = get_user_subscription(uid)

    if subscription.plan == PlanType.basic:
        return subscription if subscription.status == SubscriptionStatus.active else None

    if subscription.current_period_end:
        period_end_dt = datetime.fromtimestamp(subscription.current_period_end, tz=timezone.utc)
        if period_end_dt >= datetime.now(timezone.utc):
            return subscription

    return get_default_basic_subscription()


# ── Training data opt-in ──────────────────────────────────────────────────────


def get_user_training_data_opt_in(uid: str) -> Optional[dict]:
    data = k.obter(_USERS, uid)
    if data is None:
        return None
    return data.get('training_data_opt_in', None)


def set_user_training_data_opt_in(uid: str, status: str):
    _merge_user(
        uid,
        {
            'training_data_opt_in': {
                'status': status,
                'requested_at': datetime.now(timezone.utc),
            }
        },
    )


# ── Transcription preferences ─────────────────────────────────────────────────


def get_user_transcription_preferences(uid: str) -> dict:
    """Injeta `language` de topo na resposta (como o original)."""
    data = k.obter(_USERS, uid)
    if data is not None:
        prefs = data.get('transcription_preferences', {}) or {}
        return {
            'single_language_mode': prefs.get('single_language_mode', False),
            'vocabulary': prefs.get('vocabulary', []),
            'language': data.get('language', ''),
        }
    return {'single_language_mode': False, 'vocabulary': [], 'language': ''}


def set_user_transcription_preferences(uid: str, single_language_mode: bool = None, vocabulary: list = None) -> None:
    """O original usa dot-path update (transcription_preferences.<campo>). Aqui:
    GET do sub-map + merge client-side + PATCH da chave `transcription_preferences`
    INTEIRA (SEM dot-keys no corpo do PATCH — o merge raso não interpreta ponto)."""
    if single_language_mode is None and vocabulary is None:
        return
    data = k.obter(_USERS, uid) or {}
    prefs = dict(data.get('transcription_preferences', {}) or {})
    if single_language_mode is not None:
        prefs['single_language_mode'] = single_language_mode
    if vocabulary is not None:
        prefs['vocabulary'] = vocabulary[:100]
    _merge_user(uid, {'transcription_preferences': prefs})


# ── Agent VM ──────────────────────────────────────────────────────────────────


def get_agent_vm(uid: str) -> Optional[dict]:
    data = k.obter(_USERS, uid)
    if data is None:
        return None
    return data.get('agentVm')


# ── Notification settings ─────────────────────────────────────────────────────


def get_notification_settings(uid: str) -> dict:
    """Mapeia os campos internos (`notifications_enabled`/`notification_frequency`)
    pros nomes de wire (`enabled`/`frequency`), como o original."""
    data = k.obter(_USERS, uid)
    if data is None:
        return {'enabled': True, 'frequency': 3}
    return {
        'enabled': data.get('notifications_enabled', True),
        'frequency': data.get('notification_frequency', 3),
    }


def update_notification_settings(uid: str, enabled: bool = None, frequency: int = None) -> dict:
    updates = {}
    if enabled is not None:
        updates['notifications_enabled'] = enabled
    if frequency is not None:
        updates['notification_frequency'] = frequency
    if updates:
        _merge_user(uid, updates)
    return get_notification_settings(uid)


# ── Assistant settings ────────────────────────────────────────────────────────


def _get_raw_assistant_settings(uid: str) -> dict:
    data = k.obter(_USERS, uid)
    if data is None:
        return {}
    return data.get('assistant_settings') or {}


def get_assistant_settings(uid: str) -> dict:
    """Injeta `update_channel` de topo na resposta (como o original)."""
    data = k.obter(_USERS, uid)
    if data is None:
        return {}
    result = (data.get('assistant_settings') or {}).copy()
    if data.get('update_channel') is not None:
        result['update_channel'] = data['update_channel']
    return result


def update_assistant_settings(uid: str, settings: dict) -> dict:
    """Deep-merge parcial em `assistant_settings` (client-side); `update_channel`
    vai pra chave de TOPO do doc. PATCH das chaves de topo (assistant_settings +
    update_channel) inteiras."""
    existing = _get_raw_assistant_settings(uid)

    update_channel = settings.pop('update_channel', None)

    for section, values in settings.items():
        if isinstance(values, dict) and isinstance(existing.get(section), dict):
            existing[section].update(values)
        else:
            existing[section] = values

    updates = {'assistant_settings': existing}
    if update_channel is not None:
        updates['update_channel'] = update_channel
    _merge_user(uid, updates)

    if update_channel is not None:
        existing['update_channel'] = update_channel
    return existing


# ── AI user profile ───────────────────────────────────────────────────────────


def get_ai_user_profile(uid: str) -> Optional[dict]:
    data = k.obter(_USERS, uid)
    if data is None:
        return None
    return data.get('ai_user_profile')


def update_ai_user_profile(
    uid: str, profile_text: str = None, generated_at=None, data_sources_used: int = None
) -> dict:
    """Partial update: só escreve os campos não-None, merge com o existente."""
    existing = get_ai_user_profile(uid) or {}
    if profile_text is not None:
        existing['profile_text'] = profile_text
    if generated_at is not None:
        existing['generated_at'] = generated_at
    if data_sources_used is not None:
        existing['data_sources_used'] = data_sources_used
    _merge_user(uid, {'ai_user_profile': existing})
    return existing


# ******************************** PEOPLE **********************************


def create_person(uid: str, data: dict):
    k.upsert(_PEOPLE, data['id'], data)
    return data


def get_person(uid: str, person_id: str):
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return None
    data.setdefault('id', person_id)
    return data


def get_people(uid: str):
    people = k.listar(_PEOPLE, limite=100000)
    for p in people:
        if isinstance(p, dict):
            p.setdefault('id', p.get('id'))
    return people


def get_person_by_name(uid: str, name: str):
    """Filtro jsonb containment {"name": name}, limite 1 (como o original)."""
    docs = k.listar(_PEOPLE, filtro={"name": name}, limite=1)
    if docs:
        data = docs[0]
        data.setdefault('id', data.get('id'))
        return data
    return None


def get_people_by_ids(uid: str, person_ids: list[str]):
    if not person_ids:
        return []
    people = k.listar(_PEOPLE, ids=[str(p) for p in person_ids], limite=100000)
    for p in people:
        if isinstance(p, dict):
            p.setdefault('id', p.get('id'))
    return people


def update_person(uid: str, person_id: str, name: str):
    k.patch(_PEOPLE, person_id, {'name': name})


def delete_person(uid: str, person_id: str):
    k.deletar(_PEOPLE, person_id)


def add_person_speech_sample(
    uid: str, person_id: str, sample_path: str, transcript: Optional[str] = None, max_samples: int = 5
) -> bool:
    """Append de sample. O original é uma TRANSAÇÃO Firestore mantendo os 2
    arrays (`speech_samples` + `speech_sample_transcripts`) alinhados + bumpando
    `speech_samples_version`. Aqui: GET + merge client-side preservando o mesmo
    alinhamento + PATCH das chaves inteiras."""
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return False

    samples = list(data.get('speech_samples', []) or [])
    if len(samples) >= max_samples:
        return False

    samples.append(sample_path)
    update_data = {
        'speech_samples': samples,
        'updated_at': datetime.now(timezone.utc),
    }

    if transcript is not None:
        transcripts = list(data.get('speech_sample_transcripts', []) or [])
        existing_sample_count = len(samples) - 1  # samples já tem o novo appendado
        if len(transcripts) < existing_sample_count:
            transcripts.extend([''] * (existing_sample_count - len(transcripts)))
        transcripts.append(transcript)
        update_data['speech_sample_transcripts'] = transcripts
        update_data['speech_samples_version'] = 3

    k.patch(_PEOPLE, person_id, update_data)
    return True


def get_person_speech_samples_count(uid: str, person_id: str) -> int:
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return 0
    return len(data.get('speech_samples', []) or [])


def remove_person_speech_sample(uid: str, person_id: str, sample_path: str) -> bool:
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return False

    samples = list(data.get('speech_samples', []) or [])
    transcripts = list(data.get('speech_sample_transcripts', []) or [])

    try:
        idx = samples.index(sample_path)
    except ValueError:
        return False

    samples.pop(idx)
    if idx < len(transcripts):
        transcripts.pop(idx)

    k.patch(
        _PEOPLE,
        person_id,
        {
            'speech_samples': samples,
            'speech_sample_transcripts': transcripts,
            'updated_at': datetime.now(timezone.utc),
        },
    )
    return True


def set_person_speaker_embedding(uid: str, person_id: str, embedding: list) -> bool:
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return False
    k.patch(
        _PEOPLE,
        person_id,
        {
            'speaker_embedding': embedding,
            'updated_at': datetime.now(timezone.utc),
        },
    )
    return True


def get_person_speaker_embedding(uid: str, person_id: str) -> Optional[list]:
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return None
    return data.get('speaker_embedding')


def set_person_speech_sample_transcript(uid: str, person_id: str, sample_index: int, transcript: str) -> bool:
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return False

    samples = list(data.get('speech_samples', []) or [])
    transcripts = list(data.get('speech_sample_transcripts', []) or [])

    if sample_index < 0 or sample_index >= len(samples):
        return False

    while len(transcripts) < len(samples):
        transcripts.append('')

    transcripts[sample_index] = transcript

    k.patch(
        _PEOPLE,
        person_id,
        {
            'speech_sample_transcripts': transcripts,
            'updated_at': datetime.now(timezone.utc),
        },
    )
    return True


def _rewrite_person(uid: str, person_id: str, data: dict) -> None:
    """Re-escreve o doc person inteiro via upsert. Usado quando precisamos
    REMOVER chave (DELETE_FIELD) — o PATCH raso não apaga chaves sozinho."""
    k.upsert(_PEOPLE, person_id, data)


def update_person_speech_samples_after_migration(
    uid: str,
    person_id: str,
    samples: list,
    transcripts: list,
    version: int,
    speaker_embedding: Optional[list] = None,
) -> bool:
    """Substitui samples/transcripts/version; embedding: set OU clear
    (DELETE_FIELD → remove a chave via reescrita do doc)."""
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return False

    data['speech_samples'] = samples
    data['speech_sample_transcripts'] = transcripts
    data['speech_samples_version'] = version
    data['updated_at'] = datetime.now(timezone.utc)

    if speaker_embedding is not None:
        data['speaker_embedding'] = speaker_embedding
    else:
        data.pop('speaker_embedding', None)

    _rewrite_person(uid, person_id, data)
    return True


def clear_person_speaker_embedding(uid: str, person_id: str) -> bool:
    """DELETE_FIELD(speaker_embedding) → remove a chave via reescrita do doc."""
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return False
    data.pop('speaker_embedding', None)
    data['updated_at'] = datetime.now(timezone.utc)
    _rewrite_person(uid, person_id, data)
    return True


def update_person_speech_samples_version(uid: str, person_id: str, version: int) -> bool:
    data = k.obter(_PEOPLE, person_id)
    if data is None:
        return False
    k.patch(
        _PEOPLE,
        person_id,
        {
            'speech_samples_version': version,
            'updated_at': datetime.now(timezone.utc),
        },
    )
    return True


# ****************************** INTEGRATIONS ******************************


def get_integration(uid: str, app_key: str) -> Optional[dict]:
    return k.obter(_INTEGRATIONS, app_key)


def set_integration(uid: str, app_key: str, data: dict) -> None:
    """Injeta updated_at sempre e created_at se novo (como o original). Merge
    das chaves de topo (set(merge=True) do original)."""
    existing = k.obter(_INTEGRATIONS, app_key)
    now = datetime.now(timezone.utc)
    data['updated_at'] = now
    if existing is None:
        data['created_at'] = now
        k.upsert(_INTEGRATIONS, app_key, data)
    else:
        k.patch(_INTEGRATIONS, app_key, data)


def delete_integration(uid: str, app_key: str) -> bool:
    if k.obter(_INTEGRATIONS, app_key) is None:
        return False
    k.deletar(_INTEGRATIONS, app_key)
    return True


# **************************** TASK INTEGRATIONS ***************************


def get_task_integrations(uid: str) -> dict:
    """Todas as conexões: dict {app_key: dados}. Na Karla listamos a coleção e
    reconstruímos o dict pelo doc_id (== app_key). Como `listar` devolve só
    `dados` (sem doc_id), usamos o próprio dados como valor e — quando presente —
    um campo `app_key`/`id` interno; senão, caímos no fallback abaixo."""
    docs = k.listar(_TASK_INTEGRATIONS, limite=100000)
    result = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        key = d.get('app_key') or d.get('id')
        if key:
            result[key] = d
    return result


def get_task_integration(uid: str, app_key: str) -> Optional[dict]:
    return k.obter(_TASK_INTEGRATIONS, app_key)


def set_task_integration(uid: str, app_key: str, data: dict) -> None:
    """Injeta updated_at sempre e created_at se novo. Grava `app_key` no dados
    pra permitir reconstruir o dict em get_task_integrations (listar não devolve
    doc_id)."""
    existing = k.obter(_TASK_INTEGRATIONS, app_key)
    now = datetime.now(timezone.utc)
    data['updated_at'] = now
    data.setdefault('app_key', app_key)
    if existing is None:
        data['created_at'] = now
        k.upsert(_TASK_INTEGRATIONS, app_key, data)
    else:
        k.patch(_TASK_INTEGRATIONS, app_key, data)


def delete_task_integration(uid: str, app_key: str) -> bool:
    """Deleta a conexão e limpa `default_task_integration` do doc `users` se
    bater (DELETE_FIELD → remove a chave via reescrita do doc)."""
    if k.obter(_TASK_INTEGRATIONS, app_key) is None:
        return False

    user_data = k.obter(_USERS, uid) or {}
    is_default = user_data.get('default_task_integration') == app_key

    k.deletar(_TASK_INTEGRATIONS, app_key)

    if is_default:
        user_data.pop('default_task_integration', None)
        user_data['uid'] = uid
        k.upsert(_USERS, uid, user_data)

    return True


def get_default_task_integration(uid: str) -> Optional[str]:
    data = k.obter(_USERS, uid)
    if data is None:
        return None
    return data.get('default_task_integration')


def set_default_task_integration(uid: str, app_key: str) -> None:
    _merge_user(uid, {'default_task_integration': app_key})


# ── Deferred import (circular-import guard, see module docstring / top note) ──
# The cycle is utils.subscription ⇄ database.users, and database.users' footer
# imports THIS module. Importing `database.users` (not utils.subscription) here,
# and at EOF, makes database.users the cycle entry point — the ONLY order the
# pre-existing utils.subscription↔database.users cycle survives (subscription
# uses users lazily; users needs subscription's factory at its line 10). By the
# time we get here users_karla is fully defined, so the users.py footer re-entry
# binds cleanly. We pull get_default_basic_subscription off the database.users
# module (it re-exports it), resolved at call time in get_user_subscription /
# get_user_valid_subscription.
from database import users as _users_mod  # noqa: E402


def get_default_basic_subscription():
    return _users_mod.get_default_basic_subscription()
