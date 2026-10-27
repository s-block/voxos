import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/chat/chat_controller.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/location/location_service.dart';

import '../support/fakes.dart';

void main() {
  test(
    'opens a chat by id even when it is not in the loaded summary list',
    () async {
      final chat = fakeChatSummary(id: 'chat-from-push');
      final api = FakeChatApi(
        details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
      );
      final controller = ChatController(api: api);

      await controller.openChatById(chat.id);

      expect(controller.selectedChat?.id, chat.id);
      expect(controller.chats.single.id, chat.id);
      controller.dispose();
    },
  );

  test('initial load selects the newest chat', () async {
    final newest = fakeChatSummary(id: 'chat-new', title: 'Newest');
    final older = fakeChatSummary(id: 'chat-old', title: 'Older');
    final api = FakeChatApi(
      chats: <ChatSummary>[newest, older],
      details: <String, ChatDetail>{
        newest.id: fakeChatDetail(
          newest,
          messages: <ChatMessage>[
            fakeChatMessage(id: 'message-1', role: 'assistant', text: 'Hello'),
          ],
        ),
      },
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);

    await controller.initialize();

    expect(controller.selectedChat?.id, newest.id);
    expect(controller.messages.single.text, 'Hello');
    expect(controller.chats.map((chat) => chat.id), <String>[
      'chat-new',
      'chat-old',
    ]);
  });

  test('refreshes the selected chat without changing the selection', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{
        chat.id: fakeChatDetail(
          chat,
          messages: <ChatMessage>[
            fakeChatMessage(id: 'message-1', role: 'assistant', text: 'First'),
          ],
        ),
      },
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();
    api.details[chat.id] = fakeChatDetail(
      chat,
      messages: <ChatMessage>[
        fakeChatMessage(id: 'message-1', role: 'assistant', text: 'First'),
        fakeChatMessage(id: 'message-2', role: 'assistant', text: 'New'),
      ],
    );

    await controller.refreshSelectedChat();

    expect(controller.selectedChat?.id, chat.id);
    expect(controller.messages.map((message) => message.text), <String>[
      'First',
      'New',
    ]);
  });

  test(
    'first send lazily creates one chat and reconciles streamed text',
    () async {
      final chat = fakeChatSummary(id: 'chat-1');
      final detail = fakeChatDetail(
        chat,
        messages: <ChatMessage>[
          fakeChatMessage(id: 'user-1', role: 'user', text: 'Hello'),
          fakeChatMessage(
            id: 'assistant-1',
            role: 'assistant',
            text: 'Hi there',
          ),
        ],
      );
      final api = FakeChatApi(
        chatToCreate: chat,
        details: <String, ChatDetail>{chat.id: detail},
        streamEvents: const <ChatStreamEvent>[
          ChatStreamEvent(
            kind: 'message_delta',
            payload: <String, Object?>{'delta': 'Hi '},
          ),
          ChatStreamEvent(
            kind: 'message_delta',
            payload: <String, Object?>{'delta': 'there'},
          ),
          ChatStreamEvent(kind: 'run_completed', payload: <String, Object?>{}),
        ],
      );
      final controller = ChatController(api: api);
      addTearDown(controller.dispose);
      await controller.initialize();

      final succeeded = await controller.sendMessage('  Hello  ');

      expect(succeeded, isTrue);
      expect(api.createCount, 1);
      expect(api.sentMessages, <String>['Hello']);
      expect(controller.selectedChat?.id, chat.id);
      expect(controller.messages.map((message) => message.text), <String>[
        'Hello',
        'Hi there',
      ]);
      expect(controller.isSending, isFalse);
    },
  );

  test('blocks duplicate sends while a stream is active', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    final stream = StreamController<ChatStreamEvent>();
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
      streamFactory: (chatId, text) => stream.stream,
    );
    final controller = ChatController(api: api);
    addTearDown(() async {
      controller.dispose();
      await stream.close();
    });
    await controller.initialize();

    final firstSend = controller.sendMessage('First');
    await Future<void>.delayed(Duration.zero);
    final duplicate = await controller.sendMessage('Second');
    stream.add(
      const ChatStreamEvent(
        kind: 'message_delta',
        payload: <String, Object?>{'delta': 'Done'},
      ),
    );
    await stream.close();
    await firstSend;

    expect(duplicate, isFalse);
    expect(api.sentMessages, <String>['First']);
  });

  test(
    'adds a current location to a text send when sharing is enabled',
    () async {
      final chat = fakeChatSummary(id: 'chat-1');
      final location = AgentLocationSnapshot(
        capturedAt: DateTime.utc(2026, 8, 30, 12),
        horizontalAccuracyM: 25,
        latitude: 42.4304,
        longitude: 19.2594,
      );
      final api = FakeChatApi(
        chats: <ChatSummary>[chat],
        details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
      );
      final controller = ChatController(
        api: api,
        locationProvider: _StaticLocationProvider(location),
      );
      addTearDown(controller.dispose);
      await controller.initialize();

      await controller.sendMessage('Restaurants near me');

      expect(api.sentLocations.single, same(location));
    },
  );

  test('updates the waiting response from streamed tool activity', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    final stream = StreamController<ChatStreamEvent>();
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
      streamFactory: (chatId, text) => stream.stream,
    );
    final controller = ChatController(api: api);
    addTearDown(() async {
      controller.dispose();
      await stream.close();
    });
    await controller.initialize();

    final send = controller.sendMessage('Find the latest release');
    await Future<void>.delayed(Duration.zero);
    expect(controller.messages.last.activity?.label, 'Thinking…');

    stream.add(
      const ChatStreamEvent(
        kind: 'tool_call_started',
        payload: <String, Object?>{
          'name': 'web_search_exa',
          'activity': <String, Object?>{
            'label': 'Searching the web',
            'detail': 'Exa',
            'icon': 'web',
          },
        },
      ),
    );
    await Future<void>.delayed(Duration.zero);
    expect(controller.messages.last.activity?.label, 'Searching the web');
    expect(controller.messages.last.activity?.detail, 'Exa');
    expect(controller.messages.last.activity?.icon, ChatActivityIcon.web);

    stream.add(
      const ChatStreamEvent(
        kind: 'tool_call_completed',
        payload: <String, Object?>{'name': 'web_search_exa'},
      ),
    );
    await Future<void>.delayed(Duration.zero);
    expect(controller.messages.last.activity?.label, 'Thinking…');

    stream.add(
      const ChatStreamEvent(
        kind: 'message_delta',
        payload: <String, Object?>{'delta': 'Here is the release.'},
      ),
    );
    await Future<void>.delayed(Duration.zero);
    expect(controller.messages.last.activity, isNull);
    await stream.close();
    await send;
  });

  test(
    'run_failed becomes a visible failure and drops unsaved optimism',
    () async {
      final chat = fakeChatSummary(id: 'chat-1');
      final api = FakeChatApi(
        chatToCreate: chat,
        details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
        streamEvents: const <ChatStreamEvent>[
          ChatStreamEvent(
            kind: 'run_failed',
            payload: <String, Object?>{
              'error_message': 'Finish text provider setup.',
            },
          ),
        ],
      );
      final controller = ChatController(api: api);
      addTearDown(controller.dispose);
      await controller.initialize();

      final succeeded = await controller.sendMessage('Hello');

      expect(succeeded, isFalse);
      expect(controller.error, 'Finish text provider setup.');
      expect(controller.messages, isEmpty);
      expect(controller.isSending, isFalse);
    },
  );

  test('uses a fresh client turn id after a failed stream', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    var streamAttempt = 0;
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
      streamFactory: (chatId, text) {
        streamAttempt += 1;
        if (streamAttempt == 1) {
          return Stream<ChatStreamEvent>.error(
            StateError('The first request failed.'),
          );
        }
        return Stream<ChatStreamEvent>.value(
          const ChatStreamEvent(
            kind: 'run_completed',
            payload: <String, Object?>{},
          ),
        );
      },
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();

    final firstSucceeded = await controller.sendMessage('First');
    final secondSucceeded = await controller.sendMessage('Second');

    expect(firstSucceeded, isFalse);
    expect(secondSucceeded, isTrue);
    expect(api.sentClientTurnIds, hasLength(2));
    expect(api.sentClientTurnIds.first, isNotNull);
    expect(api.sentClientTurnIds.last, isNot(api.sentClientTurnIds.first));
  });

  test(
    'stop cancels the active stream and reconciles durable messages',
    () async {
      final chat = fakeChatSummary(id: 'chat-1');
      final stream = StreamController<ChatStreamEvent>();
      final api = FakeChatApi(
        chats: <ChatSummary>[chat],
        details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
        streamFactory: (chatId, text) => stream.stream,
      );
      final controller = ChatController(api: api);
      addTearDown(() async {
        controller.dispose();
        await stream.close();
      });
      await controller.initialize();
      final send = controller.sendMessage('Stop me');
      await Future<void>.delayed(Duration.zero);

      await controller.stopStreaming();
      await send;

      expect(controller.isSending, isFalse);
      expect(controller.error, 'Response stopped.');
      expect(controller.messages, isEmpty);
    },
  );

  test('stop during location capture prevents the stream request', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    final location = Completer<AgentLocationSnapshot?>();
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
    );
    final controller = ChatController(
      api: api,
      locationProvider: _PendingLocationProvider(location.future),
    );
    addTearDown(controller.dispose);
    await controller.initialize();

    final send = controller.sendMessage('Stop before sending');
    await Future<void>.delayed(Duration.zero);
    expect(controller.isSending, isTrue);

    await controller.stopStreaming();
    location.complete(null);

    expect(await send, isFalse);
    expect(api.streamCount, 0);
    expect(controller.isSending, isFalse);
    expect(controller.error, 'Response stopped.');
    expect(controller.messages, isEmpty);
  });

  test('sends reordered text and images as one stream request', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    final api = FakeChatApi(
      chatToCreate: chat,
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
      streamEvents: const <ChatStreamEvent>[
        ChatStreamEvent(kind: 'run_completed', payload: <String, Object?>{}),
      ],
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();
    controller.addPendingImages(<PendingChatImage>[
      pendingImage(id: 'one', name: 'one.jpg'),
      pendingImage(id: 'two', name: 'two.png', mimeType: 'image/png'),
    ]);
    controller.reorderPendingImage(1, 0);

    final sent = await controller.sendMessage('Compare these');

    expect(sent, isTrue);
    expect(api.uploadedImageNames, <String>['two.png', 'one.jpg']);
    expect(api.streamCount, 1);
    expect(api.sentMessages, <String>['Compare these']);
    expect(api.sentAttachmentIds.single, <String>[
      'attachment-1',
      'attachment-2',
    ]);
    expect(controller.pendingImages, isEmpty);
  });

  test('accepts another image draft after a completed image turn', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    final api = FakeChatApi(
      chatToCreate: chat,
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
      streamEvents: const <ChatStreamEvent>[
        ChatStreamEvent(kind: 'run_completed', payload: <String, Object?>{}),
      ],
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();
    controller.addPendingImages(<PendingChatImage>[
      pendingImage(id: 'first', name: 'first.jpg'),
    ]);

    final firstSent = await controller.sendMessage('First image');
    final secondAdded = controller.addPendingImages(<PendingChatImage>[
      pendingImage(id: 'second', name: 'second.jpg'),
    ]);

    expect(firstSent, isTrue);
    expect(controller.supportsImageInput, isTrue);
    expect(secondAdded, isTrue);
    expect(controller.pendingImages.single.name, 'second.jpg');
  });

  test(
    'partial image upload failure preserves the draft and sends no stream',
    () async {
      final chat = fakeChatSummary(id: 'chat-1');
      final api = FakeChatApi(
        chatToCreate: chat,
        details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
        failUploadAt: 2,
        uploadError: StateError('Upload failed'),
      );
      final controller = ChatController(api: api);
      addTearDown(controller.dispose);
      await controller.initialize();
      controller.addPendingImages(<PendingChatImage>[
        pendingImage(id: 'one', name: 'one.jpg'),
        pendingImage(id: 'two', name: 'two.jpg'),
      ]);

      final sent = await controller.sendMessage('');

      expect(sent, isFalse);
      expect(api.streamCount, 0);
      expect(controller.pendingImages, hasLength(2));
      expect(
        controller.pendingImages.first.status,
        PendingImageStatus.uploaded,
      );
      expect(controller.pendingImages.last.status, PendingImageStatus.selected);
      expect(controller.error, contains('Upload failed'));
    },
  );

  test(
    'keeps streamed Google Maps cards in memory after reconciliation',
    () async {
      final chat = fakeChatSummary(id: 'chat-1');
      final detail = fakeChatDetail(
        chat,
        messages: <ChatMessage>[
          fakeChatMessage(
            id: 'user-1',
            role: 'user',
            text: 'Restaurants near me',
          ),
          fakeChatMessage(
            id: 'assistant-1',
            role: 'assistant',
            text: 'Try Konoba Stari Grad.',
          ),
        ],
      );
      final api = FakeChatApi(
        chats: <ChatSummary>[chat],
        details: <String, ChatDetail>{chat.id: detail},
        streamEvents: const <ChatStreamEvent>[
          ChatStreamEvent(
            kind: 'message_completed',
            payload: <String, Object?>{
              'message_id': 'assistant-1',
              'text': 'Try Konoba Stari Grad.',
              'place_cards': <Object?>[
                <String, Object?>{
                  'provider': 'google_maps',
                  'place_id': 'ChIJ123',
                  'title': 'Konoba Stari Grad',
                  'place_url':
                      'https://www.google.com/maps/place/?q=place_id:ChIJ123',
                  'attribution_url':
                      'https://www.google.com/maps/place/?q=place_id:ChIJ123',
                  'directions_url': null,
                  'latitude': null,
                  'longitude': null,
                  'position': 0,
                },
              ],
            },
          ),
          ChatStreamEvent(kind: 'run_completed', payload: <String, Object?>{}),
        ],
      );
      final controller = ChatController(api: api);
      addTearDown(controller.dispose);
      await controller.initialize();

      final sent = await controller.sendMessage('Restaurants near me');

      expect(sent, isTrue);
      expect(controller.messages.last.placeCards.single.placeId, 'ChIJ123');

      await controller.refreshSelectedChat();

      expect(controller.messages.last.placeCards, isEmpty);
    },
  );

  test(
    'preserves prior cards and correlates new cards by durable message id',
    () async {
      final chat = fakeChatSummary(id: 'chat-1');
      late FakeChatApi api;
      api = FakeChatApi(
        chats: <ChatSummary>[chat],
        details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
        streamFactory: (_, text) {
          if (text == 'First search') {
            api.details[chat.id] = fakeChatDetail(
              chat,
              messages: <ChatMessage>[
                fakeChatMessage(id: 'user-1', role: 'user', text: text),
                fakeChatMessage(
                  id: 'assistant-1',
                  role: 'assistant',
                  text: 'Try First Place.',
                ),
              ],
            );
            return Stream<ChatStreamEvent>.fromIterable(<ChatStreamEvent>[
              placeCompletedEvent(
                messageId: 'assistant-1',
                placeId: 'place-1',
                title: 'First Place',
              ),
            ]);
          }
          api.details[chat.id] = fakeChatDetail(
            chat,
            messages: <ChatMessage>[
              fakeChatMessage(id: 'user-1', role: 'user', text: 'First search'),
              fakeChatMessage(
                id: 'assistant-1',
                role: 'assistant',
                text: 'Try First Place.',
              ),
              fakeChatMessage(id: 'user-2', role: 'user', text: text),
              fakeChatMessage(
                id: 'assistant-2',
                role: 'assistant',
                text: 'Try Second Place.',
              ),
              fakeChatMessage(
                id: 'assistant-notification',
                role: 'assistant',
                text: 'A delegated task also completed.',
              ),
            ],
          );
          return Stream<ChatStreamEvent>.fromIterable(<ChatStreamEvent>[
            placeCompletedEvent(
              messageId: 'assistant-2',
              placeId: 'place-2',
              title: 'Second Place',
            ),
          ]);
        },
      );
      final controller = ChatController(api: api);
      addTearDown(controller.dispose);
      await controller.initialize();

      expect(await controller.sendMessage('First search'), isTrue);
      expect(await controller.sendMessage('Second search'), isTrue);

      final byId = <String, ChatViewMessage>{
        for (final message in controller.messages) message.id: message,
      };
      expect(byId['assistant-1']!.placeCards.single.placeId, 'place-1');
      expect(byId['assistant-2']!.placeCards.single.placeId, 'place-2');
      expect(byId['assistant-notification']!.placeCards, isEmpty);
    },
  );

  test('text-only model rejects pending images before upload', () async {
    final chat = fakeChatSummary(id: 'chat-1', supportsImageInput: false);
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();

    final added = controller.addPendingImages(<PendingChatImage>[
      pendingImage(id: 'one', name: 'one.jpg'),
    ]);

    expect(added, isFalse);
    expect(controller.pendingImages, isEmpty);
    expect(controller.error, contains('does not support image input'));
  });

  test('image rollout flag rejects selection before a chat exists', () async {
    final api = FakeChatApi(imageInputEnabled: false);
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();

    final added = controller.addPendingImages(<PendingChatImage>[
      pendingImage(id: 'one', name: 'one.jpg'),
    ]);

    expect(added, isFalse);
    expect(controller.supportsImageInput, isFalse);
    expect(controller.pendingImages, isEmpty);
  });

  test('normal chat generates and reconciles an assistant image', () async {
    final chat = fakeChatSummary(id: 'chat-1');
    final userMessage = fakeChatMessage(
      id: 'image-user-1',
      role: 'user',
      text: 'A tiny library on the moon',
    );
    final assistantMessage = ChatMessage(
      createdAt: DateTime.utc(2026, 7, 20, 10),
      id: 'image-assistant-1',
      role: 'assistant',
      source: 'text_output',
      text: 'Generated with FLUX.2 Klein 4B via NVIDIA API Catalog.',
      attachments: const <ChatAttachmentSummary>[
        ChatAttachmentSummary(
          byteSize: 2048,
          height: 1024,
          id: 'generated-1',
          mimeType: 'image/jpeg',
          position: 0,
          width: 1024,
        ),
      ],
    );
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{
        chat.id: fakeChatDetail(
          chat,
          messages: <ChatMessage>[userMessage, assistantMessage],
        ),
      },
      streamEvents: const <ChatStreamEvent>[
        ChatStreamEvent(
          kind: 'tool_call_completed',
          payload: <String, Object?>{
            'name': 'generate_image',
            'attachments': <Object?>[
              <String, Object?>{
                'byte_size': 2048,
                'height': 1024,
                'id': 'generated-1',
                'mime_type': 'image/jpeg',
                'position': 0,
                'width': 1024,
              },
            ],
          },
        ),
        ChatStreamEvent(kind: 'run_completed', payload: <String, Object?>{}),
      ],
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();

    final generated = await controller.sendMessage(
      'A tiny library on the moon',
    );

    expect(generated, isTrue);
    expect(api.sentMessages, <String>['A tiny library on the moon']);
    expect(controller.messages.last.attachments.single.id, 'generated-1');
  });

  test(
    'text-only chat accepts images when an image editor is enabled',
    () async {
      final chat = fakeChatSummary(id: 'chat-1', supportsImageInput: false);
      final api = FakeChatApi(
        chats: <ChatSummary>[chat],
        details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
        imageEditingEnabled: true,
        streamEvents: const <ChatStreamEvent>[
          ChatStreamEvent(kind: 'run_completed', payload: <String, Object?>{}),
        ],
      );
      final controller = ChatController(api: api);
      addTearDown(controller.dispose);
      await controller.initialize();

      final added = controller.addPendingImages(<PendingChatImage>[
        pendingImage(id: 'reference', name: 'reference.jpg'),
      ]);
      final sent = await controller.sendMessage('Make the sky golden');

      expect(added, isTrue);
      expect(sent, isTrue);
      expect(api.sentMessages, <String>['Make the sky golden']);
      expect(api.sentAttachmentIds.single, <String>['attachment-1']);
    },
  );

  test('delete clears the selected chat only after success', () async {
    final selected = fakeChatSummary(id: 'chat-1', title: 'Selected');
    final other = fakeChatSummary(id: 'chat-2', title: 'Other');
    final api = FakeChatApi(
      chats: <ChatSummary>[selected, other],
      details: <String, ChatDetail>{selected.id: fakeChatDetail(selected)},
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();

    final deleted = await controller.deleteChat(selected.id);

    expect(deleted, isTrue);
    expect(controller.selectedChat, isNull);
    expect(controller.chats.single.id, other.id);
  });

  test('failed delete retains history and reports the error', () async {
    final chat = fakeChatSummary(id: 'chat-1', title: 'Keep me');
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      deleteError: StateError('Delete failed'),
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
    );
    final controller = ChatController(api: api);
    addTearDown(controller.dispose);
    await controller.initialize();

    final deleted = await controller.deleteChat(chat.id);

    expect(deleted, isFalse);
    expect(controller.chats.single.id, chat.id);
    expect(controller.error, contains('Delete failed'));
  });
}

