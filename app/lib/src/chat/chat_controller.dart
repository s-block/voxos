import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';
import 'package:voxos/src/chat/chat_api.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/location/location_service.dart';

final class ChatController extends ChangeNotifier {
  ChatController({
    required ChatApi api,
    CurrentLocationProvider? locationProvider,
    Uuid uuid = const Uuid(),
  }) : _api = api,
       _locationProvider = locationProvider,
       _uuid = uuid,
       _draftId = uuid.v4();

  static const int maxMessageLength = 24000;
  static const int maxDraftImages = 4;

  final ChatApi _api;
  final CurrentLocationProvider? _locationProvider;
  final Uuid _uuid;

  final Set<String> _deletingChatIds = <String>{};
  List<ChatSummary> _chats = const <ChatSummary>[];
  List<ChatViewMessage> _messages = const <ChatViewMessage>[];
  List<PendingChatImage> _pendingImages = const <PendingChatImage>[];
  final Map<String, Uint8List> _thumbnailCache = <String, Uint8List>{};
  String _draftId;
  StreamIterator<ChatStreamEvent>? _streamIterator;
  ChatSummary? _selectedChat;
  String? _error;
  bool _disposed = false;
  bool _imageInputEnabled = true;
  bool _imageEditingEnabled = false;
  bool _isLoading = false;
  bool _isSending = false;
  int _operation = 0;

  List<ChatSummary> get chats => _chats;
  String? get error => _error;
  bool get imageInputEnabled => _imageInputEnabled;
  bool get isLoading => _isLoading;
  bool get isSending => _isSending;
  List<ChatViewMessage> get messages => _messages;
  List<PendingChatImage> get pendingImages => _pendingImages;
  ChatSummary? get selectedChat => _selectedChat;
  bool get supportsImageInput =>
      _imageInputEnabled && (_selectedChat?.supportsImageInput ?? true);
  bool get supportsComposerImageInput =>
      supportsImageInput || (_imageInputEnabled && _imageEditingEnabled);

  bool isDeleting(String chatId) => _deletingChatIds.contains(chatId);

  void clearError() {
    if (_error == null || _disposed) {
      return;
    }
    _error = null;
    _notify();
  }

  Future<void> initialize() async {
    if (_disposed) {
      return;
    }
    final operation = ++_operation;
    await _cancelActiveStream();
    _isLoading = true;
    _error = null;
    _notify();
    try {
      final chatList = await _api.loadChats();
      final chats = chatList.chats;
      if (!_isCurrent(operation)) {
        return;
      }
      _imageInputEnabled = chatList.imageInputEnabled;
      _imageEditingEnabled = chatList.imageEditingEnabled;
      _chats = List<ChatSummary>.unmodifiable(chats);
      if (chats.isEmpty) {
        _selectedChat = null;
        _messages = const <ChatViewMessage>[];
        return;
      }
      final detail = await _api.loadChat(chats.first.id);
      if (_isCurrent(operation)) {
        _applyDetail(detail);
      }
    } catch (error) {
      if (_isCurrent(operation)) {
        _error = _friendlyError(error);
      }
    } finally {
      if (_isCurrent(operation)) {
        _isLoading = false;
        _notify();
      }
    }
  }

  Future<void> prepareToLeave() async {
    if (_disposed) {
      return;
    }
    ++_operation;
    await _cancelActiveStream();
    await _discardPendingImages();
    if (!_disposed) {
      _isSending = false;
      _notify();
    }
  }

  Future<void> retry() => initialize();

  Future<void> selectChat(ChatSummary chat) async {
    if (_disposed || (_selectedChat?.id == chat.id && _messages.isNotEmpty)) {
      return;
    }
    final operation = ++_operation;
    await _cancelActiveStream();
    await _discardPendingImages();
    if (!_isCurrent(operation)) {
      return;
    }
    _isLoading = true;
    _error = null;
    _notify();
    try {
      final detail = await _api.loadChat(chat.id);
      if (_isCurrent(operation)) {
        _applyDetail(detail);
      }
    } catch (error) {
      if (_isCurrent(operation)) {
        _error = _friendlyError(error);
      }
    } finally {
      if (_isCurrent(operation)) {
        _isLoading = false;
        _notify();
      }
    }
  }

