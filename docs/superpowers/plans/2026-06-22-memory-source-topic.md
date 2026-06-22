# Memory Source + Topic Markers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two independent provenance/subject markers to extracted memories — `source` (whatsapp/recording/plaud/email/manual) and `topic` (the conversation's `CategoryEnum`) — persisted on the memory, filterable via API, and shown as chips in the Flutter app.

**Architecture:** Duplicate `source` + `topic` onto `MemoryDB` at extraction time, derived from the parent conversation (data already in hand in `_extract_memories_inner`). No Firestore index, no backfill, no client-side conversation lookup. App model gains two nullable fields; the memory list widget renders chips.

**Tech Stack:** Python 3.11 / FastAPI / Pydantic v1-style (`.dict()`, `@validator`) for backend; Flutter/Dart (hand-written JSON model, no codegen) for app.

## Global Constraints

- Backend: Python 3.11 only; **no in-function imports** (all imports at module top level); import hierarchy `database/ → utils/ → routers/ → main.py`.
- Backend formatting: `black --line-length 120 --skip-string-normalization`.
- New backend test files MUST be added to `backend/test.sh` or CI won't run them.
- New fields are `Optional`/default `None` — must not break existing memories or existing `from_memory()` call sites.
- No backfill of old memories (decision: only forward).
- No new Firestore index (filter is in-memory).
- WhatsApp Gateway app_id (verbatim): `01KTHGQSYBNTM6C8GAVKJS9N6X`.
- Dart memory model (`app/lib/backend/schema/memory.dart`) is hand-written — `fromJson`/`toJson` are manual, **no build_runner**. New fields parse defensively (missing JSON → `null`).
- App user-facing strings use `context.l10n.keyName` (l10n). Chip labels follow this rule.
- Branch: `feat/airec-device`. Commit locally only (do not push/PR unless asked).

---

### Task 1: `MemorySource` enum + `source`/`topic` fields on `MemoryDB`

**Files:**
- Modify: `backend/models/memories.py` (add enum after line 27; add fields in `MemoryDB` near line 123; extend `from_memory` at lines 142-159)
- Test: `backend/tests/unit/test_memory_source.py` (create)

**Interfaces:**
- Consumes: existing `CategoryEnum` from `models/conversation_enums.py`.
- Produces:
  - `MemorySource(str, Enum)` with members `whatsapp`, `recording`, `plaud`, `email`, `manual`, `other`.
  - `MemoryDB.source: Optional[MemorySource] = None`
  - `MemoryDB.topic: Optional[CategoryEnum] = None`
  - `MemoryDB.from_memory(memory, uid, conversation_id, manually_added, source: Optional[MemorySource] = None, topic: Optional[CategoryEnum] = None) -> MemoryDB`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_memory_source.py`:

```python
from datetime import datetime, timezone

from models.memories import Memory, MemoryDB, MemorySource, MemoryCategory
from models.conversation_enums import CategoryEnum


def _mem():
    return Memory(content="x", category=MemoryCategory.interesting)


def test_from_memory_defaults_source_and_topic_none():
    db = MemoryDB.from_memory(_mem(), "uid", "conv1", False)
    assert db.source is None
    assert db.topic is None


def test_from_memory_sets_source_and_topic():
    db = MemoryDB.from_memory(
        _mem(), "uid", "conv1", False,
        source=MemorySource.whatsapp, topic=CategoryEnum.work,
    )
    assert db.source == MemorySource.whatsapp
    assert db.topic == CategoryEnum.work


def test_memorysource_values():
    assert {s.value for s in MemorySource} == {
        "whatsapp", "recording", "plaud", "email", "manual", "other"
    }


