"""
Unit tests for the auto-extraction kill-switch gate.

Verifies that OMI_AUTO_EXTRACTION_ENABLED controls the auto_extraction_enabled()
helper without requiring a module reload (function reads os.getenv at call time).
"""

import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault(
    "ENCRYPTION_SECRET",
    "omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv",
)


def _stub_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# Stub database package and submodules to avoid heavy imports.
database_mod = _stub_module("database")
database_mod.__path__ = []
for _submodule in [
    "redis_db",
    "memories",
    "conversations",
    "notifications",
    "users",
    "tasks",
    "trends",
    "action_items",
    "folders",
    "calendar_meetings",
    "vector_db",
    "apps",
    "llm_usage",
    "_client",
]:
    _mod = _stub_module(f"database.{_submodule}")
    setattr(database_mod, _submodule, _mod)

vector_db_mod = sys.modules["database.vector_db"]
for _attr in [
    "find_similar_memories",
    "upsert_memory_vector",
    "delete_memory_vector",
    "upsert_vector2",
    "update_vector_metadata",
    "upsert_action_item_vectors_batch",
    "delete_action_item_vectors_batch",
    "find_similar_action_items",
]:
    setattr(vector_db_mod, _attr, MagicMock())

apps_mod = sys.modules["database.apps"]
for _attr in ["record_app_usage", "get_omi_personas_by_uid_db", "get_app_by_id_db"]:
    setattr(apps_mod, _attr, MagicMock())

llm_usage_mod = sys.modules["database.llm_usage"]
llm_usage_mod.record_llm_usage = MagicMock()

users_mod = sys.modules["database.users"]
for _attr in ["get_user_language_preference", "get_people_by_ids"]:
    setattr(users_mod, _attr, MagicMock(return_value=None))

client_mod = sys.modules["database._client"]
client_mod.document_id_from_seed = MagicMock(return_value="doc-id")

# Stub utils modules that pull in external dependencies.
for _name in [
    "utils.apps",
    "utils.analytics",
    "utils.llm.memories",
    "utils.llm.conversation_processing",
    "utils.llm.external_integrations",
    "utils.llm.trends",
    "utils.llm.goals",
    "utils.llm.chat",
    "utils.llm.clients",
    "utils.notifications",
    "utils.other.hume",
    "utils.retrieval.rag",
    "utils.webhooks",
    "utils.task_sync",
    "utils.other.storage",
]:
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

utils_apps = sys.modules["utils.apps"]
for _attr in ["get_available_apps", "update_personas_async", "update_persona_prompt"]:
    setattr(utils_apps, _attr, MagicMock())

utils_analytics = sys.modules["utils.analytics"]
utils_analytics.record_usage = MagicMock()

llm_memories = sys.modules["utils.llm.memories"]
for _attr in ["resolve_memory_conflict", "extract_memories_from_text", "new_memories_extractor"]:
    setattr(llm_memories, _attr, MagicMock())

llm_conv = sys.modules["utils.llm.conversation_processing"]
for _attr in [
    "get_transcript_structure",
    "get_app_result",
    "should_discard_conversation",
    "select_best_app_for_conversation",
    "get_suggested_apps_for_conversation",
    "get_reprocess_transcript_structure",
    "assign_conversation_to_folder",
    "extract_action_items",
]:
    setattr(llm_conv, _attr, MagicMock())

llm_external = sys.modules["utils.llm.external_integrations"]
for _attr in ["summarize_experience_text", "get_message_structure"]:
    setattr(llm_external, _attr, MagicMock())

llm_trends = sys.modules["utils.llm.trends"]
llm_trends.trends_extractor = MagicMock()

llm_goals = sys.modules["utils.llm.goals"]
llm_goals.extract_and_update_goal_progress = MagicMock()

llm_chat = sys.modules["utils.llm.chat"]
for _attr in [
    "retrieve_metadata_from_text",
    "retrieve_metadata_from_message",
    "retrieve_metadata_fields_from_transcript",
    "obtain_emotional_message",
]:
    setattr(llm_chat, _attr, MagicMock())