  Future<void> openChatById(String chatId) async {
    final normalized = chatId.trim();
    if (_disposed || normalized.isEmpty) {
      return;
    }
    final known = _chats.where((chat) => chat.id == normalized).firstOrNull;
    if (known != null) {
      await selectChat(known);
      return;
    }
    final operation = ++_operation;
    await _cancelActiveStream();
    await _discardPendingImages();
    if (!_isCurrent(operation)) {
      return;
    }
    _isLoading = true;
    _error = null;
    _notify();
    try {
      final detail = await _api.loadChat(normalized);
      if (_isCurrent(operation)) {
        _applyDetail(detail);
      }
    } catch (error) {
      if (_isCurrent(operation)) {
        _error = _friendlyError(error);
      }
    } finally {
      if (_isCurrent(operation)) {
        _isLoading = false;
        _notify();
      }
    }
  }

  Future<void> refreshSelectedChat() async {
    final chat = _selectedChat;
    if (_disposed || _isSending || chat == null) {
      return;
    }
    final operation = ++_operation;
    _error = null;
    try {
      final detail = await _api.loadChat(chat.id);
      if (_isCurrent(operation)) {
        _applyDetail(detail);
      }
    } catch (error) {
      if (_isCurrent(operation)) {
        _error = _friendlyError(error);
      }
    } finally {
      if (_isCurrent(operation)) {
        _notify();
      }
    }
  }

  Future<void> startNewChat() async {
    if (_disposed) {
      return;
    }
    final operation = ++_operation;
    await _cancelActiveStream();
    await _discardPendingImages();
    if (!_isCurrent(operation)) {
      return;
    }
    _selectedChat = null;
    _messages = const <ChatViewMessage>[];
    _error = null;
    _isLoading = false;
    _isSending = false;
    _notify();
  }

  bool addPendingImages(Iterable<PendingChatImage> images) {
    if (_disposed || _isSending || !supportsComposerImageInput) {
      _error = supportsComposerImageInput
          ? _error
          : 'The selected model does not support image input and no image editor is enabled.';
      _notify();
      return false;
    }
    const maximum = maxDraftImages;
    final additions = images.take(maximum - _pendingImages.length);
    final next = <PendingChatImage>[..._pendingImages, ...additions];
    if (next.length == _pendingImages.length) {
      _error = 'This request can include at most $maximum images.';
      _notify();
      return false;
    }
    _pendingImages = List<PendingChatImage>.unmodifiable(next);
    _error = null;
    _notify();
    return true;
  }

  Future<void> removePendingImage(String imageId) async {
    if (_disposed || _isSending) {
      return;
    }
    final image = _pendingImages
        .where((item) => item.id == imageId)
        .firstOrNull;
    if (image == null) {
      return;
    }
    _pendingImages = List<PendingChatImage>.unmodifiable(
      _pendingImages.where((item) => item.id != imageId),
    );
    final chatId = _selectedChat?.id;
    if (chatId != null && image.uploaded != null) {
      try {
        await _api.deleteChatAttachment(
          chatId: chatId,
          attachmentId: image.uploaded!.id,
        );
      } catch (error) {
        _error = _friendlyError(error);
      }
    }
    _notify();
  }

  /// Moves the draft image at [fromIndex] to [toIndex].
  void reorderPendingImage(int fromIndex, int toIndex) {
    if (_disposed ||
        _isSending ||
        fromIndex < 0 ||
        toIndex < 0 ||
        fromIndex == toIndex ||
        fromIndex >= _pendingImages.length ||
        toIndex >= _pendingImages.length) {
      return;
    }
    final reordered = <PendingChatImage>[..._pendingImages];
    reordered.insert(toIndex, reordered.removeAt(fromIndex));
    _pendingImages = List<PendingChatImage>.unmodifiable(reordered);
    _notify();
  }

  Future<Uint8List> loadAttachment(
    String attachmentId, {
    required bool thumbnail,
  }) async {
    final chatId = _selectedChat?.id;
    if (chatId == null) {
      throw StateError('Select a chat before loading an image.');
    }
    final key = '$chatId:$attachmentId:${thumbnail ? 'thumbnail' : 'full'}';
    final cached = _thumbnailCache[key];
    if (thumbnail && cached != null) {
      return cached;
    }
    final bytes = await _api.loadChatAttachment(
      chatId: chatId,
      attachmentId: attachmentId,
      thumbnail: thumbnail,
    );
    if (thumbnail) {
      if (_thumbnailCache.length >= 32) {
        _thumbnailCache.remove(_thumbnailCache.keys.first);
      }
      _thumbnailCache[key] = bytes;
    }
    return bytes;
  }