def test_memorydb_serializes_source_topic():
    db = MemoryDB.from_memory(
        _mem(), "uid", "conv1", False,
        source=MemorySource.plaud, topic=CategoryEnum.spiritual,
    )
    d = db.dict()
    assert d["source"] == MemorySource.plaud
    assert d["topic"] == CategoryEnum.spiritual
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_memory_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'MemorySource'`.

- [ ] **Step 3: Add the enum**

In `backend/models/memories.py`, after the `MemoryCategory` enum block (after line 27, before `CATEGORY_BOOSTS`), add:

```python
class MemorySource(str, Enum):
    whatsapp = "whatsapp"
    recording = "recording"  # native Omi recording (ConversationSource.omi)
    plaud = "plaud"
    email = "email"
    manual = "manual"
    other = "other"  # fallback for unmapped origin
```

At the top of the file, extend the existing conversation_enums import (there is none yet — add it under the existing imports, line 8 area):

```python
from models.conversation_enums import CategoryEnum
```

- [ ] **Step 4: Add the fields to `MemoryDB`**

In `backend/models/memories.py`, in class `MemoryDB`, next to `app_id: Optional[str] = None` (line 123), add:

```python
    source: Optional[MemorySource] = None
    topic: Optional[CategoryEnum] = None
```

- [ ] **Step 5: Extend `from_memory`**

Replace the `from_memory` signature and body (lines 142-159) so it accepts and assigns the two new params:

```python
    @staticmethod
    def from_memory(
        memory: Memory,
        uid: str,
        conversation_id: str,
        manually_added: bool,
        source: Optional['MemorySource'] = None,
        topic: Optional[CategoryEnum] = None,
    ) -> 'MemoryDB':
        memory_db = MemoryDB(
            id=document_id_from_seed(memory.content),
            uid=uid,
            content=memory.content,
            category=memory.category,
            tags=memory.tags,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            conversation_id=conversation_id,
            manually_added=manually_added,
            user_review=True if manually_added else None,
            reviewed=True,
            visibility=memory.visibility,
            source=source,
            topic=topic,
        )
        memory_db.scoring = MemoryDB.calculate_score(memory_db)
        return memory_db
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_memory_source.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Register the test file in CI**

In `backend/test.sh`, add `tests/unit/test_memory_source.py` to the pytest invocation list (match the existing pattern for unit test files).

- [ ] **Step 8: Format and commit**

```bash
cd backend && black --line-length 120 --skip-string-normalization models/memories.py tests/unit/test_memory_source.py
git add backend/models/memories.py backend/tests/unit/test_memory_source.py backend/test.sh
git commit -m "feat(airec): MemorySource enum + source/topic on MemoryDB"
```

---

### Task 2: Derive `source`/`topic` from the parent conversation in extraction

**Files:**
- Modify: `backend/utils/conversations/process_conversation.py` (top imports near lines 35-37; new helper function; call site at line 503)
- Test: `backend/tests/unit/test_derive_memory_source.py` (create)

**Interfaces:**
- Consumes: `MemorySource` (Task 1), `ConversationSource`, `Conversation`.
- Produces: `derive_memory_source(conversation) -> MemorySource` (module-level function in `process_conversation.py`); module constant `WHATSAPP_GATEWAY_APP_ID = "01KTHGQSYBNTM6C8GAVKJS9N6X"`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_derive_memory_source.py`:

```python
from types import SimpleNamespace

from models.memories import MemorySource
from models.conversation_enums import ConversationSource
from utils.conversations.process_conversation import (
    derive_memory_source,
    WHATSAPP_GATEWAY_APP_ID,
)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_derive_memory_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'derive_memory_source'`.

- [ ] **Step 3: Add imports at top of `process_conversation.py`**

Update the `from models.memories import ...` line (line 35) and add the enums import. After line 35 (`from models.memories import MemoryDB, Memory`), change it to include `MemorySource`, and add the conversation_enums import:

```python
from models.memories import MemoryDB, Memory, MemorySource
from models.conversation_enums import ConversationSource, CategoryEnum
```

(If `ConversationSource`/`CategoryEnum` are already imported via the `from models.conversation import (...)` block at line 37, do not duplicate — import them only once. Verify with `grep -n "ConversationSource\|CategoryEnum" backend/utils/conversations/process_conversation.py` first; import only the names not already present.)

- [ ] **Step 4: Add the constant and helper**

Near the top of `process_conversation.py`, after the imports (module level), add:

```python
WHATSAPP_GATEWAY_APP_ID = "01KTHGQSYBNTM6C8GAVKJS9N6X"


