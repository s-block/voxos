import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/config/server_config.dart';

import '../support/config_fixtures.dart';

void main() {
  group('ServerEndpoint', () {
    test('normalizes an HTTPS deployment root', () {
      expect(
        ServerEndpoint.parse(' voxos.example.com/ ').toString(),
        'https://voxos.example.com',
      );
      expect(
        ServerEndpoint.parse('HTTPS://VOXOS.EXAMPLE.COM:443').toString(),
        'https://voxos.example.com',
      );
    });

    test('rejects insecure or path-scoped endpoints', () {
      expect(
        () => ServerEndpoint.parse('http://voxos.example.com'),
        throwsFormatException,
      );
      expect(
        () => ServerEndpoint.parse('https://voxos.example.com/api'),
        throwsFormatException,
      );
    });
  });

  test('parses platform Firebase IDs and voice routing config', () {
    final connection = ServerConnection(
      baseUri: ServerEndpoint.parse('https://voxos.example.com'),
      config: ServerConfig.fromJson(clientConfigJson()),
    );

    expect(connection.config.firebase.projectId, 'voxos-project');
    expect(connection.config.firebase.androidAppId, 'android-app-id');
    expect(connection.config.firebase.iosAppId, 'ios-app-id');
    expect(
      connection.config.firebase.googleIosClientId,
      'google-ios-client-id',
    );
    expect(
      connection.config.firebase.googleServerClientId,
      'google-server-client-id',
    );
    expect(connection.config.voice.agentName, 'voxos-voice-agent');
    expect(
      connection.resolvePath(connection.config.voice.tokenEndpointPath),
      Uri.parse('https://voxos.example.com/api/livekit/token'),
    );
  });

  test('rejects unsupported schemas and absolute token endpoints', () {
    final unsupported = clientConfigJson()..['schema_version'] = 2;
    expect(() => ServerConfig.fromJson(unsupported), throwsFormatException);

    final invalidPath = clientConfigJson();
    (invalidPath['voice']! as Map<String, Object?>)['token_endpoint_path'] =
        'https://elsewhere.example/api/livekit/token';
    expect(() => ServerConfig.fromJson(invalidPath), throwsFormatException);
  });
}