  Future<bool> sendMessage(String rawText) async {
    final text = rawText.trim();
    final frozenImages = List<PendingChatImage>.of(_pendingImages);
    final clientTurnId = _draftId;
    if (_disposed || _isSending || (text.isEmpty && frozenImages.isEmpty)) {
      return false;
    }
    if (text.length > maxMessageLength) {
      _error = 'Messages can be at most $maxMessageLength characters.';
      _notify();
      return false;
    }

    final operation = ++_operation;
    await _cancelActiveStream();
    if (!_isCurrent(operation)) {
      return false;
    }
    _isSending = true;
    _error = null;
    _notify();

    String? terminalError;
    String? completedMessageId;
    var transientPlaceCards = const <ChatPlaceCard>[];
    try {
      var chat = _selectedChat;
      if (chat == null) {
        chat = await _api.createChat();
        if (!_isCurrent(operation)) {
          return false;
        }
        _selectedChat = chat;
        _chats = List<ChatSummary>.unmodifiable(<ChatSummary>[
          chat,
          ..._chats.where((item) => item.id != chat!.id),
        ]);
      }
      if (frozenImages.isNotEmpty &&
          !(_imageInputEnabled &&
              (chat.supportsImageInput || _imageEditingEnabled))) {
        _error =
            'The selected model does not support image input and no image editor is enabled.';
        return false;
      }

      _pendingImages = List<PendingChatImage>.unmodifiable(
        _pendingImages.map(
          (image) =>
              frozenImages.any(
                (frozen) => frozen.id == image.id && frozen.uploaded == null,
              )
              ? image.copyWith(
                  clearError: true,
                  status: PendingImageStatus.uploading,
                )
              : image,
        ),
      );
      _notify();
      final attachments = await _uploadImages(
        chatId: chat.id,
        clientDraftId: clientTurnId,
        images: frozenImages,
      );

      final userMessageId = 'local-user-${_uuid.v4()}';
      final assistantMessageId = 'local-assistant-${_uuid.v4()}';
      _messages = List<ChatViewMessage>.unmodifiable(<ChatViewMessage>[
        ..._messages,
        ChatViewMessage(
          id: userMessageId,
          role: 'user',
          text: text,
          attachments: <ChatViewAttachment>[
            for (var index = 0; index < frozenImages.length; index += 1)
              ChatViewAttachment(
                id: attachments[index].id,
                localPath: frozenImages[index].path,
                summary: attachments[index],
              ),
          ],
        ),
        ChatViewMessage(
          activity: ChatActivityPresentation.thinking(),
          id: assistantMessageId,
          isStreaming: true,
          role: 'assistant',
          text: '',
          attachments: const <ChatViewAttachment>[],
        ),
      ]);
      _notify();

      final currentLocation = await _currentLocation();
      if (!_isCurrent(operation)) {
        return false;
      }

      final iterator = StreamIterator<ChatStreamEvent>(
        _api.streamChatMessage(
          attachmentIds: attachments.map((item) => item.id).toList(),
          chatId: chat.id,
          clientTurnId: clientTurnId,
          currentLocation: currentLocation,
          text: text,
        ),
      );
      _streamIterator = iterator;
      _draftId = _uuid.v4();
      while (await iterator.moveNext()) {
        if (!_isCurrent(operation) || _streamIterator != iterator) {
          return false;
        }
        final event = iterator.current;
        if (event.kind == 'message_delta') {
          _appendDelta(assistantMessageId, event.delta ?? '');
        } else if (event.kind == 'tool_call_started') {
          _setActivity(
            assistantMessageId,
            event.activity ??
                const ChatActivityPresentation(label: 'Using a tool'),
          );
        } else if (event.kind == 'tool_call_completed' &&
            event.toolName == 'generate_image' &&
            event.attachments.isNotEmpty) {
          _appendAttachments(assistantMessageId, event.attachments);
          _setActivity(assistantMessageId, ChatActivityPresentation.thinking());
        } else if (event.kind == 'tool_call_completed' ||
            event.kind == 'tool_call_failed') {
          _setActivity(assistantMessageId, ChatActivityPresentation.thinking());
        } else if (event.kind == 'message_completed') {
          completedMessageId = event.messageId;
          transientPlaceCards = event.placeCards;
          _setPlaceCards(assistantMessageId, transientPlaceCards);
        } else if (event.kind == 'run_failed') {
          terminalError =
              event.errorMessage ?? 'Voxos could not complete this response.';
        }
      }
      if (!_isCurrent(operation)) {
        return false;
      }
      _streamIterator = null;
      await _reconcile(
        chat.id,
        operation,
        completedMessageId: completedMessageId,
        transientPlaceCards: transientPlaceCards,
      );
      if (!_isCurrent(operation)) {
        return false;
      }
      _error = terminalError;
      if (terminalError == null) {
        _pendingImages = const <PendingChatImage>[];
      }
      return terminalError == null;
    } catch (error) {
      if (_isCurrent(operation)) {
        final message = _friendlyError(error);
        final chatId = _selectedChat?.id;
        if (chatId != null) {
          try {
            await _reconcile(
              chatId,
              operation,
              completedMessageId: completedMessageId,
              transientPlaceCards: transientPlaceCards,
            );
          } on Object {
            // Preserve the original stream error for the user.
          }
        }
        if (_isCurrent(operation)) {
          _error = terminalError ?? message;
        }
      }
      return false;
    } finally {
      if (_isCurrent(operation)) {
        _streamIterator = null;
        _isSending = false;
        _markStreamingComplete();
        _notify();
      }
    }
  }

