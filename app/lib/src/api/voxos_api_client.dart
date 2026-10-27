import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/chat/chat_api.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/notifications/push_api.dart';
import 'package:voxos/src/settings/model_settings.dart';
import 'package:voxos/src/settings/model_settings_api.dart';

final class ApiException implements Exception {
  const ApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

final class VoiceReadiness {
  const VoiceReadiness._({required this.isReady, this.reason});

  const VoiceReadiness.ready() : this._(isReady: true);

  const VoiceReadiness.unavailable(String reason)
    : this._(isReady: false, reason: reason);

  final bool isReady;
  final String? reason;
}

final class VoxosApiClient implements ChatApi, ModelSettingsApi, PushApi {
  VoxosApiClient({
    required AuthSession auth,
    required ServerConnection connection,
    http.Client? client,
  }) : _auth = auth,
       _connection = connection,
       _client = client ?? http.Client();

  static const _requestTimeout = Duration(seconds: 20);
  static const _maxAttachmentResponseBytes = 6 * 1024 * 1024;

  final AuthSession _auth;
  final http.Client _client;
  final ServerConnection _connection;

  @override
  Future<ChatSummary> createChat() async {
    final json = await _requestJson(
      method: 'POST',
      path: '/api/chats',
      body: const <String, Object?>{},
      fallbackError: 'The chat could not be created.',
    );
    try {
      return ChatSummary.fromJson(json);
    } on FormatException {
      throw const ApiException(
        'The Voxos server returned an invalid chat response.',
      );
    }
  }

  @override
  Future<void> deleteChat(String chatId) async {
    await _sendRequest(
      method: 'DELETE',
      path: '/api/chats/${Uri.encodeComponent(chatId)}',
      fallbackError: 'The chat could not be deleted.',
    );
  }

  @override
  Future<ChatDetail> loadChat(String chatId) async {
    final json = await _requestJson(
      method: 'GET',
      path: '/api/chats/${Uri.encodeComponent(chatId)}',
      fallbackError: 'The chat could not be loaded.',
    );
    try {
      return ChatDetail.fromJson(json);
    } on FormatException {
      throw const ApiException(
        'The Voxos server returned invalid chat history.',
      );
    }
  }

  @override
  Future<ChatList> loadChats() async {
    final json = await _requestJson(
      method: 'GET',
      path: '/api/chats',
      fallbackError: 'Chat history could not be loaded.',
    );
    try {
      return ChatList.fromJson(json);
    } on FormatException {
      throw const ApiException(
        'The Voxos server returned invalid chat history.',
      );
    }
  }

  @override
  Future<void> registerInstallation({
    required String installationId,
    required String platform,
    required String registrationToken,
  }) async {
    await _sendRequest(
      method: 'POST',
      path: '/api/push/installations/register',
      body: <String, Object?>{
        'installation_id': installationId,
        'platform': platform,
        'registration_token': registrationToken,
      },
      fallbackError: 'Notifications could not be connected.',
    );
  }

  @override
  Future<void> revokeInstallation({required String installationId}) async {
    await _sendRequest(
      method: 'POST',
      path: '/api/push/installations/revoke',
      body: <String, Object?>{'installation_id': installationId},
      fallbackError: 'Notifications could not be disconnected.',
    );
  }

  Future<VoiceReadiness> loadVoiceReadiness() async {
    final responses = await Future.wait(<Future<Map<String, Object?>>>[
      _requestJson(
        method: 'GET',
        path: '/api/voice/provider-settings',
        fallbackError: 'The Voxos server could not check voice setup.',
      ),
      _requestJson(
        method: 'GET',
        path: '/api/livekit/settings',
        fallbackError: 'The Voxos server could not check voice setup.',
      ),
    ]);
    final providerSettings = responses[0];
    final liveKitSettings = responses[1];

    final activeSettings = providerSettings['active_settings'];
    final activeProvider = providerSettings['active_provider'];
    final providers = providerSettings['providers'];
    final liveKitConfigured = liveKitSettings['configured'];

    if (activeSettings is! Map<String, Object?> ||
        activeProvider is! String ||
        providers is! List<Object?> ||
        liveKitConfigured is! bool) {
      throw const ApiException(
        'The server returned invalid voice readiness information.',
      );
    }

    final credentialConfigured = activeSettings['configured'];
    if (credentialConfigured != true) {
      return const VoiceReadiness.unavailable(
        'Finish voice provider setup in the Voxos web app.',
      );
    }

    Map<String, Object?>? selectedProvider;
    for (final provider in providers) {
      if (provider is Map<String, Object?> &&
          provider['provider'] == activeProvider) {
        selectedProvider = provider;
        break;
      }
    }
    if (selectedProvider == null ||
        selectedProvider['runtime_supported'] != true) {
      return const VoiceReadiness.unavailable(
        'The selected voice provider is not available on this deployment.',
      );
    }
    if (!liveKitConfigured) {
      return const VoiceReadiness.unavailable(
        'Finish LiveKit setup in the Voxos web app.',
      );
    }

    return const VoiceReadiness.ready();
  }