final class _StaticLocationProvider implements CurrentLocationProvider {
  const _StaticLocationProvider(this.location);

  final AgentLocationSnapshot location;

  @override
  Future<AgentLocationSnapshot?> snapshotForAction() async => location;
}

final class _PendingLocationProvider implements CurrentLocationProvider {
  const _PendingLocationProvider(this.location);

  final Future<AgentLocationSnapshot?> location;

  @override
  Future<AgentLocationSnapshot?> snapshotForAction() => location;
}

PendingChatImage pendingImage({
  required String id,
  required String name,
  String mimeType = 'image/jpeg',
}) {
  return PendingChatImage(
    byteSize: 1024,
    id: id,
    mimeType: mimeType,
    name: name,
    path: '/tmp/$name',
  );
}

ChatStreamEvent placeCompletedEvent({
  required String messageId,
  required String placeId,
  required String title,
}) {
  final placeUri = 'https://www.google.com/maps/place/$placeId';
  return ChatStreamEvent(
    kind: 'message_completed',
    payload: <String, Object?>{
      'message_id': messageId,
      'text': 'Try $title.',
      'place_cards': <Object?>[
        <String, Object?>{
          'provider': 'google_maps',
          'place_id': placeId,
          'title': title,
          'place_url': placeUri,
          'attribution_url': placeUri,
          'directions_url': null,
          'latitude': null,
          'longitude': null,
          'position': 0,
        },
      ],
    },
  );
}