  Future<void> stopStreaming() async {
    if (_disposed || !_isSending) {
      return;
    }
    final chatId = _selectedChat?.id;
    final operation = ++_operation;
    await _cancelActiveStream();
    if (!_isCurrent(operation)) {
      return;
    }
    _isSending = false;
    _error = 'Response stopped.';
    if (chatId != null) {
      try {
        await _reconcile(chatId, operation);
      } on Object {
        // The explicit stopped state remains useful if reconciliation fails.
      }
    }
    if (_isCurrent(operation)) {
      _notify();
    }
  }

  Future<AgentLocationSnapshot?> _currentLocation() async {
    try {
      return await _locationProvider?.snapshotForAction();
    } on Exception {
      return null;
    }
  }

  Future<bool> deleteChat(String chatId) async {
    if (_disposed || _deletingChatIds.contains(chatId)) {
      return false;
    }
    final operation = ++_operation;
    await _cancelActiveStream();
    if (!_isCurrent(operation)) {
      return false;
    }
    _deletingChatIds.add(chatId);
    _error = null;
    _notify();
    try {
      await _api.deleteChat(chatId);
      if (!_isCurrent(operation)) {
        return false;
      }
      _chats = List<ChatSummary>.unmodifiable(
        _chats.where((chat) => chat.id != chatId),
      );
      if (_selectedChat?.id == chatId) {
        _selectedChat = null;
        _messages = const <ChatViewMessage>[];
        _pendingImages = const <PendingChatImage>[];
        _draftId = _uuid.v4();
      }
      return true;
    } catch (error) {
      if (_isCurrent(operation)) {
        _error = _friendlyError(error);
      }
      return false;
    } finally {
      _deletingChatIds.remove(chatId);
      if (_isCurrent(operation)) {
        _notify();
      }
    }
  }

  Future<List<ChatAttachmentSummary>> _uploadImages({
    required String chatId,
    required String clientDraftId,
    required List<PendingChatImage> images,
  }) async {
    final results = List<ChatAttachmentSummary?>.filled(images.length, null);
    var nextIndex = 0;

    Future<void> worker() async {
      while (nextIndex < images.length) {
        final index = nextIndex;
        nextIndex += 1;
        final image = images[index];
        if (image.uploaded case final uploaded?) {
          results[index] = uploaded;
          continue;
        }
        try {
          final attachment = await _api.uploadChatAttachment(
            chatId: chatId,
            clientDraftId: clientDraftId,
            image: image,
          );
          results[index] = attachment;
          _updatePendingImage(
            image.id,
            image.copyWith(
              clearError: true,
              status: PendingImageStatus.uploaded,
              uploaded: attachment,
            ),
          );
        } catch (error) {
          _updatePendingImage(
            image.id,
            image.copyWith(
              error: _friendlyError(error),
              status: PendingImageStatus.selected,
            ),
          );
          rethrow;
        }
      }
    }

    await Future.wait(
      List<Future<void>>.generate(
        images.length < 2 ? images.length : 2,
        (_) => worker(),
      ),
    );
    return results.cast<ChatAttachmentSummary>();
  }

  void _updatePendingImage(String imageId, PendingChatImage replacement) {
    _pendingImages = List<PendingChatImage>.unmodifiable(
      _pendingImages.map((image) => image.id == imageId ? replacement : image),
    );
    _notify();
  }

  Future<void> _discardPendingImages() async {
    final images = _pendingImages;
    final chatId = _selectedChat?.id;
    _pendingImages = const <PendingChatImage>[];
    _draftId = _uuid.v4();
    if (chatId == null) {
      return;
    }
    await Future.wait(
      images.where((image) => image.uploaded != null).map((image) async {
        try {
          await _api.deleteChatAttachment(
            chatId: chatId,
            attachmentId: image.uploaded!.id,
          );
        } on Object {
          // The server staging TTL remains the cleanup fallback.
        }
      }),
    );
  }

