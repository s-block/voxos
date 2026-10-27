import 'dart:typed_data';

final class ChatSummary {
  const ChatSummary({
    required this.agentId,
    required this.id,
    required this.model,
    required this.provider,
    required this.startedAt,
    required this.status,
    this.supportsImageInput = false,
    required this.updatedAt,
    this.title,
  });

  factory ChatSummary.fromJson(Map<String, Object?> json) {
    return ChatSummary(
      agentId: _requiredString(json, 'agent_id'),
      id: _requiredString(json, 'id'),
      model: _requiredString(json, 'model'),
      provider: _requiredString(json, 'provider'),
      startedAt: _requiredDateTime(json, 'started_at'),
      status: _requiredString(json, 'status'),
      supportsImageInput: json['supports_image_input'] == true,
      title: _optionalString(json, 'title'),
      updatedAt: _requiredDateTime(json, 'updated_at'),
    );
  }

  final String agentId;
  final String id;
  final String model;
  final String provider;
  final DateTime startedAt;
  final String status;
  final bool supportsImageInput;
  final String? title;
  final DateTime updatedAt;

  String get displayTitle =>
      title?.trim().isNotEmpty == true ? title!.trim() : 'New chat';
}

final class ChatList {
  const ChatList({
    required this.chats,
    required this.imageInputEnabled,
    this.imageEditingEnabled = false,
  });

  factory ChatList.fromJson(Map<String, Object?> json) {
    final chatJson = json['chats'];
    if (chatJson is! List<Object?>) {
      throw const FormatException('Invalid chat history.');
    }
    return ChatList(
      chats: List<ChatSummary>.unmodifiable(
        chatJson.map((item) {
          if (item is! Map<String, Object?>) {
            throw const FormatException('Invalid chat summary.');
          }
          return ChatSummary.fromJson(item);
        }),
      ),
      imageInputEnabled: json['image_input_enabled'] != false,
      imageEditingEnabled: json['image_editing_enabled'] == true,
    );
  }

  final List<ChatSummary> chats;
  final bool imageEditingEnabled;
  final bool imageInputEnabled;
}

final class ChatMessage {
  const ChatMessage({
    required this.createdAt,
    required this.id,
    required this.role,
    required this.source,
    required this.text,
    this.attachments = const <ChatAttachmentSummary>[],
    this.placeCards = const <ChatPlaceCard>[],
  });

  factory ChatMessage.fromJson(Map<String, Object?> json) {
    return ChatMessage(
      createdAt: _requiredDateTime(json, 'created_at'),
      id: _requiredString(json, 'id'),
      role: _requiredString(json, 'role'),
      source: _requiredString(json, 'source'),
      text: _requiredString(json, 'text', allowEmpty: true),
      attachments: _attachmentList(json['attachments']),
      placeCards: _placeCardList(json['place_cards']),
    );
  }

  final DateTime createdAt;
  final String id;
  final String role;
  final String source;
  final String text;
  final List<ChatAttachmentSummary> attachments;
  final List<ChatPlaceCard> placeCards;
}

final class ChatPlaceCard {
  const ChatPlaceCard({
    required this.attributionUri,
    required this.placeId,
    required this.placeUri,
    required this.position,
    required this.title,
    this.directionsUri,
    this.latitude,
    this.longitude,
  });

  factory ChatPlaceCard.fromJson(Map<String, Object?> json) {
    if (json['provider'] != 'google_maps') {
      throw const FormatException('Invalid place card provider.');
    }
    return ChatPlaceCard(
      attributionUri: _requiredGoogleMapsUri(json, 'attribution_url'),
      directionsUri: _optionalGoogleMapsUri(json, 'directions_url'),
      latitude: _optionalCoordinate(json, 'latitude', min: -90, max: 90),
      longitude: _optionalCoordinate(json, 'longitude', min: -180, max: 180),
      placeId: _requiredString(json, 'place_id'),
      placeUri: _requiredGoogleMapsUri(json, 'place_url'),
      position: _requiredInt(json, 'position'),
      title: _requiredString(json, 'title'),
    );
  }

