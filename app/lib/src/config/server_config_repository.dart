import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/config/server_config.dart';

final class ServerConnectionException implements Exception {
  const ServerConnectionException(this.message);

  final String message;

  @override
  String toString() => message;
}

final class ServerConfigRepository {
  ServerConfigRepository({
    required SharedPreferences preferences,
    http.Client? client,
  }) : _preferences = preferences,
       _client = client ?? http.Client();

  static const _activeStorageKey = 'voxos_active_server_v2';
  static const _legacyStorageKey = 'voxos_server_connection_v1';
  static const _serversStorageKey = 'voxos_servers_v2';
  static const _requestTimeout = Duration(seconds: 15);

  final http.Client _client;
  final SharedPreferences _preferences;

  ServerConnection? load() {
    final connections = loadAll();
    final activeEndpoint = _preferences.getString(_activeStorageKey);
    if (activeEndpoint != null) {
      for (final connection in connections) {
        if (connection.baseUri.toString() == activeEndpoint) {
          return connection;
        }
      }
    }
    return connections.firstOrNull;
  }

  List<ServerConnection> loadAll() {
    final storedServers = _preferences.getStringList(_serversStorageKey);
    if (storedServers != null) {
      return _decodeUnique(storedServers);
    }

    final legacy = _preferences.getString(_legacyStorageKey);
    if (legacy == null) {
      return const <ServerConnection>[];
    }

    try {
      return <ServerConnection>[ServerConnection.decode(legacy)];
    } on FormatException {
      _preferences.remove(_legacyStorageKey);
      return const <ServerConnection>[];
    }
  }

  Future<ServerConnection> fetchAndSave(String endpoint) async {
    final baseUri = ServerEndpoint.parse(endpoint);
    final configUri = baseUri.resolve('/api/client-config');

    late final http.Response response;
    try {
      response = await _client
          .get(
            configUri,
            headers: const <String, String>{'Accept': 'application/json'},
          )
          .timeout(_requestTimeout);
    } on Exception {
      throw const ServerConnectionException(
        'Could not reach that Voxos server. Check the address and try again.',
      );
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ServerConnectionException(_serverError(response));
    }

    try {
      final decoded = jsonDecode(response.body);
      if (decoded is! Map<String, Object?>) {
        throw const FormatException('Expected a JSON object.');
      }
      final connection = ServerConnection(
        baseUri: baseUri,
        config: ServerConfig.fromJson(decoded),
      );
      final connections = loadAll().toList();
      final existingIndex = connections.indexWhere(
        (stored) => stored.baseUri == connection.baseUri,
      );
      if (existingIndex == -1) {
        connections.add(connection);
      } else {
        connections[existingIndex] = connection;
      }
      await _preferences.setStringList(
        _serversStorageKey,
        connections.map((stored) => stored.encode()).toList(),
      );
      await _preferences.setString(
        _activeStorageKey,
        connection.baseUri.toString(),
      );
      await _preferences.remove(_legacyStorageKey);
      return connection;
    } on FormatException {
      throw const ServerConnectionException(
        'That server returned invalid mobile configuration.',
      );
    }
  }

  void dispose() {
    _client.close();
  }

  List<ServerConnection> _decodeUnique(List<String> values) {
    final connections = <ServerConnection>[];
    final endpoints = <Uri>{};
    for (final value in values) {
      try {
        final connection = ServerConnection.decode(value);
        if (endpoints.add(connection.baseUri)) {
          connections.add(connection);
        }
      } on FormatException {
        // Invalid entries are discarded without losing the other servers.
      }
    }
    return connections;
  }

  String _serverError(http.Response response) {
    if (response.statusCode == 404) {
      return 'That deployment does not support the Voxos mobile app yet.';
    }

    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map<String, Object?>) {
        final detail = decoded['detail'];
        if (detail is String && detail.trim().isNotEmpty) {
          return detail.trim();
        }
      }
    } on FormatException {
      // Fall through to the safe generic message.
    }
    return 'The Voxos server could not provide mobile configuration.';
  }
}