def derive_memory_source(conversation) -> MemorySource:
    src = getattr(conversation, 'source', None)
    if src == ConversationSource.external_integration and getattr(conversation, 'app_id', None) == WHATSAPP_GATEWAY_APP_ID:
        return MemorySource.whatsapp
    if src == ConversationSource.plaud:
        return MemorySource.plaud
    if src == ConversationSource.omi:
        return MemorySource.recording
    return MemorySource.other
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_derive_memory_source.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Wire derivation into the extraction call site**

In `backend/utils/conversations/process_conversation.py`, replace line 503:

```python
        memory_db_obj = MemoryDB.from_memory(memory, uid, conversation.id, False)
```

with:

```python
        mem_topic = conversation.structured.category if conversation.structured else None
        memory_db_obj = MemoryDB.from_memory(
            memory, uid, conversation.id, False,
            source=derive_memory_source(conversation),
            topic=mem_topic,
        )
```

- [ ] **Step 7: Run the full unit suite to confirm no regression**

Run: `cd backend && python -m pytest tests/unit/test_memory_source.py tests/unit/test_derive_memory_source.py -v`
Expected: PASS (all).

- [ ] **Step 8: Register test file and commit**

Add `tests/unit/test_derive_memory_source.py` to `backend/test.sh`.

```bash
cd backend && black --line-length 120 --skip-string-normalization utils/conversations/process_conversation.py tests/unit/test_derive_memory_source.py
git add backend/utils/conversations/process_conversation.py backend/tests/unit/test_derive_memory_source.py backend/test.sh
git commit -m "feat(airec): derive memory source/topic from parent conversation"
```

---

### Task 3: Set `source=manual` on the manual create-memory path

**Files:**
- Modify: `backend/routers/memories.py` (POST `/v3/memories` at lines 52-58; batch create at lines 105-111)
- Test: `backend/tests/unit/test_memory_source.py` (extend — append cases)

**Interfaces:**
- Consumes: `MemoryDB.from_memory(..., source=MemorySource.manual)` (Task 1).
- Produces: manual memories carry `source == MemorySource.manual`.

- [ ] **Step 1: Add failing test cases**

Append to `backend/tests/unit/test_memory_source.py`:

```python
def test_manual_memory_source_is_manual():
    db = MemoryDB.from_memory(_mem(), "uid", None, True, source=MemorySource.manual)
    assert db.source == MemorySource.manual
```

(This documents the contract; the router change below uses the same call.)

- [ ] **Step 2: Run to verify it passes already** (this case only exercises Task 1 code)

Run: `cd backend && python -m pytest tests/unit/test_memory_source.py::test_manual_memory_source_is_manual -v`
Expected: PASS.

- [ ] **Step 3: Update the single-create router**

In `backend/routers/memories.py`, line 57-58, change:

```python
    memory.category = MemoryCategory.manual
    memory_db = MemoryDB.from_memory(memory, uid, None, True)
```

to:

```python
    memory.category = MemoryCategory.manual
    memory_db = MemoryDB.from_memory(memory, uid, None, True, source=MemorySource.manual)
```

Add `MemorySource` to the existing import on line 16:

```python
from models.memories import MemoryDB, Memory, MemoryCategory, MemorySource
```

- [ ] **Step 4: Update the batch-create router**

In `backend/routers/memories.py`, around lines 110-111, change:

```python
        memory.category = MemoryCategory.manual
        memory_db = MemoryDB.from_memory(memory, uid, None, True)
```

to:

