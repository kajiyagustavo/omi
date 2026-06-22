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
