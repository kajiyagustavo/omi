"""
Tests for the CONVERSAS_KARLA gates in database/vector_db.py (ns1 = conversas).

Mirrors the MEMORIAS_KARLA gate idiom already present in the same module
(upsert_memory_vector, delete_memory_vector, etc.): flag on → Pinecone is
never touched, writes are no-ops, and reads/search delegate to the Karla
shim (conversations_karla.buscar_conversas_ids). Flag off → Pinecone path is
byte-identical to pre-gate behavior.
"""

import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Stub heavy deps before importing vector_db (same pattern as test_action_item_dedup
# and test_conversas_karla — vector_db now imports database.conversations_karla at
# module level, which pulls in utils.other.storage / opuslib / hume / audio_file).
for mod_name in [
    'pinecone',
    'firebase_admin',
    'firebase_admin.auth',
    'google',
    'google.cloud',
    'google.cloud.firestore',
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules['pinecone'].Pinecone = MagicMock


class _FakeFirestoreClient:
    def collection(self, *a, **kw):
        return MagicMock()

    def batch(self):
        return MagicMock()


sys.modules['google.cloud.firestore'].Client = _FakeFirestoreClient
sys.modules['google.cloud.firestore'].ArrayUnion = MagicMock
sys.modules['google.cloud.firestore'].ArrayRemove = MagicMock
sys.modules['google.cloud.firestore'].Increment = MagicMock
sys.modules['google.cloud.firestore'].SERVER_TIMESTAMP = object()
sys.modules['google.cloud.firestore'].DELETE_FIELD = object()
sys.modules['google.cloud.firestore'].FieldFilter = MagicMock
sys.modules['google.cloud.firestore'].Query = MagicMock
sys.modules['firebase_admin.auth'].InvalidIdTokenError = type('InvalidIdTokenError', (Exception,), {})

if 'utils.llm.clients' not in sys.modules:
    clients_stub = types.ModuleType('utils.llm.clients')
    clients_stub.embeddings = MagicMock()
    sys.modules['utils.llm.clients'] = clients_stub

# conversations_karla pulls in opuslib + utils.other.storage (GCS client at import
# time) — stub the whole chain so import stays credential-free (mirrors
# test_conversas_karla.py).
sys.modules.setdefault('opuslib', MagicMock())
if 'utils.other.storage' not in sys.modules:
    _storage = types.ModuleType('utils.other.storage')
    _storage.list_audio_chunks = lambda uid, cid: []
    sys.modules['utils.other.storage'] = _storage

from database import conversations_karla  # noqa: E402
from database import vector_db  # noqa: E402


def _set_flag(monkeypatch, enabled: bool):
    monkeypatch.setattr(conversations_karla, 'is_enabled', lambda: enabled)


def _fake_index(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(vector_db, 'index', fake)
    return fake


class TestWriteNoOpsWhenFlagOn:
    def test_upsert_vector_no_op(self, monkeypatch):
        _set_flag(monkeypatch, True)
        fake_index = _fake_index(monkeypatch)

        vector_db.upsert_vector('uid-1', 'conv-1', [0.1, 0.2])

        fake_index.upsert.assert_not_called()

    def test_upsert_vector2_no_op(self, monkeypatch):
        _set_flag(monkeypatch, True)
        fake_index = _fake_index(monkeypatch)

        vector_db.upsert_vector2('uid-1', 'conv-1', [0.1, 0.2], {'topics': ['x']})

        fake_index.upsert.assert_not_called()

    def test_update_vector_metadata_no_op(self, monkeypatch):
        _set_flag(monkeypatch, True)
        fake_index = _fake_index(monkeypatch)

        result = vector_db.update_vector_metadata('uid-1', 'conv-1', {'foo': 'bar'})

        fake_index.update.assert_not_called()
        assert result is None

    def test_upsert_vectors_no_op(self, monkeypatch):
        _set_flag(monkeypatch, True)
        fake_index = _fake_index(monkeypatch)

        vector_db.upsert_vectors('uid-1', [[0.1], [0.2]], ['conv-1', 'conv-2'])

        fake_index.upsert.assert_not_called()

    def test_delete_vector_no_op(self, monkeypatch):
        _set_flag(monkeypatch, True)
        fake_index = _fake_index(monkeypatch)

        vector_db.delete_vector('uid-1', 'conv-1')

        fake_index.delete.assert_not_called()


class TestQueryVectorsDelegatesToKarla:
    def test_query_vectors_delegates_with_converted_params(self, monkeypatch):
        _set_flag(monkeypatch, True)
        fake_index = _fake_index(monkeypatch)

        with patch.object(conversations_karla, 'buscar_conversas_ids', return_value=['c1', 'c2']) as mock_buscar:
            result = vector_db.query_vectors('reunião', 'uid-1', starts_at=1000, ends_at=2000, k=7)

        mock_buscar.assert_called_once_with('reunião', 1000, 2000, 7)
        assert result == ['c1', 'c2']
        fake_index.query.assert_not_called()

    def test_query_vectors_defaults(self, monkeypatch):
        _set_flag(monkeypatch, True)
        _fake_index(monkeypatch)

        with patch.object(conversations_karla, 'buscar_conversas_ids', return_value=[]) as mock_buscar:
            vector_db.query_vectors('reunião', 'uid-1')

        mock_buscar.assert_called_once_with('reunião', None, None, 5)

    def test_query_vectors_flag_off_uses_pinecone(self, monkeypatch):
        _set_flag(monkeypatch, False)
        fake_index = _fake_index(monkeypatch)
        fake_index.query.return_value = {'matches': [{'id': 'uid-1-conv-1'}]}
        fake_embeddings = MagicMock()
        fake_embeddings.embed_query = MagicMock(return_value=[0.1, 0.2])
        monkeypatch.setattr(vector_db, 'embeddings', fake_embeddings)

        with patch.object(conversations_karla, 'buscar_conversas_ids') as mock_buscar:
            result = vector_db.query_vectors('reunião', 'uid-1', starts_at=1000, ends_at=2000, k=5)

        mock_buscar.assert_not_called()
        fake_index.query.assert_called_once()
        assert result == ['conv-1']


class TestQueryVectorsByMetadata:
    def test_joins_terms_and_delegates(self, monkeypatch):
        _set_flag(monkeypatch, True)
        _fake_index(monkeypatch)
        dates_filter = [
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 31, tzinfo=timezone.utc),
        ]

        with patch.object(conversations_karla, 'buscar_conversas_ids', return_value=['c9']) as mock_buscar:
            result = vector_db.query_vectors_by_metadata(
                'uid-1',
                [0.1],
                dates_filter,
                people=['ana'],
                topics=['trabalho'],
                entities=['jamjoy'],
                dates=['ontem'],
                limit=3,
            )

        expected_start = int(dates_filter[0].timestamp())
        expected_end = int(dates_filter[1].timestamp())
        mock_buscar.assert_called_once_with('ana trabalho jamjoy ontem', expected_start, expected_end, 3)
        assert result == ['c9']

    def test_early_returns_empty_when_no_terms(self, monkeypatch):
        _set_flag(monkeypatch, True)
        fake_index = _fake_index(monkeypatch)

        with patch.object(conversations_karla, 'buscar_conversas_ids') as mock_buscar:
            result = vector_db.query_vectors_by_metadata(
                'uid-1', [0.1], None, people=[], topics=[], entities=[], dates=[]
            )

        assert result == []
        mock_buscar.assert_not_called()
        fake_index.query.assert_not_called()

    def test_no_dates_filter_omits_range(self, monkeypatch):
        _set_flag(monkeypatch, True)
        _fake_index(monkeypatch)

        with patch.object(conversations_karla, 'buscar_conversas_ids', return_value=[]) as mock_buscar:
            vector_db.query_vectors_by_metadata('uid-1', [0.1], None, people=['ana'], topics=[], entities=[], dates=[])

        mock_buscar.assert_called_once_with('ana', None, None, 5)

    def test_flag_off_uses_pinecone(self, monkeypatch):
        _set_flag(monkeypatch, False)
        fake_index = _fake_index(monkeypatch)
        fake_index.query.return_value = {'matches': []}

        with patch.object(conversations_karla, 'buscar_conversas_ids') as mock_buscar:
            result = vector_db.query_vectors_by_metadata(
                'uid-1', [0.1], None, people=[], topics=[], entities=[], dates=[]
            )

        mock_buscar.assert_not_called()
        fake_index.query.assert_called_once()
        assert result == []
