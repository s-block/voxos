import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:voxos/src/api/voxos_api_client.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/voice/voice_controller.dart';

import '../support/config_fixtures.dart';
import '../support/fakes.dart';

void main() {
  test('a readiness check alone never creates a paid connection', () async {
    final harness = _VoiceHarness();

    await harness.controller.refreshReadiness();

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.transports, isEmpty);
    expect(harness.microphone.requestCount, 0);

    harness.dispose();
  });

  test(
    'opening a call connects, opens the mic, and holds the screen',
    () async {
      final harness = _VoiceHarness();

      await harness.controller.openCall();

      expect(harness.controller.phase, VoicePhase.active);
      expect(harness.controller.isMuted, isFalse);
      expect(harness.transports, hasLength(1));
      expect(harness.transports.single.connectCount, 1);
      expect(harness.transports.single.startWithMicrophone, isTrue);
      expect(harness.transports.single.microphoneEnabled, isTrue);
      expect(harness.microphone.requestCount, 1);
      expect(harness.wakeLock.enabled, isTrue);

      await harness.controller.toggleMute();
      expect(harness.controller.isMuted, isTrue);
      expect(harness.transports.single.microphoneEnabled, isFalse);

      harness.dispose();
    },
  );

  test('the call is never announced active while still muted', () async {
    final harness = _VoiceHarness();
    final mutedWhileActive = <bool>[];
    harness.controller.addListener(() {
      if (harness.controller.phase == VoicePhase.active) {
        mutedWhileActive.add(harness.controller.isMuted);
      }
    });

    await harness.controller.openCall();

    expect(mutedWhileActive, isNotEmpty);
    expect(
      mutedWhileActive,
      everyElement(isFalse),
      reason: 'The caller should never see a muted state they did not ask for.',
    );

    harness.dispose();
  });

  test('captures location once when a voice call starts', () async {
    final location = AgentLocationSnapshot(
      capturedAt: DateTime.utc(2026, 8, 30, 12),
      horizontalAccuracyM: 25,
      latitude: 42.4304,
      longitude: 19.2594,
    );
    final harness = _VoiceHarness(
      locationProvider: _StaticLocationProvider(location),
    );

    await harness.controller.openCall();

    expect(harness.transports.single.currentLocation, same(location));
    harness.dispose();
  });

  test('an initial microphone publish failure leaves no active call', () async {
    final harness = _VoiceHarness(microphonePublishFails: true);

    await harness.controller.openCall();

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.controller.isMuted, isTrue);
    expect(harness.transports.single.microphoneEnabled, isFalse);
    expect(harness.transports.single.disposeCount, 1);
    expect(harness.controller.message, contains('could not be published'));

    harness.dispose();
  });

  test('ending a call disconnects and gives every call a fresh room', () async {
    final harness = _VoiceHarness();
    await harness.controller.openCall();

    await harness.controller.endCall();

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.controller.isMuted, isTrue);
    expect(harness.transports.single.disposeCount, 1);
    expect(harness.wakeLock.enabled, isFalse);

    await harness.controller.openCall();

    expect(harness.controller.phase, VoicePhase.active);
    expect(harness.transports, hasLength(2));
    expect(
      harness.transports[1].roomName,
      isNot(harness.transports[0].roomName),
      reason: 'Each call starts its own conversation.',
    );

    harness.dispose();
  });

  test('background stop disconnects and never auto-resumes', () async {
    final harness = _VoiceHarness();
    await harness.controller.openCall();

    await harness.controller.stopForLifecycle();

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.transports.single.disposeCount, 1);
    expect(harness.transports, hasLength(1));
    expect(harness.wakeLock.enabled, isFalse);
    expect(
      harness.controller.message,
      'The call ended when the app left the foreground.',
    );

    harness.dispose();
  });

  test('shutdown disconnects an active transport before navigation', () async {
    final harness = _VoiceHarness();
    await harness.controller.openCall();

    await harness.controller.shutdown();

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.transports.single.disposeCount, 1);
    expect(harness.wakeLock.enabled, isFalse);

    harness.dispose();
  });

  test('transport disconnect releases the display wake lock', () async {
    final harness = _VoiceHarness();
    await harness.controller.openCall();

    harness.transports.single.emitDisconnected();
    await pumpEventQueue();

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.wakeLock.enabled, isFalse);

    harness.dispose();
  });

  test('permission denial prevents any paid connection', () async {
    final harness = _VoiceHarness(microphoneGranted: false);

    await harness.controller.openCall();

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.transports, isEmpty);
    expect(harness.controller.message, contains('Microphone access'));
    expect(harness.wakeLock.enabled, isFalse);

    harness.dispose();
  });

  test('backgrounding while permission is pending cancels the call', () async {
    final permission = Completer<bool>();
    final harness = _VoiceHarness(microphoneRequest: permission);

    final call = harness.controller.openCall();
    await pumpEventQueue();
    expect(harness.controller.phase, VoicePhase.connecting);
    expect(harness.wakeLock.enabled, isTrue);

    await harness.controller.stopForLifecycle();
    permission.complete(true);
    await call;

    expect(harness.controller.phase, VoicePhase.ready);
    expect(harness.transports, isEmpty);
    expect(harness.wakeLock.enabled, isFalse);
    expect(
      harness.controller.message,
      'The call ended when the app left the foreground.',
    );

    harness.dispose();
  });

  test('an unavailable server can never be dialled', () async {
    final harness = _VoiceHarness(voiceReady: false);

    await harness.controller.openCall();

    expect(harness.controller.phase, VoicePhase.unavailable);
    expect(harness.transports, isEmpty);
    expect(harness.microphone.requestCount, 0);

    harness.dispose();
  });
}

final class _VoiceHarness {
  _VoiceHarness({
    bool microphoneGranted = true,
    bool microphonePublishFails = false,
    Completer<bool>? microphoneRequest,
    CurrentLocationProvider? locationProvider,
    bool voiceReady = true,
  }) : _microphonePublishFails = microphonePublishFails,
       auth = FakeAuthSession(),
       microphone = FakeMicrophoneAccess(
         granted: microphoneGranted,
         pendingRequest: microphoneRequest,
       ),
       wakeLock = FakeVoiceWakeLock() {
    api = VoxosApiClient(
      auth: auth,
      connection: ServerConnection(
        baseUri: Uri.parse('https://voxos.example.com'),
        config: ServerConfig.fromJson(clientConfigJson()),
      ),
      client: MockClient((request) async {
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
      }),
    );
    controller = VoiceController(
      api: api,
      locationProvider: locationProvider,
      microphoneAccess: microphone,
      participantName: 'Josh',
      wakeLock: wakeLock,
      transportFactory: () {
        final transport = FakeVoiceTransport(
          publishFailure: _microphonePublishFails
              ? StateError('Microphone track could not be published.')
              : null,
        );
        transports.add(transport);
        return transport;
      },
    );
  }

  final bool _microphonePublishFails;
  final FakeAuthSession auth;
  late final VoxosApiClient api;
  late final VoiceController controller;
  final FakeMicrophoneAccess microphone;
  final FakeVoiceWakeLock wakeLock;
  final List<FakeVoiceTransport> transports = <FakeVoiceTransport>[];

  void dispose() {
    controller.dispose();
  }
}

final class _StaticLocationProvider implements CurrentLocationProvider {
  const _StaticLocationProvider(this.location);

  final AgentLocationSnapshot location;

  @override
  Future<AgentLocationSnapshot?> snapshotForAction() async => location;
}
