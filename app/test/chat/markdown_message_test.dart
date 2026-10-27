import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/chat/markdown_message.dart';

void main() {
  testWidgets('opens inline HTTP links with the external launcher', (
    tester,
  ) async {
    final openedUris = <Uri>[];

    await tester.pumpWidget(
      _app(
        MarkdownMessage(
          foreground: Colors.black,
          launchExternalLink: (uri) async {
            openedUris.add(uri);
            return true;
          },
          showSources: false,
          text: '[OpenAI](https://openai.com/research)',
        ),
      ),
    );

    await tester.tap(find.text('OpenAI'));
    await tester.pump();

    expect(openedUris, <Uri>[Uri.parse('https://openai.com/research')]);
    expect(find.text('SOURCES'), findsNothing);
  });

  testWidgets('ignores non-web and credential-bearing links', (tester) async {
    final openedUris = <Uri>[];

    await tester.pumpWidget(
      _app(
        MarkdownMessage(
          foreground: Colors.black,
          launchExternalLink: (uri) async {
            openedUris.add(uri);
            return true;
          },
          showSources: false,
          text: [
            '[File](file:///tmp/report.txt)',
            '[Email](mailto:person@example.com)',
            '[Misleading](https://example.com@attacker.example/path)',
          ].join('\n\n'),
        ),
      ),
    );

    await tester.tap(find.text('File'));
    await tester.tap(find.text('Email'));
    await tester.tap(find.text('Misleading'));
    await tester.pump();

    expect(openedUris, isEmpty);
    expect(find.text('SOURCES'), findsNothing);
  });

  testWidgets('shows three deduplicated source cards and opens a card', (
    tester,
  ) async {
    final openedUris = <Uri>[];

    await tester.pumpWidget(
      _app(
        MarkdownMessage(
          foreground: Colors.black,
          launchExternalLink: (uri) async {
            openedUris.add(uri);
            return true;
          },
          text: [
            '[OpenAI research](https://openai.com/research)',
            '[Duplicate](https://openai.com/research)',
            'https://example.com/news',
            '[MDN](https://developer.mozilla.org/docs)',
            '[Fourth source](https://fourth.example/article)',
          ].join('\n\n'),
        ),
      ),
    );

    expect(find.text('SOURCES'), findsOneWidget);
    expect(
      find.byKey(
        const ValueKey<String>('chat-source-https://openai.com/research'),
      ),
      findsOneWidget,
    );
    expect(
      find.byKey(
        const ValueKey<String>('chat-source-https://fourth.example/article'),
      ),
      findsNothing,
    );

    await tester.tap(
      find.byKey(
        const ValueKey<String>('chat-source-https://openai.com/research'),
      ),
    );
    await tester.pump();

    expect(openedUris, <Uri>[Uri.parse('https://openai.com/research')]);
  });

  testWidgets('shows provider-backed places and opens the exact Maps URL', (
    tester,
  ) async {
    final openedUris = <Uri>[];
    final placeUri = Uri.parse(
      'https://www.google.com/maps/place/?q=place_id:ChIJ123',
    );

    await tester.pumpWidget(
      _app(
        MarkdownMessage(
          foreground: Colors.black,
          launchExternalLink: (uri) async {
            openedUris.add(uri);
            return true;
          },
          placeCards: <ChatPlaceCard>[
            ChatPlaceCard(
              attributionUri: placeUri,
              directionsUri: Uri.parse(
                'https://www.google.com/maps/dir/'
                '?api=1&destination=ChIJ123',
              ),
              latitude: 42.4304,
              longitude: 19.2594,
              placeId: 'ChIJ123',
              placeUri: placeUri,
              position: 0,
              title: 'Konoba Stari Grad',
            ),
          ],
          text: 'Try [Konoba Stari Grad]($placeUri).',
        ),
      ),
    );

    expect(find.text('PLACES'), findsOneWidget);
    expect(find.text('Konoba Stari Grad'), findsWidgets);
    expect(find.text('Google Maps'), findsOneWidget);
    expect(find.text('SOURCES'), findsNothing);

    await tester.tap(find.byKey(const ValueKey<String>('chat-place-ChIJ123')));
    await tester.pump();

    expect(openedUris, <Uri>[placeUri]);
  });

  testWidgets('reports when the browser cannot open a link', (tester) async {
    await tester.pumpWidget(
      _app(
        MarkdownMessage(
          foreground: Colors.black,
          launchExternalLink: (_) async => false,
          showSources: false,
          text: '[OpenAI](https://openai.com)',
        ),
      ),
    );

    await tester.tap(find.text('OpenAI'));
    await tester.pump();

    expect(find.text('Could not open that link.'), findsOneWidget);
  });
}

Widget _app(Widget child) {
  return MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(
        child: Padding(padding: const EdgeInsets.all(16), child: child),
      ),
    ),
  );
}