  final Uri attributionUri;
  final Uri? directionsUri;
  final double? latitude;
  final double? longitude;
  final String placeId;
  final Uri placeUri;
  final int position;
  final String title;
}

final class ChatAttachmentSummary {
  const ChatAttachmentSummary({
    required this.byteSize,
    required this.height,
    required this.id,
    required this.mimeType,
    required this.width,
    this.position,
  });

  factory ChatAttachmentSummary.fromJson(Map<String, Object?> json) {
    return ChatAttachmentSummary(
      byteSize: _requiredInt(json, 'byte_size'),
      height: _requiredInt(json, 'height'),
      id: _requiredString(json, 'id'),
      mimeType: _requiredString(json, 'mime_type'),
      position: _optionalInt(json, 'position'),
      width: _requiredInt(json, 'width'),
    );
  }

  final int byteSize;
  final int height;
  final String id;
  final String mimeType;
  final int? position;
  final int width;
}

enum PendingImageStatus { selected, uploading, uploaded }

final class PendingChatImage {
  const PendingChatImage({
    required this.byteSize,
    required this.id,
    required this.mimeType,
    required this.name,
    required this.path,
    this.error,
    this.status = PendingImageStatus.selected,
    this.uploaded,
  });

  final int byteSize;
  final String? error;
  final String id;
  final String mimeType;
  final String name;
  final String path;
  final PendingImageStatus status;
  final ChatAttachmentSummary? uploaded;

  PendingChatImage copyWith({
    String? error,
    bool clearError = false,
    PendingImageStatus? status,
    ChatAttachmentSummary? uploaded,
  }) {
    return PendingChatImage(
      byteSize: byteSize,
      error: clearError ? null : error ?? this.error,
      id: id,
      mimeType: mimeType,
      name: name,
      path: path,
      status: status ?? this.status,
      uploaded: uploaded ?? this.uploaded,
    );
  }
}

final class ChatDetail {
  const ChatDetail({required this.chat, required this.messages});

  factory ChatDetail.fromJson(Map<String, Object?> json) {
    final chatJson = json['chat'];
    final messageJson = json['messages'];
    if (chatJson is! Map<String, Object?> || messageJson is! List<Object?>) {
      throw const FormatException('Invalid chat detail.');
    }
    return ChatDetail(
      chat: ChatSummary.fromJson(chatJson),
      messages: List<ChatMessage>.unmodifiable(
        messageJson.map((item) {
          if (item is! Map<String, Object?>) {
            throw const FormatException('Invalid chat message.');
          }
          return ChatMessage.fromJson(item);
        }),
      ),
    );
  }

  final ChatSummary chat;
  final List<ChatMessage> messages;
}

final class ChatStreamEvent {
  const ChatStreamEvent({
    required this.kind,
    required this.payload,
    this.createdAt,
    this.id,
    this.runId,
    this.sessionId,
  });

  factory ChatStreamEvent.fromJson(Map<String, Object?> json) {
    final payload = json['payload'];
    if (payload is! Map<String, Object?>) {
      throw const FormatException('Invalid chat stream payload.');
    }
    return ChatStreamEvent(
      createdAt: _optionalDateTime(json, 'created_at'),
      id: _optionalString(json, 'id'),
      kind: _requiredString(json, 'kind'),
      payload: Map<String, Object?>.unmodifiable(payload),
      runId: _optionalString(json, 'run_id'),
      sessionId: _optionalString(json, 'session_id'),
    );
  }

  final DateTime? createdAt;
  final String? id;
  final String kind;
  final Map<String, Object?> payload;
  final String? runId;
  final String? sessionId;

  String? get delta {
    final value = payload['delta'];
    return value is String ? value : null;
  }

  String? get errorMessage {
    final value = payload['error_message'];
    return value is String && value.trim().isNotEmpty ? value.trim() : null;
  }

  String? get messageId {
    final value = payload['message_id'];
    return value is String && value.trim().isNotEmpty ? value.trim() : null;
  }

  String? get toolName {
    final value = payload['name'];
    return value is String && value.trim().isNotEmpty ? value.trim() : null;
  }

