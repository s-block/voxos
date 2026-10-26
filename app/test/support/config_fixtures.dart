const String testFirebaseApiKey = 'public-api-key'; // pragma: allowlist secret

Map<String, Object?> clientConfigJson() => <String, Object?>{
  'schema_version': 1,
  'firebase': <String, Object?>{
    'project_id': 'voxos-project',
    'api_key': testFirebaseApiKey,
    'messaging_sender_id': '123456789',
    'android_app_id': 'android-app-id',
    'ios_app_id': 'ios-app-id',
    'google_ios_client_id': 'google-ios-client-id',
    'google_server_client_id': 'google-server-client-id',
    'auth_domain': 'voxos-project.firebaseapp.com',
    'storage_bucket': 'voxos-project.firebasestorage.app',
  },
  'push': <String, Object?>{'enabled': false},
  'voice': <String, Object?>{
    'agent_name': 'voxos-voice-agent',
    'token_endpoint_path': '/api/livekit/token',
  },
};