```python
        memory.category = MemoryCategory.manual
        memory_db = MemoryDB.from_memory(memory, uid, None, True, source=MemorySource.manual)
```

- [ ] **Step 5: Run the memory_source suite**

Run: `cd backend && python -m pytest tests/unit/test_memory_source.py -v`
Expected: PASS.

- [ ] **Step 6: Format and commit**

```bash
cd backend && black --line-length 120 --skip-string-normalization routers/memories.py tests/unit/test_memory_source.py
git add backend/routers/memories.py backend/tests/unit/test_memory_source.py
git commit -m "feat(airec): tag manually-created memories with source=manual"
```

---

### Task 4: `GET /v3/memories?source=` in-memory filter

**Files:**
- Modify: `backend/routers/memories.py` (`get_memories` at lines 141-163)
- Test: `backend/tests/unit/test_memories_source_filter.py` (create)

**Interfaces:**
- Consumes: `MemoryDB.source` (Task 1).
- Produces: `get_memories(limit, offset, source: Optional[str] = None, uid)` filtering the returned list by `m.source.value == source` when `source` is provided.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_memories_source_filter.py`. This test patches the DB layer and calls the route function directly:

```python
from datetime import datetime, timezone
from unittest.mock import patch

import backend.routers.memories as memories_router  # adjust if import path differs
from models.memories import MemorySource


def _doc(mid, source):
    return {
        "id": mid, "uid": "u", "content": f"c{mid}",
        "category": "interesting",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "source": source,
    }


def test_filter_by_source_whatsapp():
    docs = [_doc("1", "whatsapp"), _doc("2", "manual"), _doc("3", "whatsapp")]
    with patch.object(memories_router.memories_db, "get_memories", return_value=docs):
        result = memories_router.get_memories(limit=100, offset=1, source="whatsapp", uid="u")
    assert {m.id for m in result} == {"1", "3"}


def test_no_filter_returns_all():
    docs = [_doc("1", "whatsapp"), _doc("2", "manual")]
    with patch.object(memories_router.memories_db, "get_memories", return_value=docs):
        result = memories_router.get_memories(limit=100, offset=1, source=None, uid="u")
    assert {m.id for m in result} == {"1", "2"}
```

> Note on import path: the module is imported in the existing suite as `routers.memories` (run from `backend/`). Use the same style the other tests in `backend/tests/unit/` use — check an existing test's import line and match it. If they use `import routers.memories`, use that instead of `import backend.routers.memories`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_memories_source_filter.py -v`
Expected: FAIL — `get_memories()` got an unexpected keyword argument `source`.

- [ ] **Step 3: Add the `source` param and filter**

In `backend/routers/memories.py`, change the `get_memories` signature (line 142):

```python
@router.get('/v3/memories', tags=['memories'], response_model=List[MemoryDB])
def get_memories(
    limit: int = 100,
    offset: int = 0,
    source: Optional[str] = None,
    uid: str = Depends(auth.get_current_user_uid),
):
```

After the `valid_memories` list is fully built (after the `for` loop, before `return valid_memories`, ~line 162), add:

```python
    if source:
        valid_memories = [m for m in valid_memories if m.source and m.source.value == source]
```

Ensure `Optional` is imported at the top of the file (add `from typing import Optional, List` if not already present — check first).

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_memories_source_filter.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Register and commit**

Add `tests/unit/test_memories_source_filter.py` to `backend/test.sh`.

```bash
cd backend && black --line-length 120 --skip-string-normalization routers/memories.py tests/unit/test_memories_source_filter.py
git add backend/routers/memories.py backend/tests/unit/test_memories_source_filter.py backend/test.sh
git commit -m "feat(airec): filter GET /v3/memories by source"
```

---

### Task 5: Dart `Memory` model — parse/serialize `source` + `topic`

**Files:**
- Modify: `app/lib/backend/schema/memory.dart` (fields, constructor, `fromJson`, `toJson`)
- Test: `app/test/unit/memory_source_test.dart` (create)

