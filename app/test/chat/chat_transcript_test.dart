import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/chat/chat_transcript.dart';
import 'package:voxos/src/theme/voxos_theme.dart';

void main() {
  testWidgets('shows configured activity while the assistant is waiting', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: VoxosTheme.light(),
        home: Scaffold(
          body: ChatMessageView(
            loadAttachment: (_, {required thumbnail}) async => Uint8List(0),
            message: const ChatViewMessage(
              activity: ChatActivityPresentation(
                detail: 'Exa',
                icon: ChatActivityIcon.web,
                label: 'Searching the web',
                logo: ChatActivityLogo(domain: 'exa.ai', label: 'Exa'),
              ),
              id: 'assistant-1',
              isStreaming: true,
              role: 'assistant',
              text: '',
            ),
            onOpenAttachment: (_) async {},
          ),
        ),
      ),
    );

    expect(find.text('Searching the web'), findsOneWidget);
    expect(find.text('Exa'), findsOneWidget);
    expect(find.byIcon(Icons.public_rounded), findsOneWidget);
    expect(find.text('E'), findsNothing);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('keeps tool activity visible after a streamed preamble', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: VoxosTheme.light(),
        home: Scaffold(
          body: ChatMessageView(
            loadAttachment: (_, {required thumbnail}) async => Uint8List(0),
            message: const ChatViewMessage(
              activity: ChatActivityPresentation(
                detail: 'Local tools',
                icon: ChatActivityIcon.mcp,
                label: 'Using local data',
              ),
              id: 'assistant-2',
              isStreaming: true,
              role: 'assistant',
              text: 'I’ll check that now.',
            ),
            onOpenAttachment: (_) async {},
          ),
        ),
      ),
    );

    expect(find.text('I’ll check that now.'), findsOneWidget);
    expect(find.text('Using local data'), findsOneWidget);
    expect(find.byIcon(Icons.hub_outlined), findsOneWidget);
  });
}
