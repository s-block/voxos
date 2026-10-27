import 'dart:convert';

final class ServerEndpoint {
  ServerEndpoint._();

  static Uri parse(String input) {
    final trimmed = input.trim();
    if (trimmed.isEmpty) {
      throw const FormatException('Enter your Voxos server address.');
    }

    final withScheme = trimmed.contains('://') ? trimmed : 'https://$trimmed';
    final uri = Uri.tryParse(withScheme);
    if (uri == null || uri.host.isEmpty) {
      throw const FormatException('Enter a valid server address.');
    }
    if (uri.userInfo.isNotEmpty ||
        uri.query.isNotEmpty ||
        uri.fragment.isNotEmpty) {
      throw const FormatException(
        'Use only the server address, without credentials, a query, or a fragment.',
      );
    }
    if (uri.path.isNotEmpty && uri.path != '/') {
      throw const FormatException(
        'Use the deployment root, without an API path.',
      );
    }
    if (uri.scheme != 'https') {
      throw const FormatException('The server address must use HTTPS.');
    }

    return Uri.parse(uri.origin);
  }
}

final class FirebaseClientConfig {
  const FirebaseClientConfig({
    required this.androidAppId,
    required this.apiKey,
    required this.googleIosClientId,
    required this.googleServerClientId,
    required this.iosAppId,
    required this.messagingSenderId,
    required this.projectId,
    this.authDomain,
    this.storageBucket,
  });

  factory FirebaseClientConfig.fromJson(Map<String, Object?> json) {
    return FirebaseClientConfig(
      androidAppId: _requiredString(json, 'android_app_id'),
      apiKey: _requiredString(json, 'api_key'),
      authDomain: _optionalString(json, 'auth_domain'),
      googleIosClientId: _requiredString(json, 'google_ios_client_id'),
      googleServerClientId: _requiredString(json, 'google_server_client_id'),
      iosAppId: _requiredString(json, 'ios_app_id'),
      messagingSenderId: _requiredString(json, 'messaging_sender_id'),
      projectId: _requiredString(json, 'project_id'),
      storageBucket: _optionalString(json, 'storage_bucket'),
    );
  }

  final String androidAppId;
  final String apiKey;
  final String? authDomain;
  final String googleIosClientId;
  final String googleServerClientId;
  final String iosAppId;
  final String messagingSenderId;
  final String projectId;
  final String? storageBucket;

  Map<String, Object?> toJson() => <String, Object?>{
    'android_app_id': androidAppId,
    'api_key': apiKey,
    'auth_domain': authDomain,
    'google_ios_client_id': googleIosClientId,
    'google_server_client_id': googleServerClientId,
    'ios_app_id': iosAppId,
    'messaging_sender_id': messagingSenderId,
    'project_id': projectId,
    'storage_bucket': storageBucket,
  };
}

final class VoiceClientConfig {
  const VoiceClientConfig({
    required this.agentName,
    required this.tokenEndpointPath,
  });

  factory VoiceClientConfig.fromJson(Map<String, Object?> json) {
    final tokenEndpointPath = _requiredString(json, 'token_endpoint_path');
    final tokenUri = Uri.tryParse(tokenEndpointPath);
    if (tokenUri == null ||
        !tokenEndpointPath.startsWith('/') ||
        tokenUri.hasAuthority ||
        tokenUri.query.isNotEmpty ||
        tokenUri.fragment.isNotEmpty) {
      throw const FormatException(
        'Server config contains an invalid voice token path.',
      );
    }

    return VoiceClientConfig(
      agentName: _requiredString(json, 'agent_name'),
      tokenEndpointPath: tokenEndpointPath,
    );
  }

  final String agentName;
  final String tokenEndpointPath;

  Map<String, Object?> toJson() => <String, Object?>{
    'agent_name': agentName,
    'token_endpoint_path': tokenEndpointPath,
  };
}

final class PushClientConfig {
  const PushClientConfig({required this.enabled});

  factory PushClientConfig.fromJson(Map<String, Object?> json) {
    final enabled = json['enabled'];
    if (enabled is! bool) {
      throw const FormatException(
        'Server config contains an invalid push notification setting.',
      );
    }
    return PushClientConfig(enabled: enabled);
  }

  const PushClientConfig.disabled() : enabled = false;

  final bool enabled;

  Map<String, Object?> toJson() => <String, Object?>{'enabled': enabled};
}

final class ServerConfig {
  const ServerConfig({
    required this.firebase,
    required this.push,
    required this.schemaVersion,
    required this.voice,
  });

  factory ServerConfig.fromJson(Map<String, Object?> json) {
    final schemaVersion = json['schema_version'];
    if (schemaVersion != 1) {
      throw const FormatException(
        'This server uses an unsupported mobile config version.',
      );
    }

    return ServerConfig(
      firebase: FirebaseClientConfig.fromJson(_requiredMap(json, 'firebase')),
      push: switch (json['push']) {
        final Map<String, Object?> value => PushClientConfig.fromJson(value),
        null => const PushClientConfig.disabled(),
        _ => throw const FormatException(
          'Server config contains an invalid push notification setting.',
        ),
      },
      schemaVersion: schemaVersion! as int,
      voice: VoiceClientConfig.fromJson(_requiredMap(json, 'voice')),
    );
  }

  final FirebaseClientConfig firebase;
  final PushClientConfig push;
  final int schemaVersion;
  final VoiceClientConfig voice;

  Map<String, Object?> toJson() => <String, Object?>{
    'firebase': firebase.toJson(),
    'push': push.toJson(),
    'schema_version': schemaVersion,
    'voice': voice.toJson(),
  };
}

final class ServerConnection {
  const ServerConnection({required this.baseUri, required this.config});

  factory ServerConnection.fromJson(Map<String, Object?> json) {
    final baseUri = ServerEndpoint.parse(_requiredString(json, 'base_url'));
    return ServerConnection(
      baseUri: baseUri,
      config: ServerConfig.fromJson(_requiredMap(json, 'config')),
    );
  }

  factory ServerConnection.decode(String value) {
    final decoded = jsonDecode(value);
    if (decoded is! Map<String, Object?>) {
      throw const FormatException('Stored server config is invalid.');
    }
    return ServerConnection.fromJson(decoded);
  }

  final Uri baseUri;
  final ServerConfig config;

  Uri resolvePath(String path) => baseUri.resolve(path);

  String encode() => jsonEncode(<String, Object?>{
    'base_url': baseUri.toString(),
    'config': config.toJson(),
  });
}

Map<String, Object?> _requiredMap(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! Map<String, Object?>) {
    throw FormatException('Server config is missing "$key".');
  }
  return value;
}

String _requiredString(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Server config is missing "$key".');
  }
  return value.trim();
}

String? _optionalString(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value == null) {
    return null;
  }
  if (value is! String) {
    throw FormatException('Server config contains an invalid "$key".');
  }
  final trimmed = value.trim();
  return trimmed.isEmpty ? null : trimmed;
}