**Interfaces:**
- Produces: `Memory.source` (`String?`), `Memory.topic` (`String?`); parsed from JSON keys `source`/`topic`; `null` when absent.

- [ ] **Step 1: Write the failing test**

Create `app/test/unit/memory_source_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:omi/backend/schema/memory.dart';

Map<String, dynamic> base() => {
      'id': 'm1',
      'uid': 'u',
      'content': 'hello',
      'category': 'interesting',
      'created_at': '2026-06-22T10:00:00Z',
      'updated_at': '2026-06-22T10:00:00Z',
    };

void main() {
  test('parses source and topic when present', () {
    final json = base()..addAll({'source': 'whatsapp', 'topic': 'work'});
    final m = Memory.fromJson(json);
    expect(m.source, 'whatsapp');
    expect(m.topic, 'work');
  });

  test('source and topic null when absent (old memories)', () {
    final m = Memory.fromJson(base());
    expect(m.source, isNull);
    expect(m.topic, isNull);
  });

  test('toJson includes source and topic', () {
    final m = Memory.fromJson(base()..addAll({'source': 'plaud', 'topic': 'spiritual'}));
    final out = m.toJson();
    expect(out['source'], 'plaud');
    expect(out['topic'], 'spiritual');
  });
}
```

> Confirm the package import prefix: check an existing test in `app/test/` for `package:omi/...` vs `package:friend/...` and match it.

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && flutter test test/unit/memory_source_test.dart`
Expected: FAIL — `source`/`topic` not defined on `Memory`.

- [ ] **Step 3: Add fields + constructor params**

In `app/lib/backend/schema/memory.dart`, in class `Memory`, after `bool isLocked;` (line 33) add:

```dart
  String? source;
  String? topic;
```

In the constructor (after `this.isLocked = false,`, line 49) add:

```dart
    this.source,
    this.topic,
```

- [ ] **Step 4: Parse in `fromJson` and emit in `toJson`**

In `fromJson` (before the closing `);` at line 70), add:

```dart
      source: json['source'],
      topic: json['topic'],
```

In `toJson` (before the closing `};` at line 90), add:

```dart
      'source': source,
      'topic': topic,
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd app && flutter test test/unit/memory_source_test.dart`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd app && dart format --line-length 120 lib/backend/schema/memory.dart test/unit/memory_source_test.dart
git add app/lib/backend/schema/memory.dart app/test/unit/memory_source_test.dart
git commit -m "feat(app): parse memory source/topic markers"
```

---

### Task 6: Source/topic chip helper (labels + icons)

**Files:**
- Create: `app/lib/pages/memories/widgets/memory_source_chip.dart`
- Test: `app/test/widgets/memory_source_chip_test.dart` (create)

**Interfaces:**
- Produces:
  - `String? memorySourceLabel(BuildContext context, String? source)` — returns a localized label (e.g. "WhatsApp", "Recording", "Plaud", "Email", "Manual") for known sources, `null` for `other`/`null`/unknown.
  - `IconData? memorySourceIcon(String? source)` — an icon for known sources, `null` otherwise.
  - `Widget buildMemorySourceChip(BuildContext context, String? source)` — returns a small chip widget, or `SizedBox.shrink()` when there is nothing to show.

- [ ] **Step 1: Write the failing test**

Create `app/test/widgets/memory_source_chip_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omi/pages/memories/widgets/memory_source_chip.dart';

void main() {
  testWidgets('shows chip with label for whatsapp', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(builder: (ctx) => buildMemorySourceChip(ctx, 'whatsapp')),
      ),
    ));
    expect(find.text('WhatsApp'), findsOneWidget);
  });

  testWidgets('renders nothing for other/null', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(builder: (ctx) => buildMemorySourceChip(ctx, 'other')),
      ),
    ));
    expect(find.byType(SizedBox), findsOneWidget);
    expect(find.textContaining(RegExp(r'\w')), findsNothing);
  });
}
```

