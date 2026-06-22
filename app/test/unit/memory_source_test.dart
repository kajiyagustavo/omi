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
