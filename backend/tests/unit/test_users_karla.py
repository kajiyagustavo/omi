import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import requests as _real_requests


def _requests_mock():
    """A `requests` mock whose exception classes stay REAL, so the shim's
    `except requests.HTTPError` clauses don't blow up with 'catching classes
    that do not inherit from BaseException'."""
    m = MagicMock()
    m.HTTPError = _real_requests.HTTPError
    m.RequestException = _real_requests.RequestException
    return m


os.environ.setdefault("MEMORIA_UNIFICADA_URL", "http://karla-fake:8765")
os.environ.setdefault("MEMORIA_UNIFICADA_TOKEN", "tok-teste")
os.environ.setdefault("CONFIG_KARLA", "true")

# Stub Firestore client so importing the modules under test doesn't trigger ADC lookups.
_client = types.ModuleType("database._client")
_client.db = MagicMock()
_client.document_id_from_seed = lambda s: "id_" + str(abs(hash(s)) % 100000)
sys.modules["database._client"] = _client

# Stub opuslib + utils.other.storage — omi_docs_karla imports database.conversations_karla,
# which reaches utils.other.storage at import time.
sys.modules.setdefault("opuslib", MagicMock())
_storage = types.ModuleType("utils.other.storage")
_storage.list_audio_chunks = lambda uid, cid: []
sys.modules["utils.other.storage"] = _storage

# Import order matters: database.users participates in a pre-existing circular
# import with utils.subscription (users → utils.subscription → users). Importing
# database.users FIRST lets utils.subscription finish initializing before
# users_karla pulls get_default_basic_subscription from it — same order as the
# real app boot. Importing users_karla first would hit a partially-initialized
# utils.subscription.
from database import users as users_mod  # noqa: E402
from database import users_karla as uk  # noqa: E402
from database import omi_docs_karla as odk  # noqa: E402
from database import auth as auth_mod  # noqa: E402
from models.users import PlanType, SubscriptionStatus  # noqa: E402


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


# ── language (HOT) ────────────────────────────────────────────────────────────


def test_language_get_colecao_doc_id_correto():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"dados": {"language": "pt"}})
        assert uk.get_user_language_preference("uid-1") == "pt"
        args, kwargs = rq.request.call_args
        assert args[0] == "GET"
        assert args[1].endswith("/u/tok-teste/omi-docs/users/uid-1")


def test_language_set_merge_patch():
    with patch.object(odk, "requests") as rq:
        # obter (existe) → patch
        rq.request.side_effect = [
            _resp({"dados": {"language": "en"}}),  # obter dentro de _merge_user
            _resp({"dados": {"language": "vi"}}),  # patch
        ]
        uk.set_user_language_preference("uid-1", "vi")
        patch_call = rq.request.call_args_list[-1]
        assert patch_call[0][0] == "PATCH"
        assert patch_call[0][1].endswith("/u/tok-teste/omi-docs/users/uid-1")
        assert patch_call[1]["json"]["dados_merge"] == {"language": "vi"}


def test_language_get_default_vazio_quando_ausente():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp(None, status=404)
        assert uk.get_user_language_preference("uid-x") == ""


# ── subscription ──────────────────────────────────────────────────────────────


def test_subscription_default_free_quando_doc_ausente():
    with patch.object(odk, "requests") as rq:
        # get_user_subscription: obter(users)=404 → cria default. _merge_user:
        # obter(users)=404 → upsert.
        rq.request.side_effect = [
            _resp(None, status=404),  # obter no get_user_subscription
            _resp(None, status=404),  # obter dentro de _merge_user
            _resp({"ok": True}, status=201),  # upsert do default
        ]
        sub = uk.get_user_subscription("uid-1")
        assert sub.plan == PlanType.basic
        assert sub.status == SubscriptionStatus.active
        # último request grava a subscription default
        last = rq.request.call_args_list[-1]
        assert last[0][0] == "POST"
        assert "subscription" in last[1]["json"]["dados"]


def test_subscription_existente_nao_regrava():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"dados": {"subscription": {"plan": "basic", "status": "active"}}})
        sub = uk.get_user_subscription("uid-1")
        assert sub.plan == PlanType.basic
        # só um GET, sem POST/PATCH
        assert rq.request.call_count == 1