> If the codebase wires l10n in tests via a localizations delegate, follow the pattern in an existing widget test. To keep this task self-contained, the helper falls back to a hard-coded English label when `context.l10n` is unavailable (see Step 3) so the widget test passes without delegate setup. The l10n keys are added in Step 4.

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && flutter test test/widgets/memory_source_chip_test.dart`
Expected: FAIL — file/target not found.

- [ ] **Step 3: Implement the helper**

Create `app/lib/pages/memories/widgets/memory_source_chip.dart`:

```dart
import 'package:flutter/material.dart';

const Map<String, String> _sourceLabels = {
  'whatsapp': 'WhatsApp',
  'recording': 'Recording',
  'plaud': 'Plaud',
  'email': 'Email',
  'manual': 'Manual',
};

const Map<String, IconData> _sourceIcons = {
  'whatsapp': Icons.chat_bubble_outline,
  'recording': Icons.mic_none,
  'plaud': Icons.memory,
  'email': Icons.mail_outline,
  'manual': Icons.edit_outlined,
};

String? memorySourceLabel(BuildContext context, String? source) {
  if (source == null) return null;
  return _sourceLabels[source];
}

IconData? memorySourceIcon(String? source) {
  if (source == null) return null;
  return _sourceIcons[source];
}

Widget buildMemorySourceChip(BuildContext context, String? source) {
  final label = memorySourceLabel(context, source);
  if (label == null) return const SizedBox.shrink();
  final icon = memorySourceIcon(source);
  return Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: BoxDecoration(
      color: Colors.white.withOpacity(0.08),
      borderRadius: BorderRadius.circular(8),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (icon != null) ...[
          Icon(icon, size: 12, color: Colors.white70),
          const SizedBox(width: 4),
        ],
        Text(label, style: const TextStyle(fontSize: 11, color: Colors.white70)),
      ],
    ),
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd app && flutter test test/widgets/memory_source_chip_test.dart`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd app && dart format --line-length 120 lib/pages/memories/widgets/memory_source_chip.dart test/widgets/memory_source_chip_test.dart
git add app/lib/pages/memories/widgets/memory_source_chip.dart app/test/widgets/memory_source_chip_test.dart
git commit -m "feat(app): memory source chip helper"
```

---

### Task 7: Render the chip in the memory list item

**Files:**
- Modify: `app/lib/pages/memories/widgets/memory_item.dart` (after the content row, ~after line 72)

**Interfaces:**
- Consumes: `buildMemorySourceChip(context, source)` (Task 6), `Memory.source` (Task 5).

- [ ] **Step 1: Import the helper**

At the top of `app/lib/pages/memories/widgets/memory_item.dart`, add:

```dart
import 'package:omi/pages/memories/widgets/memory_source_chip.dart';
```

(Match the existing import prefix used in this file.)

- [ ] **Step 2: Render the chip under the memory content**

Locate the column/children where the memory `content` text and the conversation-link icon are rendered (around lines 62-72). After the content text widget, insert the chip in the same Column (guarded so it only takes space when present):

```dart
              buildMemorySourceChip(context, memory.source),
```

If the layout places content in a `Row` rather than a `Column`, wrap content + chip in a `Column(crossAxisAlignment: CrossAxisAlignment.start, children: [...])` so the chip sits below the text. Read the surrounding 30 lines first and match the existing widget tree; the chip returns `SizedBox.shrink()` when there's nothing to show, so it's safe to always include.

- [ ] **Step 3: Verify the app builds and the widget renders**

Run: `cd app && flutter analyze lib/pages/memories/widgets/memory_item.dart`
Expected: no errors (warnings about pre-existing issues are acceptable).

Then run the existing memory widget tests if any:
Run: `cd app && flutter test test/widgets/`
Expected: PASS (no regressions).

