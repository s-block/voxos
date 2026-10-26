import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/location/location_service.dart';

abstract interface class ChatApi {
  Future<ChatSummary> createChat();

  Future<void> deleteChat(String chatId);

  void dispose();

  Future<ChatDetail> loadChat(String chatId);

  Future<ChatList> loadChats();

  Future<ChatAttachmentSummary> uploadChatAttachment({
    required String chatId,
    required String clientDraftId,
    required PendingChatImage image,
  });

  Future<void> deleteChatAttachment({
    required String chatId,
    required String attachmentId,
  });

  Future<Uint8List> loadChatAttachment({
    required String chatId,
    required String attachmentId,
    required bool thumbnail,
  });

  Stream<ChatStreamEvent> streamChatMessage({
    List<String> attachmentIds = const <String>[],
    required String chatId,
    String? clientTurnId,
    AgentLocationSnapshot? currentLocation,
    required String text,
  });
}

Stream<ChatStreamEvent> parseChatEventStream(Stream<List<int>> bytes) async* {
  final dataLines = <String>[];

  ChatStreamEvent? parseBufferedEvent() {
    if (dataLines.isEmpty) {
      return null;
    }
    final encoded = dataLines.join('\n');
    dataLines.clear();
    final decoded = jsonDecode(encoded);
    if (decoded is! Map<String, Object?>) {
      throw const FormatException('Invalid chat stream event.');
    }
    return ChatStreamEvent.fromJson(decoded);
  }

  await for (final line
      in bytes.transform(utf8.decoder).transform(const LineSplitter())) {
    if (line.isEmpty) {
      final event = parseBufferedEvent();
      if (event != null) {
        yield event;
      }
      continue;
    }
    if (line.startsWith(':')) {
      continue;
    }

    final separator = line.indexOf(':');
    final field = separator == -1 ? line : line.substring(0, separator);
    var value = separator == -1 ? '' : line.substring(separator + 1);
    if (value.startsWith(' ')) {
      value = value.substring(1);
    }
    if (field == 'data') {
      dataLines.add(value);
    }
  }

  final finalEvent = parseBufferedEvent();
  if (finalEvent != null) {
    yield finalEvent;
  }
}