# ── transcription_preferences (GET+merge, sem dot-keys) ───────────────────────


def test_transcription_prefs_merge_sem_dot_keys():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"transcription_preferences": {"vocabulary": ["omi"]}}}),  # obter (sub-map)
            _resp({"dados": {}}),  # obter dentro de _merge_user
            _resp({"dados": {}}),  # patch
        ]
        uk.set_user_transcription_preferences("uid-1", single_language_mode=True)
        patch_call = rq.request.call_args_list[-1]
        body = patch_call[1]["json"]["dados_merge"]
        # chave de topo inteira, sem notação de ponto
        assert "transcription_preferences" in body
        assert not any("." in key for key in body)
        prefs = body["transcription_preferences"]
        assert prefs["single_language_mode"] is True
        assert prefs["vocabulary"] == ["omi"]  # preserva o irmão


def test_transcription_prefs_get_injeta_language():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp(
            {"dados": {"language": "pt", "transcription_preferences": {"single_language_mode": True}}}
        )
        result = uk.get_user_transcription_preferences("uid-1")
        assert result == {"single_language_mode": True, "vocabulary": [], "language": "pt"}


# ── people: add_person_speech_sample preserva arrays irmãos + bump version ─────


def test_add_person_speech_sample_preserva_arrays_e_bump_version():
    with patch.object(odk, "requests") as rq:
        existing = {
            "dados": {
                "speech_samples": ["s0.wav"],
                "speech_sample_transcripts": ["t0"],
                "speaker_embedding": [1.0, 2.0],
            }
        }
        rq.request.side_effect = [
            _resp(existing),  # obter person
            _resp({"dados": {}}),  # patch
        ]
        ok = uk.add_person_speech_sample("uid", "p1", "s1.wav", transcript="t1")
        assert ok is True
        patch_call = rq.request.call_args_list[-1]
        assert patch_call[0][0] == "PATCH"
        assert patch_call[0][1].endswith("/u/tok-teste/omi-docs/people/p1")
        body = patch_call[1]["json"]["dados_merge"]
        assert body["speech_samples"] == ["s0.wav", "s1.wav"]
        assert body["speech_sample_transcripts"] == ["t0", "t1"]
        assert body["speech_samples_version"] == 3
        # NÃO tocamos no embedding no patch (preservado pelo merge raso de topo)
        assert "speaker_embedding" not in body


def test_add_person_speech_sample_person_ausente_retorna_false():
    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp(None, status=404)
        assert uk.add_person_speech_sample("uid", "nope", "s.wav") is False


# ── people: get_person_by_name filtro ─────────────────────────────────────────


def test_get_person_by_name_filtro_e_limite():
    import json

    with patch.object(odk, "requests") as rq:
        rq.request.return_value = _resp({"docs": [{"dados": {"id": "p1", "name": "Ana"}}]})
        result = uk.get_person_by_name("uid", "Ana")
        assert result["id"] == "p1"
        args, kwargs = rq.request.call_args
        assert args[0] == "GET"
        filtro = json.loads(kwargs["params"]["filtro"])
        assert filtro == {"name": "Ana"}
        assert kwargs["params"]["limite"] == 1


# ── clear_person_speaker_embedding: DELETE_FIELD remove a chave ───────────────


def test_clear_person_speaker_embedding_remove_chave_via_reescrita():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"speaker_embedding": [1.0], "name": "Ana"}}),  # obter
            _resp({"ok": True}, status=201),  # upsert (reescrita)
        ]
        ok = uk.clear_person_speaker_embedding("uid", "p1")
        assert ok is True
        upsert_call = rq.request.call_args_list[-1]
        assert upsert_call[0][0] == "POST"
        dados = upsert_call[1]["json"]["dados"]
        assert "speaker_embedding" not in dados
        assert dados["name"] == "Ana"


# ── task_integrations: delete limpa default via reescrita do doc users ────────


