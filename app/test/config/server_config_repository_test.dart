import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/config/server_config_repository.dart';

import '../support/config_fixtures.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  test('fetches and persists public server configuration', () async {
    final preferences = await SharedPreferences.getInstance();
    late Uri requestedUri;
    final repository = ServerConfigRepository(
      preferences: preferences,
      client: MockClient((request) async {
        requestedUri = request.url;
        expect(request.headers['accept'], 'application/json');
        return http.Response(jsonEncode(clientConfigJson()), 200);
      }),
    );

    final fetched = await repository.fetchAndSave('https://voxos.example.com/');
    final restored = repository.load();
    final all = repository.loadAll();

    expect(
      requestedUri,
      Uri.parse('https://voxos.example.com/api/client-config'),
    );
    expect(fetched.config.firebase.projectId, 'voxos-project');
    expect(restored?.baseUri, Uri.parse('https://voxos.example.com'));
    expect(restored?.config.firebase.iosAppId, 'ios-app-id');
    expect(restored?.config.firebase.googleIosClientId, 'google-ios-client-id');
    expect(all, hasLength(1));

    repository.dispose();
  });

  test('stores unique servers and remembers the selected server', () async {
    final preferences = await SharedPreferences.getInstance();
    final repository = ServerConfigRepository(
      preferences: preferences,
      client: MockClient(
        (_) async => http.Response(jsonEncode(clientConfigJson()), 200),
      ),
    );

    await repository.fetchAndSave('https://one.example.com');
    await repository.fetchAndSave('https://two.example.com');
    await repository.fetchAndSave('https://ONE.example.com:443/');

    expect(
      repository.loadAll().map((connection) => connection.baseUri.toString()),
      <String>['https://one.example.com', 'https://two.example.com'],
    );
    expect(repository.load()?.baseUri, Uri.parse('https://one.example.com'));

    repository.dispose();
  });

  test('loads the legacy connection until it is refreshed', () async {
    final legacy = ServerConnection(
      baseUri: Uri.parse('https://legacy.example.com'),
      config: ServerConfig.fromJson(clientConfigJson()),
    );
    SharedPreferences.setMockInitialValues(<String, Object>{
      'voxos_server_connection_v1': legacy.encode(),
    });
    final preferences = await SharedPreferences.getInstance();
    final repository = ServerConfigRepository(
      preferences: preferences,
      client: MockClient(
        (_) async => http.Response(jsonEncode(clientConfigJson()), 200),
      ),
    );

    expect(repository.load()?.baseUri, legacy.baseUri);
    expect(repository.loadAll(), hasLength(1));

    await repository.fetchAndSave(legacy.baseUri.toString());

    expect(preferences.getString('voxos_server_connection_v1'), isNull);
    expect(repository.loadAll(), hasLength(1));

    repository.dispose();
  });

  test('does not persist an invalid server response', () async {
    final preferences = await SharedPreferences.getInstance();
    final repository = ServerConfigRepository(
      preferences: preferences,
      client: MockClient((_) async => http.Response('{}', 200)),
    );

    await expectLater(
      repository.fetchAndSave('https://voxos.example.com'),
      throwsA(isA<ServerConnectionException>()),
    );
    expect(repository.load(), isNull);

    repository.dispose();
  });
}