llm_clients = sys.modules["utils.llm.clients"]
llm_clients.generate_embedding = MagicMock()

utils_notifications = sys.modules["utils.notifications"]
for _attr in ["send_notification", "send_important_conversation_message", "send_action_item_data_message"]:
    setattr(utils_notifications, _attr, MagicMock())

utils_hume = sys.modules["utils.other.hume"]
for _attr in ["get_hume", "HumeJobCallbackModel", "HumeJobModelPredictionResponseModel"]:
    setattr(utils_hume, _attr, MagicMock())

utils_rag = sys.modules["utils.retrieval.rag"]
utils_rag.retrieve_rag_conversation_context = MagicMock()

utils_webhooks = sys.modules["utils.webhooks"]
utils_webhooks.conversation_created_webhook = MagicMock()

utils_task_sync = sys.modules["utils.task_sync"]
utils_task_sync.auto_sync_action_items_batch = MagicMock()

utils_storage = sys.modules["utils.other.storage"]
utils_storage.precache_conversation_audio = MagicMock()

# Stub utils.llm and utils.llm.usage_tracker to avoid langchain_core import
_utils_llm_mod = _stub_module("utils.llm")
_utils_llm_mod.__path__ = []
_usage_tracker_mod = _stub_module("utils.llm.usage_tracker")
from contextlib import contextmanager as _contextmanager


@_contextmanager
def _fake_track_usage(uid, feature):
    yield


_usage_tracker_mod.track_usage = _fake_track_usage
_usage_tracker_mod.get_current_context = MagicMock(return_value=None)


class _FakeFeatures:
    CONVERSATION_DISCARD = "CONVERSATION_DISCARD"
    CONVERSATION_STRUCTURE = "CONVERSATION_STRUCTURE"
    CONVERSATION_ACTION_ITEMS = "CONVERSATION_ACTION_ITEMS"
    CONVERSATION_FOLDER = "CONVERSATION_FOLDER"
    CONVERSATION_APPS = "CONVERSATION_APPS"
    CONVERSATION_PROCESSING = "CONVERSATION_PROCESSING"
    MEMORIES = "MEMORIES"
    TRENDS = "TRENDS"


_usage_tracker_mod.Features = _FakeFeatures
setattr(_utils_llm_mod, "usage_tracker", _usage_tracker_mod)

import importlib

process_conversation = importlib.import_module("utils.conversations.process_conversation")
auto_extraction_enabled = process_conversation.auto_extraction_enabled

# ---------------------------------------------------------------------------
# Tests for OMI_AUTO_EXTRACTION_ENABLED gate
# ---------------------------------------------------------------------------


def test_default_true():
    """When env var is absent, auto-extraction is enabled by default."""
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)
    assert auto_extraction_enabled() is True


def test_false_disables():
    """Value 'false' disables auto-extraction."""
    os.environ['OMI_AUTO_EXTRACTION_ENABLED'] = 'false'
    assert auto_extraction_enabled() is False
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)


def test_false_case_insensitive():
    """Value 'FALSE' (uppercase) also disables auto-extraction."""
    os.environ['OMI_AUTO_EXTRACTION_ENABLED'] = 'FALSE'
    assert auto_extraction_enabled() is False
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)


def test_true_stays_enabled():
    """Value 'true' keeps auto-extraction enabled."""
    os.environ['OMI_AUTO_EXTRACTION_ENABLED'] = 'true'
    assert auto_extraction_enabled() is True
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)


def test_yes_stays_enabled():
    """Non-'false' value like 'yes' keeps auto-extraction enabled."""
    os.environ['OMI_AUTO_EXTRACTION_ENABLED'] = 'yes'
    assert auto_extraction_enabled() is True
    os.environ.pop('OMI_AUTO_EXTRACTION_ENABLED', None)