def test_delete_task_integration_limpa_default():
    with patch.object(odk, "requests") as rq:
        rq.request.side_effect = [
            _resp({"dados": {"app_key": "todoist"}}),  # obter task_integration (existe)
            _resp({"dados": {"default_task_integration": "todoist", "language": "pt"}}),  # obter users
            _resp(None, status=204),  # deletar task_integration
            _resp({"ok": True}, status=201),  # upsert users (limpa default)
        ]
        assert uk.delete_task_integration("uid", "todoist") is True
        upsert_call = rq.request.call_args_list[-1]
        assert upsert_call[0][0] == "POST"
        assert upsert_call[0][1].endswith("/u/tok-teste/omi-docs/users")
        body = upsert_call[1]["json"]
        assert body["doc_id"] == "uid"
        dados = body["dados"]
        assert "default_task_integration" not in dados
        assert dados["language"] == "pt"  # preserva o resto do doc


# ── record_user_platform: throttle + ArrayUnion via GET+merge ─────────────────


def test_record_user_platform_throttle_bloqueia():
    with patch.object(uk, "try_acquire_user_platform_write_lock", return_value=False):
        with patch.object(odk, "requests") as rq:
            uk.record_user_platform("uid", "macos")
            # lock negado → nenhum I/O
            assert rq.request.call_count == 0


def test_record_user_platform_array_union_e_signup_once():
    with patch.object(uk, "try_acquire_user_platform_write_lock", return_value=True):
        with patch.object(odk, "requests") as rq:
            rq.request.side_effect = [
                _resp({"dados": {"platforms_used": ["mobile"], "signup_platform": "mobile"}}),  # obter (record)
                _resp({"dados": {}}),  # obter dentro de _merge_user
                _resp({"dados": {}}),  # patch
            ]
            uk.record_user_platform("uid", "macos")
            patch_call = rq.request.call_args_list[-1]
            body = patch_call[1]["json"]["dados_merge"]
            assert set(body["platforms_used"]) == {"mobile", "desktop"}
            # signup já existe → set_once não sobrescreve
            assert "signup_platform" not in body


# ── auth name fallback lê Karla quando flag on ────────────────────────────────


def test_auth_name_fallback_le_karla():
    with patch.object(uk, "is_enabled", return_value=True):
        with patch.object(uk, "get_user_profile", return_value={"name": "Ana Silva"}) as gp:
            assert auth_mod._get_firestore_user_name("uid") == "Ana"
            gp.assert_called_once_with("uid")


def test_auth_name_fallback_flag_off_usa_firestore():
    with patch.object(uk, "is_enabled", return_value=False):
        fake_doc = MagicMock()
        fake_doc.exists = True
        fake_doc.to_dict.return_value = {"name": "Bob Marley"}
        with patch.object(auth_mod, "db") as fdb:
            fdb.collection.return_value.document.return_value.get.return_value = fake_doc
            assert auth_mod._get_firestore_user_name("uid") == "Bob"


# ── footer rebind ─────────────────────────────────────────────────────────────


def test_users_footer_rebind():
    assert users_mod.get_user_language_preference is uk.get_user_language_preference
    assert users_mod.get_user_subscription is uk.get_user_subscription
    assert users_mod.create_person is uk.create_person
    assert users_mod.get_person_by_name is uk.get_person_by_name
    assert users_mod.set_integration is uk.set_integration
    assert users_mod.set_task_integration is uk.set_task_integration
    assert users_mod.record_user_platform is uk.record_user_platform


def test_footer_nao_rebinda_excluidas():
    # pagamentos / analytics / delete_user_data continuam Firestore (não viram
    # o shim — users_karla nem define essas funções).
    assert not hasattr(uk, "delete_user_data")
    assert not hasattr(uk, "get_stripe_customer_id")
    assert not hasattr(uk, "get_all_ratings")


# ── erro de rede → default do original (não raise) ────────────────────────────


def test_erro_de_rede_language_devolve_default():
    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(odk, "requests", new=rq):
        # obter engole o erro e devolve None → getter devolve ''
        assert uk.get_user_language_preference("uid") == ""


def test_erro_de_rede_is_byok_active_false():
    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(odk, "requests", new=rq):
        assert uk.is_byok_active("uid") is False


def test_erro_de_rede_notification_settings_default():
    rq = _requests_mock()
    rq.request.side_effect = RuntimeError("boom")
    with patch.object(odk, "requests", new=rq):
        assert uk.get_notification_settings("uid") == {"enabled": True, "frequency": 3}
