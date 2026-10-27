import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:voxos/src/api/voxos_api_client.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/theme/voxos_theme.dart';
import 'package:voxos/src/voice/voice_call_page.dart';
import 'package:voxos/src/voice/voice_controller.dart';

import '../support/config_fixtures.dart';
import '../support/fakes.dart';

void main() {
  testWidgets('opening the call page connects and opens the microphone', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final transports = <FakeVoiceTransport>[];
    final controller = _controller(auth: auth, transports: transports);

    await tester.pumpWidget(
      MaterialApp(
        theme: VoxosTheme.light(),
        home: VoiceCallPage(controller: controller),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(transports, hasLength(1));
    expect(transports.single.microphoneEnabled, isTrue);
    expect(find.text('Listening…'), findsOneWidget);
    expect(find.byKey(const ValueKey<String>('mute-control')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey<String>('mute-control')));
    await tester.pump();

    expect(transports.single.microphoneEnabled, isFalse);
    expect(find.text('Microphone muted'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    controller.dispose();
    await auth.dispose();
    await transports.single.closeEvents();
  });

  testWidgets('ending the call disconnects and leaves the page', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final transports = <FakeVoiceTransport>[];
    final controller = _controller(auth: auth, transports: transports);

    await tester.pumpWidget(
      MaterialApp(
        theme: VoxosTheme.light(),
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () =>
                  Navigator.of(context).push(VoiceCallPage.route(controller)),
              child: const Text('Open voice'),
            ),
          ),
        ),
      ),
    );
    // The orb animates forever, so the call page is pumped by hand.
    await tester.tap(find.text('Open voice'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pump(const Duration(milliseconds: 50));

    expect(transports, hasLength(1));

    await tester.tap(find.byKey(const ValueKey<String>('end-call-control')));
    // Cancelling the transport subscription needs the real event loop.
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 10)),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));

    expect(transports.single.disposeCount, 1);
    expect(controller.phase, VoicePhase.ready);
    expect(find.text('Open voice'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    controller.dispose();
    await auth.dispose();
    await transports.single.closeEvents();
  });

  testWidgets('a server without voice explains itself instead of dialling', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final transports = <FakeVoiceTransport>[];
    final controller = _controller(
      auth: auth,
      transports: transports,
      voiceReady: false,
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: VoxosTheme.dark(),
        home: VoiceCallPage(controller: controller),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(transports, isEmpty);
    expect(find.text('Voice needs setup'), findsOneWidget);
    expect(
      find.byKey(const ValueKey<String>('restart-voice-call')),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    controller.dispose();
    await auth.dispose();
  });
}

VoiceController _controller({
  required FakeAuthSession auth,
  required List<FakeVoiceTransport> transports,
  bool voiceReady = true,
}) {
  final connection = ServerConnection(
    baseUri: Uri.parse('https://voxos.example.com'),
    config: ServerConfig.fromJson(clientConfigJson()),
  );
  return VoiceController(
    api: VoxosApiClient(
      auth: auth,
      connection: connection,
      client: _voiceSettingsClient(voiceReady: voiceReady),
    ),
    microphoneAccess: FakeMicrophoneAccess(),
    participantName: 'Josh',
    wakeLock: FakeVoiceWakeLock(),
    transportFactory: () {
      final transport = FakeVoiceTransport();
      transports.add(transport);
      return transport;
    },
  );
}

MockClient _voiceSettingsClient({required bool voiceReady}) {
  return MockClient((request) async {
    if (request.url.path == '/api/voice/provider-settings') {
      return http.Response(
        jsonEncode(<String, Object?>{
          'active_provider': 'openai_realtime',
          'active_settings': <String, Object?>{'configured': voiceReady},
          'providers': <Object?>[
            <String, Object?>{
              'provider': 'openai_realtime',
              'runtime_supported': true,
            },
          ],
        }),
        200,
      );
    }
    if (request.url.path == '/api/livekit/settings') {
      return http.Response(
        jsonEncode(<String, Object?>{'configured': true}),
        200,
      );
    }
    return http.Response('{}', 404);
  });
}