  @override
  Future<UserModelSettings> loadModelSettings() async {
    final responses = await Future.wait(<Future<Map<String, Object?>>>[
      _requestJson(
        method: 'GET',
        path: '/api/text/provider-settings',
        fallbackError: 'Text model settings could not be loaded.',
      ),
      _requestJson(
        method: 'GET',
        path: '/api/voice/provider-settings',
        fallbackError: 'Voice model settings could not be loaded.',
      ),
    ]);
    try {
      return UserModelSettings.fromProviderResponses(
        text: responses[0],
        voice: responses[1],
      );
    } on FormatException {
      throw const ApiException(
        'The Voxos server returned invalid model settings.',
      );
    }
  }

  @override
  Future<void> updateImageModel(ModelSelection selection) async {
    await _sendRequest(
      method: 'PUT',
      path: '/api/text/settings/image-generation',
      body: <String, Object?>{
        'provider': selection.provider,
        'model': selection.model,
      },
      fallbackError: 'The default image model could not be saved.',
    );
  }

  @override
  Future<void> updateTextModel({
    required TextModelPreferences current,
    required ProviderModelCatalog provider,
    required ModelSelection selection,
  }) async {
    await _sendRequest(
      method: 'PUT',
      path: '/api/text/settings',
      body: current.requestBodyWithSelection(
        selectedProvider: provider,
        selection: selection,
      ),
      fallbackError: 'The default text model could not be saved.',
    );
  }

  @override
  Future<void> updateVoiceModel({
    required VoiceModelPreferences current,
    required ProviderModelCatalog provider,
    required ModelSelection selection,
  }) async {
    await _sendRequest(
      method: 'PUT',
      path: '/api/voice/settings',
      body: current.requestBodyWithSelection(
        selectedProvider: provider,
        selection: selection,
      ),
      fallbackError: 'The default voice model could not be saved.',
    );
  }

  @override
  void dispose() {
    _client.close();
  }

  @override
  Future<ChatAttachmentSummary> uploadChatAttachment({
    required String chatId,
    required String clientDraftId,
    required PendingChatImage image,
  }) async {
    final token = await _auth.idToken();
    final request =
        http.MultipartRequest(
            'POST',
            _connection.resolvePath(
              '/api/chats/${Uri.encodeComponent(chatId)}/attachments',
            ),
          )
          ..headers.addAll(<String, String>{
            'Accept': 'application/json',
            'Authorization': 'Bearer $token',
          })
          ..fields['client_draft_id'] = clientDraftId
          ..fields['client_source'] = 'flutter'
          ..files.add(
            await http.MultipartFile.fromPath(
              'file',
              image.path,
              filename: image.name,
            ),
          );
    final response = await _sendBounded(
      request,
      fallbackError: 'The image could not be uploaded.',
      maxBytes: 1024 * 1024,
    );
    try {
      final decoded = jsonDecode(response);
      if (decoded is! Map<String, Object?>) {
        throw const FormatException('Invalid attachment response.');
      }
      return ChatAttachmentSummary.fromJson(decoded);
    } on FormatException {
      throw const ApiException(
        'The Voxos server returned an invalid image response.',
      );
    }
  }

  @override
  Future<void> deleteChatAttachment({
    required String chatId,
    required String attachmentId,
  }) async {
    await _sendRequest(
      method: 'DELETE',
      path:
          '/api/chats/${Uri.encodeComponent(chatId)}/attachments/'
          '${Uri.encodeComponent(attachmentId)}',
      fallbackError: 'The image could not be removed.',
    );
  }

  @override
  Future<Uint8List> loadChatAttachment({
    required String chatId,
    required String attachmentId,
    required bool thumbnail,
  }) async {
    final request = await _authorizedRequest(
      method: 'GET',
      path:
          '/api/chats/${Uri.encodeComponent(chatId)}/attachments/'
          '${Uri.encodeComponent(attachmentId)}/content'
          '?variant=${thumbnail ? 'thumbnail' : 'full'}',
      accept: 'image/*',
    );
    final bytes = await _sendBoundedBytes(
      request,
      fallbackError: 'The image could not be loaded.',
      maxBytes: _maxAttachmentResponseBytes,
    );
    return Uint8List.fromList(bytes);
  }

