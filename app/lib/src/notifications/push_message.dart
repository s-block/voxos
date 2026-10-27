enum PushIntentType { runTerminal, chatMessage }

final class PushIntent {
  const PushIntent({
    required this.notificationId,
    required this.type,
    this.chatId,
    this.messageId,
    this.runId,
    this.status,
  });

  factory PushIntent.fromData(Map<String, Object?> data) {
    if (data['schema_version'] != '1') {
      throw const FormatException('Unsupported push notification payload.');
    }
    final notificationId = _requiredString(data, 'notification_id');
    switch (data['type']) {
      case 'run_terminal':
        final status = _requiredString(data, 'status');
        if (status != 'succeeded' && status != 'failed') {
          throw const FormatException('Invalid push notification status.');
        }
        return PushIntent(
          notificationId: notificationId,
          type: PushIntentType.runTerminal,
          runId: _requiredString(data, 'run_id'),
          status: status,
          chatId: _optionalString(data, 'chat_id'),
        );
      case 'chat_message':
        return PushIntent(
          notificationId: notificationId,
          type: PushIntentType.chatMessage,
          chatId: _requiredString(data, 'chat_id'),
          messageId: _requiredString(data, 'message_id'),
          runId: _optionalString(data, 'run_id'),
        );
      default:
        throw const FormatException('Unsupported push notification payload.');
    }
  }

  final String? chatId;
  final String? messageId;
  final String notificationId;
  final String? runId;
  final String? status;
  final PushIntentType type;
}

final class PushEnvelope {
  const PushEnvelope({required this.data, this.body, this.title});

  final String? body;
  final Map<String, Object?> data;
  final String? title;

  PushIntent? get intent {
    try {
      return PushIntent.fromData(data);
    } on FormatException {
      return null;
    }
  }
}

final class PushEvent {
  const PushEvent({required this.envelope, required this.foreground});

  final PushEnvelope envelope;
  final bool foreground;
  PushIntent? get intent => envelope.intent;
}

String _requiredString(Map<String, Object?> data, String key) {
  final value = data[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Push notification is missing "$key".');
  }
  return value.trim();
}

String? _optionalString(Map<String, Object?> data, String key) {
  final value = data[key];
  if (value == null) {
    return null;
  }
  if (value is! String) {
    throw FormatException('Push notification contains an invalid "$key".');
  }
  final trimmed = value.trim();
  return trimmed.isEmpty ? null : trimmed;
}
