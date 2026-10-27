import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/voice/livekit_voice_transport.dart';

import '../support/config_fixtures.dart';
import '../support/fakes.dart';

void main() {
  test('dispose cancels a connection waiting for an ID token', () async {
    final pendingToken = Completer<String>();
    final transport = LiveKitVoiceTransport(
      auth: FakeAuthSession(pendingToken: pendingToken),
      connection: ServerConnection(
        baseUri: Uri.parse('https://voxos.example.com'),
        config: ServerConfig.fromJson(clientConfigJson()),
      ),
    );

    final connecting = transport.connect(
      participantName: 'Josh',
      roomName: 'voxos-mobile-test',
      startWithMicrophone: true,
    );
    await Future<void>.delayed(Duration.zero);

    await transport.dispose();
    pendingToken.complete('late-token');

    await expectLater(connecting, throwsA(isA<StateError>()));
  });
}