- [ ] **Step 4: Verify programmatically (agent-flutter)**

Per root CLAUDE.md "Verifying UI Changes": with the app running, hot restart and snapshot the memories screen, confirming a memory ingested from WhatsApp shows the chip.

```bash
kill -SIGUSR2 $(pgrep -f "flutter run" | head -1)
AGENT_FLUTTER_LOG=/tmp/flutter-run.log agent-flutter connect
# navigate to Memories, then:
agent-flutter screenshot /tmp/memory-source-chip.png
```

Expected: a "WhatsApp" chip visible on at least one memory (requires a WhatsApp-sourced memory to exist; new ones arrive from the daily cron, or POST a test conversation via the integration endpoint).

- [ ] **Step 5: Commit**

```bash
cd app && dart format --line-length 120 lib/pages/memories/widgets/memory_item.dart
git add app/lib/pages/memories/widgets/memory_item.dart
git commit -m "feat(app): show source chip on memory list items"
```

---

### Task 8: Full suites + deploy notes

**Files:** none (verification + deploy).

- [ ] **Step 1: Backend full suite**

Run: `cd backend && bash test-preflight.sh && bash test.sh`
Expected: PASS, including the three new unit test files.

- [ ] **Step 2: App full suite**

Run: `cd app && bash test.sh`
Expected: PASS, including the two new test files.

- [ ] **Step 3: Deploy backend to Karla (when ready)**

Per the area state and root CLAUDE.md: code is baked into the image (not volume-mounted). On Karla, in `/opt/omi-airec/omi`:

```bash
git pull            # branch feat/airec-device
docker build -f Dockerfile.patched -t omi-backend:local omi/
docker compose up -d --force-recreate backend
```

Backend healthy in ~35s (HTTP 200 at `/docs`). No Firestore index change, no backfill.

- [ ] **Step 4: Post-deploy verification**

- Trigger a fresh WhatsApp ingestion (or wait for the 06:00 cron), then `GET /v3/memories?source=whatsapp` via API using `Bearer <ADMIN_KEY><uid>` and confirm it returns WhatsApp-sourced memories.
- In the app, open Memories and confirm the "WhatsApp" chip appears on a newly-ingested memory.

---

## Self-Review

**Spec coverage:**
- Model `MemorySource` + `source`/`topic` on `MemoryDB` → Task 1. ✅
- `from_memory()` optional params, non-breaking → Task 1. ✅
- Derivation function + `WHATSAPP_GATEWAY_APP_ID` + wired into extraction → Task 2. ✅
- `topic` herdado de `structured.category` → Task 2, Step 6. ✅
- Manual path sets `source=manual` → Task 3. ✅
- `GET /v3/memories?source=` in-memory filter, no index → Task 4. ✅
- Dart model fields, defensive parse → Task 5. ✅
- App chip (label/icon, reuses conversation chip style) → Tasks 6-7. ✅
- No backfill / no new index → honored (Tasks state it; Task 8 deploy notes). ✅
- Tests added to `test.sh` → Tasks 1, 2, 4. ✅

**Placeholder scan:** No TBD/TODO. Every code step shows the code. Import-path and l10n caveats are explicit instructions to verify-and-match, not vague gaps.

**Type consistency:** `MemorySource`/`CategoryEnum` names consistent across Tasks 1-4. `from_memory(..., source=, topic=)` signature matches all call sites (Tasks 1, 2, 3). Dart `source`/`topic` (`String?`) consistent across Tasks 5-7. `buildMemorySourceChip(context, source)` signature consistent between Task 6 (def) and Task 7 (use).

**Known judgment calls left to the implementer (intentional, not gaps):**
- Exact import prefix (`package:omi` vs `package:friend`) — instructed to match existing files.
- l10n: chip labels are hard-coded English in Task 6 to keep the task self-contained; a follow-up could route them through `context.l10n` once keys exist. This is a deliberate YAGNI deferral, noted, not silent.