  @override
  Stream<ChatStreamEvent> streamChatMessage({
    List<String> attachmentIds = const <String>[],
    required String chatId,
    String? clientTurnId,
    AgentLocationSnapshot? currentLocation,
    required String text,
  }) async* {
    final body = <String, Object?>{
      'attachment_ids': attachmentIds,
      'text': text,
    };
    if (clientTurnId != null) {
      body['client_turn_id'] = clientTurnId;
    }
    if (currentLocation != null) {
      body['location'] = currentLocation.toJson();
    }
    final request = await _authorizedRequest(
      method: 'POST',
      path: '/api/chats/${Uri.encodeComponent(chatId)}/messages/stream',
      body: body,
      accept: 'text/event-stream',
    );
    late final http.StreamedResponse response;
    try {
      response = await _client.send(request).timeout(_requestTimeout);
    } on AuthFailure {
      rethrow;
    } on Exception {
      throw const ApiException(
        'Could not reach the Voxos server. Check your connection.',
      );
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      final body = await response.stream.bytesToString();
      throw ApiException(
        _errorMessage(
          statusCode: response.statusCode,
          body: body,
          fallback: 'The message could not be sent.',
        ),
      );
    }

    try {
      await for (final event in parseChatEventStream(response.stream)) {
        yield event;
      }
    } on FormatException {
      throw const ApiException(
        'The Voxos server returned an invalid chat stream.',
      );
    }
  }

  Future<String> _sendBounded(
    http.BaseRequest request, {
    required String fallbackError,
    required int maxBytes,
  }) async {
    final bytes = await _sendBoundedBytes(
      request,
      fallbackError: fallbackError,
      maxBytes: maxBytes,
    );
    return utf8.decode(bytes);
  }

  Future<List<int>> _sendBoundedBytes(
    http.BaseRequest request, {
    required String fallbackError,
    required int maxBytes,
  }) async {
    late final http.StreamedResponse response;
    try {
      response = await _client.send(request).timeout(_requestTimeout);
    } on AuthFailure {
      rethrow;
    } on Exception {
      throw const ApiException(
        'Could not reach the Voxos server. Check your connection.',
      );
    }
    final bytes = <int>[];
    await for (final chunk in response.stream.timeout(_requestTimeout)) {
      if (bytes.length + chunk.length > maxBytes) {
        throw const ApiException('The Voxos server returned too much data.');
      }
      bytes.addAll(chunk);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        _errorMessage(
          statusCode: response.statusCode,
          body: utf8.decode(bytes, allowMalformed: true),
          fallback: fallbackError,
        ),
      );
    }
    return bytes;
  }

  Future<Map<String, Object?>> _requestJson({
    required String fallbackError,
    required String method,
    required String path,
    Map<String, Object?>? body,
  }) async {
    final response = await _sendRequest(
      fallbackError: fallbackError,
      method: method,
      path: path,
      body: body,
    );
    try {
      final decoded = jsonDecode(response.body);
      if (decoded is! Map<String, Object?>) {
        throw const FormatException('Expected a JSON object.');
      }
      return decoded;
    } on FormatException {
      throw const ApiException(
        'The Voxos server returned an invalid response.',
      );
    }
  }

  Future<_ApiResponse> _sendRequest({
    required String fallbackError,
    required String method,
    required String path,
    Map<String, Object?>? body,
  }) async {
    final request = await _authorizedRequest(
      method: method,
      path: path,
      body: body,
    );
    late final http.StreamedResponse response;
    late final String responseBody;
    try {
      response = await _client.send(request).timeout(_requestTimeout);
      responseBody = await response.stream.bytesToString().timeout(
        _requestTimeout,
      );
    } on AuthFailure {
      rethrow;
    } on Exception {
      throw const ApiException(
        'Could not reach the Voxos server. Check your connection.',
      );
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        _errorMessage(
          statusCode: response.statusCode,
          body: responseBody,
          fallback: fallbackError,
        ),
      );
    }
    return _ApiResponse(body: responseBody);
  }

  Future<http.Request> _authorizedRequest({
    required String method,
    required String path,
    String accept = 'application/json',
    Map<String, Object?>? body,
  }) async {
    final token = await _auth.idToken();
    final request = http.Request(method, _connection.resolvePath(path));
    request.headers.addAll(<String, String>{
      'Accept': accept,
      'Authorization': 'Bearer $token',
    });
    if (body != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    return request;
  }

  String _errorMessage({
    required String body,
    required String fallback,
    required int statusCode,
  }) {
    if (statusCode == 401) {
      return 'Your sign-in is no longer valid. Sign out and try again.';
    }
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map<String, Object?>) {
        final detail = decoded['detail'];
        if (detail is String && detail.trim().isNotEmpty) {
          return detail.trim();
        }
      }
    } on FormatException {
      // Use the safe generic message below.
    }
    return fallback;
  }
}

final class _ApiResponse {
  const _ApiResponse({required this.body});

  final String body;
}