  ChatActivityPresentation? get activity =>
      ChatActivityPresentation.fromValue(payload['activity']);

  List<ChatAttachmentSummary> get attachments =>
      _attachmentList(payload['attachments']);

  List<ChatPlaceCard> get placeCards => _placeCardList(payload['place_cards']);
}

final class ChatActivityLogo {
  const ChatActivityLogo({required this.domain, required this.label});

  static ChatActivityLogo? fromValue(Object? value) {
    if (value is! Map<String, Object?> || value['source'] != 'domain') {
      return null;
    }
    final domain = value['domain'];
    final label = value['label'];
    if (domain is! String ||
        domain.trim().isEmpty ||
        label is! String ||
        label.trim().isEmpty) {
      return null;
    }
    return ChatActivityLogo(domain: domain.trim(), label: label.trim());
  }

  final String domain;
  final String label;
}

enum ChatActivityIcon {
  mcp,
  web;

  static ChatActivityIcon? fromValue(Object? value) {
    return switch (value) {
      'mcp' => ChatActivityIcon.mcp,
      'web' => ChatActivityIcon.web,
      _ => null,
    };
  }
}

final class ChatActivityPresentation {
  const ChatActivityPresentation({
    required this.label,
    this.detail,
    this.icon,
    this.logo,
  });

  factory ChatActivityPresentation.thinking() {
    return const ChatActivityPresentation(label: 'Thinking…');
  }

  static ChatActivityPresentation? fromValue(Object? value) {
    if (value is! Map<String, Object?>) {
      return null;
    }
    final label = value['label'];
    if (label is! String || label.trim().isEmpty) {
      return null;
    }
    final detailValue = value['detail'];
    final detail = detailValue is String && detailValue.trim().isNotEmpty
        ? detailValue.trim()
        : null;
    return ChatActivityPresentation(
      detail: detail,
      icon: ChatActivityIcon.fromValue(value['icon']),
      label: label.trim(),
      logo: ChatActivityLogo.fromValue(value['logo']),
    );
  }

  final String? detail;
  final ChatActivityIcon? icon;
  final String label;
  final ChatActivityLogo? logo;
}

final class ChatViewMessage {
  const ChatViewMessage({
    required this.id,
    required this.role,
    required this.text,
    this.activity,
    this.attachments = const <ChatViewAttachment>[],
    this.isStreaming = false,
    this.placeCards = const <ChatPlaceCard>[],
  });

  factory ChatViewMessage.fromRecord(ChatMessage message) {
    return ChatViewMessage(
      id: message.id,
      role: message.role,
      text: message.text,
      attachments: message.attachments
          .map(ChatViewAttachment.fromSummary)
          .toList(growable: false),
      placeCards: message.placeCards,
    );
  }

  final String id;
  final bool isStreaming;
  final String role;
  final String text;
  final ChatActivityPresentation? activity;
  final List<ChatViewAttachment> attachments;
  final List<ChatPlaceCard> placeCards;

  ChatViewMessage copyWith({
    ChatActivityPresentation? activity,
    List<ChatViewAttachment>? attachments,
    bool clearActivity = false,
    bool? isStreaming,
    List<ChatPlaceCard>? placeCards,
    String? text,
  }) {
    return ChatViewMessage(
      activity: clearActivity ? null : activity ?? this.activity,
      attachments: attachments ?? this.attachments,
      id: id,
      isStreaming: isStreaming ?? this.isStreaming,
      placeCards: placeCards ?? this.placeCards,
      role: role,
      text: text ?? this.text,
    );
  }
}

final class ChatViewAttachment {
  const ChatViewAttachment({required this.id, this.localPath, this.summary});

  factory ChatViewAttachment.fromSummary(ChatAttachmentSummary summary) {
    return ChatViewAttachment(id: summary.id, summary: summary);
  }

  final String id;
  final String? localPath;
  final ChatAttachmentSummary? summary;
}

String _requiredString(
  Map<String, Object?> json,
  String key, {
  bool allowEmpty = false,
}) {
  final value = json[key];
  if (value is! String || (!allowEmpty && value.trim().isEmpty)) {
    throw FormatException('Invalid $key.');
  }
  return value;
}

