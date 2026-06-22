"""Unit tests for derive_memory_source — maps a conversation's source/app_id to MemorySource.

Reuses the heavy-import stub strategy from test_process_conversation_usage_context.py so
that importing utils.conversations.process_conversation does not require GCP/OpenAI creds.
models.* are left real so the MemorySource/ConversationSource enums are the genuine ones.
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault(
    "ENCRYPTION_SECRET",
    "omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv",
)


def _stub_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    # Any attribute accessed (incl. `from mod import X`) resolves to a MagicMock,
    # so we don't have to enumerate every function the production code imports.
    mod.__getattr__ = lambda attr: MagicMock()
    sys.modules[name] = mod
    return mod


# Stub database package and submodules to avoid heavy imports / ADC lookups.
database_mod = _stub_module("database")
database_mod.__path__ = []
for submodule in [
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
    mod = _stub_module(f"database.{submodule}")
    setattr(database_mod, submodule, mod)

vector_db_mod = sys.modules["database.vector_db"]
for attr in [
    "find_similar_memories",
    "upsert_memory_vector",
    "delete_memory_vector",
    "upsert_vector2",
    "update_vector_metadata",
    "upsert_action_item_vectors_batch",
    "delete_action_item_vectors_batch",
]:
    setattr(vector_db_mod, attr, MagicMock())

apps_mod = sys.modules["database.apps"]
for attr in ["record_app_usage", "get_omi_personas_by_uid_db", "get_app_by_id_db"]:
    setattr(apps_mod, attr, MagicMock())

llm_usage_mod = sys.modules["database.llm_usage"]
llm_usage_mod.record_llm_usage = MagicMock()

users_mod = sys.modules["database.users"]
for attr in ["get_user_language_preference", "get_people_by_ids"]:
    setattr(users_mod, attr, MagicMock(return_value=None))

client_mod = sys.modules["database._client"]
client_mod.document_id_from_seed = MagicMock(return_value="doc-id")

# Stub utils modules that pull in external dependencies (OpenAI/GCS/etc.).
for name in [
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
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)

utils_apps = sys.modules["utils.apps"]
for attr in ["get_available_apps", "update_personas_async", "update_persona_prompt"]:
    setattr(utils_apps, attr, MagicMock())

utils_analytics = sys.modules["utils.analytics"]
utils_analytics.record_usage = MagicMock()

llm_memories = sys.modules["utils.llm.memories"]
for attr in ["resolve_memory_conflict", "extract_memories_from_text", "new_memories_extractor"]:
    setattr(llm_memories, attr, MagicMock())

llm_conv = sys.modules["utils.llm.conversation_processing"]
for attr in [
    "get_transcript_structure",
    "get_app_result",
    "should_discard_conversation",
    "select_best_app_for_conversation",
    "get_suggested_apps_for_conversation",
    "get_reprocess_transcript_structure",
    "assign_conversation_to_folder",
    "extract_action_items",
]:
    setattr(llm_conv, attr, MagicMock())

llm_external = sys.modules["utils.llm.external_integrations"]
for attr in ["summarize_experience_text", "get_message_structure"]:
    setattr(llm_external, attr, MagicMock())

llm_trends = sys.modules["utils.llm.trends"]
llm_trends.trends_extractor = MagicMock()

llm_goals = sys.modules["utils.llm.goals"]
llm_goals.extract_and_update_goal_progress = MagicMock()

llm_chat = sys.modules["utils.llm.chat"]
for attr in [
    "retrieve_metadata_from_text",
    "retrieve_metadata_from_message",
    "retrieve_metadata_fields_from_transcript",
    "obtain_emotional_message",
]:
    setattr(llm_chat, attr, MagicMock())

llm_clients = sys.modules["utils.llm.clients"]
llm_clients.generate_embedding = MagicMock()

utils_notifications = sys.modules["utils.notifications"]
for attr in ["send_notification", "send_important_conversation_message", "send_action_item_data_message"]:
    setattr(utils_notifications, attr, MagicMock())

utils_hume = sys.modules["utils.other.hume"]
for attr in ["get_hume", "HumeJobCallbackModel", "HumeJobModelPredictionResponseModel"]:
    setattr(utils_hume, attr, MagicMock())

utils_rag = sys.modules["utils.retrieval.rag"]
utils_rag.retrieve_rag_conversation_context = MagicMock()

utils_webhooks = sys.modules["utils.webhooks"]
utils_webhooks.conversation_created_webhook = MagicMock()

utils_task_sync = sys.modules["utils.task_sync"]
utils_task_sync.auto_sync_action_items_batch = MagicMock()

utils_storage = sys.modules["utils.other.storage"]
utils_storage.precache_conversation_audio = MagicMock()

import importlib  # noqa: E402

from models.memories import MemorySource  # noqa: E402
from models.conversation_enums import ConversationSource  # noqa: E402

process_conversation = importlib.import_module("utils.conversations.process_conversation")
derive_memory_source = process_conversation.derive_memory_source
WHATSAPP_GATEWAY_APP_ID = process_conversation.WHATSAPP_GATEWAY_APP_ID


def _conv(source, app_id=None):
    return SimpleNamespace(source=source, app_id=app_id)


def test_whatsapp_gateway_maps_to_whatsapp():
    c = _conv(ConversationSource.external_integration, WHATSAPP_GATEWAY_APP_ID)
    assert derive_memory_source(c) == MemorySource.whatsapp


def test_external_integration_other_app_maps_to_other():
    c = _conv(ConversationSource.external_integration, "some_other_app_id")
    assert derive_memory_source(c) == MemorySource.other


def test_plaud_maps_to_plaud():
    assert derive_memory_source(_conv(ConversationSource.plaud)) == MemorySource.plaud


def test_omi_maps_to_recording():
    assert derive_memory_source(_conv(ConversationSource.omi)) == MemorySource.recording


def test_unknown_source_maps_to_other():
    assert derive_memory_source(_conv(ConversationSource.workflow)) == MemorySource.other