  @override
  void dispose() {
    if (_disposed) {
      return;
    }
    _disposed = true;
    ++_operation;
    final iterator = _streamIterator;
    _streamIterator = null;
    unawaited(iterator?.cancel());
    _api.dispose();
    super.dispose();
  }

  Future<void> _cancelActiveStream() async {
    final iterator = _streamIterator;
    _streamIterator = null;
    if (iterator != null) {
      await iterator.cancel();
    }
  }

  Future<void> _reconcile(
    String chatId,
    int operation, {
    String? completedMessageId,
    List<ChatPlaceCard> transientPlaceCards = const <ChatPlaceCard>[],
  }) async {
    final retainedPlaceCards = <String, List<ChatPlaceCard>>{
      for (final message in _messages)
        if (message.placeCards.isNotEmpty) message.id: message.placeCards,
    };
    if (completedMessageId != null && transientPlaceCards.isNotEmpty) {
      retainedPlaceCards[completedMessageId] = transientPlaceCards;
    }
    final detail = await _api.loadChat(chatId);
    if (!_isCurrent(operation)) {
      return;
    }
    final chatList = await _api.loadChats();
    if (!_isCurrent(operation)) {
      return;
    }
    _imageInputEnabled = chatList.imageInputEnabled;
    _imageEditingEnabled = chatList.imageEditingEnabled;
    _chats = List<ChatSummary>.unmodifiable(chatList.chats);
    _applyDetail(detail);
    _restorePlaceCards(retainedPlaceCards);
  }

  void _appendDelta(String messageId, String delta) {
    if (delta.isEmpty) {
      return;
    }
    _messages = List<ChatViewMessage>.unmodifiable(
      _messages.map(
        (message) => message.id == messageId
            ? message.copyWith(
                clearActivity: true,
                text: '${message.text}$delta',
              )
            : message,
      ),
    );
    _notify();
  }

  void _setActivity(String messageId, ChatActivityPresentation activity) {
    _messages = List<ChatViewMessage>.unmodifiable(
      _messages.map(
        (message) => message.id == messageId
            ? message.copyWith(activity: activity)
            : message,
      ),
    );
    _notify();
  }

  void _setPlaceCards(String messageId, List<ChatPlaceCard> placeCards) {
    _messages = List<ChatViewMessage>.unmodifiable(
      _messages.map(
        (message) => message.id == messageId
            ? message.copyWith(
                placeCards: List<ChatPlaceCard>.unmodifiable(placeCards),
              )
            : message,
      ),
    );
    _notify();
  }

  void _restorePlaceCards(Map<String, List<ChatPlaceCard>> placeCardsById) {
    if (placeCardsById.isEmpty) {
      return;
    }
    _messages = List<ChatViewMessage>.unmodifiable(
      _messages.map((message) {
        final placeCards = placeCardsById[message.id];
        return placeCards == null
            ? message
            : message.copyWith(
                placeCards: List<ChatPlaceCard>.unmodifiable(placeCards),
              );
      }),
    );
  }

  void _appendAttachments(
    String messageId,
    List<ChatAttachmentSummary> attachments,
  ) {
    _messages = List<ChatViewMessage>.unmodifiable(
      _messages.map(
        (message) => message.id == messageId
            ? message.copyWith(
                attachments: attachments
                    .map(ChatViewAttachment.fromSummary)
                    .toList(growable: false),
              )
            : message,
      ),
    );
    _notify();
  }

  void _applyDetail(ChatDetail detail) {
    _selectedChat = detail.chat;
    _chats = List<ChatSummary>.unmodifiable(<ChatSummary>[
      detail.chat,
      ..._chats.where((chat) => chat.id != detail.chat.id),
    ]);
    _messages = List<ChatViewMessage>.unmodifiable(
      detail.messages.map(ChatViewMessage.fromRecord),
    );
  }

  void _markStreamingComplete() {
    _messages = List<ChatViewMessage>.unmodifiable(
      _messages.map(
        (message) => message.isStreaming
            ? message.copyWith(clearActivity: true, isStreaming: false)
            : message,
      ),
    );
  }

  bool _isCurrent(int operation) => !_disposed && operation == _operation;

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  static String _friendlyError(Object error) {
    final message = error.toString().trim();
    if (message.isEmpty) {
      return 'Something went wrong. Please try again.';
    }
    return message;
  }
}
