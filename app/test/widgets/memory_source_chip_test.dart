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

  testWidgets('topic chip capitalizes the category label', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(builder: (ctx) => buildMemoryTopicChip(ctx, 'work')),
      ),
    ));
    expect(find.text('Work'), findsOneWidget);
  });

  testWidgets('topic chip renders nothing for null/empty/other', (tester) async {
    for (final t in [null, '', 'other']) {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Builder(builder: (ctx) => buildMemoryTopicChip(ctx, t)),
        ),
      ));
      expect(find.textContaining(RegExp(r'\w')), findsNothing, reason: 'topic=$t');
    }
  });
}