String? _optionalString(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value == null) {
    return null;
  }
  if (value is! String) {
    throw FormatException('Invalid $key.');
  }
  return value;
}

DateTime _requiredDateTime(Map<String, Object?> json, String key) {
  final value = _requiredString(json, key);
  final parsed = DateTime.tryParse(value);
  if (parsed == null) {
    throw FormatException('Invalid $key.');
  }
  return parsed;
}

DateTime? _optionalDateTime(Map<String, Object?> json, String key) {
  final value = _optionalString(json, key);
  if (value == null) {
    return null;
  }
  final parsed = DateTime.tryParse(value);
  if (parsed == null) {
    throw FormatException('Invalid $key.');
  }
  return parsed;
}

int _requiredInt(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! int || value < 0) {
    throw FormatException('Invalid $key.');
  }
  return value;
}

int? _optionalInt(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value == null) {
    return null;
  }
  if (value is! int || value < 0) {
    throw FormatException('Invalid $key.');
  }
  return value;
}

List<ChatAttachmentSummary> _attachmentList(Object? value) {
  if (value == null) {
    return const <ChatAttachmentSummary>[];
  }
  if (value is! List<Object?>) {
    throw const FormatException('Invalid attachments.');
  }
  return List<ChatAttachmentSummary>.unmodifiable(
    value.map((item) {
      if (item is! Map<String, Object?>) {
        throw const FormatException('Invalid attachment.');
      }
      return ChatAttachmentSummary.fromJson(item);
    }),
  );
}

List<ChatPlaceCard> _placeCardList(Object? value) {
  if (value == null) {
    return const <ChatPlaceCard>[];
  }
  if (value is! List<Object?>) {
    throw const FormatException('Invalid place cards.');
  }
  return List<ChatPlaceCard>.unmodifiable(
    value.map((item) {
      if (item is! Map<String, Object?>) {
        throw const FormatException('Invalid place card.');
      }
      return ChatPlaceCard.fromJson(item);
    }),
  );
}

Uri _requiredGoogleMapsUri(Map<String, Object?> json, String key) {
  final value = _requiredString(json, key);
  final uri = Uri.tryParse(value);
  if (uri == null || !_isGoogleMapsUri(uri)) {
    throw FormatException('Invalid $key.');
  }
  return uri;
}

Uri? _optionalGoogleMapsUri(Map<String, Object?> json, String key) {
  if (json[key] == null) {
    return null;
  }
  return _requiredGoogleMapsUri(json, key);
}

double? _optionalCoordinate(
  Map<String, Object?> json,
  String key, {
  required double min,
  required double max,
}) {
  final value = json[key];
  if (value == null) {
    return null;
  }
  if (value is! num || !value.isFinite || value < min || value > max) {
    throw FormatException('Invalid $key.');
  }
  return value.toDouble();
}

bool _isGoogleMapsUri(Uri uri) {
  final rawHost = uri.host.toLowerCase();
  final host = rawHost.endsWith('.')
      ? rawHost.substring(0, rawHost.length - 1)
      : rawHost;
  final path = uri.path.isEmpty ? '/' : uri.path;
  final isMapsDestination = switch (host) {
    'maps.app.goo.gl' => path != '/',
    'maps.google.com' =>
      path == '/' || path == '/maps' || path.startsWith('/maps/'),
    'google.com' ||
    'www.google.com' => path == '/maps' || path.startsWith('/maps/'),
    _ => false,
  };
  const sensitiveQueryKeys = <String>{
    'access_token',
    'api_key',
    'apikey',
    'credential',
    'key',
    'password',
    'secret',
    'sig',
    'signature',
    'token',
  };
  return uri.scheme == 'https' &&
      uri.hasAuthority &&
      uri.userInfo.isEmpty &&
      (!uri.hasPort || uri.port == 443) &&
      uri.fragment.isEmpty &&
      isMapsDestination &&
      !uri.queryParametersAll.keys.any(
        (key) => sensitiveQueryKeys.contains(key.toLowerCase()),
      );
}

typedef AttachmentBytes = Uint8List;
