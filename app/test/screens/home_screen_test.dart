import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/api/voxos_api_client.dart';
import 'package:voxos/src/chat/chat_controller.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/media/clipboard_image_service.dart';
import 'package:voxos/src/media/gallery_service.dart';
import 'package:voxos/src/notifications/push_api.dart';
import 'package:voxos/src/notifications/push_controller.dart';
import 'package:voxos/src/notifications/push_message.dart';
import 'package:voxos/src/notifications/push_messaging.dart';
import 'package:voxos/src/screens/home_screen.dart';
import 'package:voxos/src/theme/voxos_theme.dart';
import 'package:voxos/src/voice/voice_controller.dart';

import '../support/config_fixtures.dart';
import '../support/fakes.dart';

void main() {
  testWidgets('the account menu exposes model default settings', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final controller = ChatController(api: FakeChatApi());

    await tester.pumpWidget(_app(auth: auth, chatController: controller));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('open-chat-history')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('account-tile')));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey<String>('open-model-settings')),
      findsOneWidget,
    );

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('an empty conversation invites typing or talking', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final controller = ChatController(api: FakeChatApi());

    await tester.pumpWidget(_app(auth: auth, chatController: controller));
    await tester.pumpAndSettle();

    expect(find.text('How can I help?'), findsOneWidget);
    expect(find.byKey(const ValueKey<String>('chat-composer')), findsOneWidget);
    expect(
      find.byKey(const ValueKey<String>('start-voice-call')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey<String>('start-voice-call-empty')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey<String>('chat-composer-mode')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey<String>('image-composer-mode')),
      findsNothing,
    );

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets(
    'a foreground message refreshes the open chat without notifying',
    (tester) async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final auth = FakeAuthSession();
      final chat = fakeChatSummary(id: 'chat-1');
      final api = FakeChatApi(
        chats: <ChatSummary>[chat],
        details: <String, ChatDetail>{
          chat.id: fakeChatDetail(
            chat,
            messages: <ChatMessage>[
              fakeChatMessage(
                id: 'message-1',
                role: 'assistant',
                text: 'Existing',
              ),
            ],
          ),
        },
      );
      final messaging = _HomeTestPushMessaging();
      final push = PushController(
        api: _HomeTestPushApi(),
        available: true,
        installations: _HomeTestInstallations(),
        messaging: messaging,
        platform: 'ios',
        preferences: await SharedPreferences.getInstance(),
        serverUri: Uri.parse('https://voxos.example.com'),
        userId: 'user-123',
      );
      await push.initialize();
      final controller = ChatController(api: api);

      await tester.pumpWidget(
        _app(auth: auth, chatController: controller, pushController: push),
      );
      await tester.pumpAndSettle();
      api.details[chat.id] = fakeChatDetail(
        chat,
        messages: <ChatMessage>[
          fakeChatMessage(id: 'message-1', role: 'assistant', text: 'Existing'),
          fakeChatMessage(
            id: 'message-2',
            role: 'assistant',
            text: 'Arrived quietly',
          ),
        ],
      );

      messaging.foreground.add(
        const PushEnvelope(
          body: 'Open VoxOS to view the message.',
          data: <String, Object?>{
            'schema_version': '1',
            'type': 'chat_message',
            'notification_id': 'message:message-2',
            'message_id': 'message-2',
            'chat_id': 'chat-1',
          },
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Arrived quietly'), findsOneWidget);
      expect(find.byType(SnackBar), findsNothing);

      await tester.pumpWidget(const SizedBox.shrink());
      push.dispose();
      await messaging.close();
      await auth.dispose();
    },
  );

  testWidgets('the composer voice button opens a live call', (tester) async {
    final auth = FakeAuthSession();
    final transports = <FakeVoiceTransport>[];
    final voice = _voiceController(auth: auth, transports: transports);

    await tester.pumpWidget(
      _app(
        auth: auth,
        chatController: ChatController(api: FakeChatApi()),
        voiceController: voice,
      ),
    );
    await tester.pumpAndSettle();

    // The orb animates forever, so the call page is pumped by hand.
    await tester.tap(find.byKey(const ValueKey<String>('start-voice-call')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pump(const Duration(milliseconds: 50));

    expect(transports, hasLength(1));
    expect(transports.single.microphoneEnabled, isTrue);
    expect(find.text('Listening…'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey<String>('end-call-control')));
    // Cancelling the transport subscription needs the real event loop.
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 10)),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));

    expect(transports.single.disposeCount, 1);
    expect(find.byKey(const ValueKey<String>('chat-composer')), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    await auth.dispose();
    await transports.single.closeEvents();
  });

  testWidgets('composer sends and renders the durable transcript', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final chat = fakeChatSummary(id: 'chat-1');
    final api = FakeChatApi(
      chatToCreate: chat,
      details: <String, ChatDetail>{
        chat.id: fakeChatDetail(
          chat,
          messages: <ChatMessage>[
            fakeChatMessage(id: 'user-1', role: 'user', text: 'Hello'),
            fakeChatMessage(id: 'assistant-1', role: 'assistant', text: 'Hi!'),
          ],
        ),
      },
      streamEvents: const <ChatStreamEvent>[
        ChatStreamEvent(
          kind: 'message_delta',
          payload: <String, Object?>{'delta': 'Hi!'},
        ),
        ChatStreamEvent(kind: 'run_completed', payload: <String, Object?>{}),
      ],
    );
    final controller = ChatController(api: api);

    await tester.pumpWidget(_app(auth: auth, chatController: controller));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey<String>('chat-composer')),
      'Hello',
    );
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey<String>('send-chat-message')));
    await tester.pumpAndSettle();

    expect(find.text('Hello'), findsOneWidget);
    expect(find.text('Hi!'), findsOneWidget);
    expect(api.createCount, 1);
    expect(api.sentMessages, <String>['Hello']);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('assistant replies render markdown and do not fetch images', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final chat = fakeChatSummary(id: 'chat-1');
    const assistantReply = '''
## Flight status

**BA751** is still in progress.

- Departed around `15:00Z`
- Has not landed

![Route map](https://example.com/tracking-pixel.png)
''';
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{
        chat.id: fakeChatDetail(
          chat,
          messages: <ChatMessage>[
            fakeChatMessage(
              id: 'user-1',
              role: 'user',
              text: '**show this literally**',
            ),
            fakeChatMessage(
              id: 'assistant-1',
              role: 'assistant',
              text: assistantReply,
            ),
          ],
        ),
      },
    );
    final controller = ChatController(api: api);

    await tester.pumpWidget(_app(auth: auth, chatController: controller));
    await tester.pumpAndSettle();

    expect(find.byType(MarkdownBody), findsOneWidget);
    expect(find.text('**show this literally**'), findsOneWidget);
    expect(find.text('Flight status'), findsOneWidget);
    expect(find.text('BA751 is still in progress.'), findsOneWidget);
    expect(find.text('Departed around 15:00Z'), findsOneWidget);
    expect(find.text('[Route map]'), findsOneWidget);
    expect(find.byType(Image), findsNothing);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('generated assistant images can be saved to the gallery', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final gallery = FakeGalleryService();
    final chat = fakeChatSummary(id: 'chat-1');
    final imageBytes = base64Decode(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=',
    );
    final api = FakeChatApi(
      attachmentBytes: imageBytes,
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{
        chat.id: fakeChatDetail(
          chat,
          messages: <ChatMessage>[
            ChatMessage(
              attachments: const <ChatAttachmentSummary>[
                ChatAttachmentSummary(
                  byteSize: 68,
                  height: 1,
                  id: 'generated-image-1',
                  mimeType: 'image/png',
                  position: 0,
                  width: 1,
                ),
              ],
              createdAt: DateTime.utc(2026, 8, 17),
              id: 'assistant-image-1',
              role: 'assistant',
              source: 'text_output',
              text: 'Generated with FLUX.2 Klein 4B via NVIDIA API Catalog.',
            ),
          ],
        ),
      },
    );

    await tester.pumpWidget(
      _app(
        auth: auth,
        chatController: ChatController(api: api),
        galleryService: gallery,
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.bySemanticsLabel('Open attached image 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('save-chat-image')));
    await tester.pumpAndSettle();

    expect(gallery.saveCount, 1);
    expect(gallery.savedName, 'voxos-generatedimage1');
    expect(gallery.savedBytes, imageBytes);
    expect(find.text('Image saved to Photos.'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('history opens in the drawer, filters, and deletes on swipe', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final chat = fakeChatSummary(id: 'chat-1', title: 'Delete me');
    final api = FakeChatApi(
      chats: <ChatSummary>[chat],
      details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
    );
    final controller = ChatController(api: api);

    await tester.pumpWidget(_app(auth: auth, chatController: controller));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('open-chat-history')));
    await tester.pumpAndSettle();

    expect(find.text('Delete me'), findsWidgets);
    expect(find.byTooltip('Delete Delete me'), findsOneWidget);

    await tester.enterText(
      find.byKey(const ValueKey<String>('chat-search')),
      'nothing here',
    );
    await tester.pumpAndSettle();
    expect(find.text('No chats match that search.'), findsOneWidget);

    await tester.enterText(
      find.byKey(const ValueKey<String>('chat-search')),
      'delete',
    );
    await tester.pumpAndSettle();

    await tester.drag(
      find.byKey(const ValueKey<String>('history-chat-1')),
      const Offset(-500, 0),
    );
    await tester.pumpAndSettle();

    expect(find.text('Delete this chat?'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await tester.pumpAndSettle();

    expect(api.deleteCount, 1);
    expect(find.text('No chats match that search.'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('opens server selection from the signed-in account menu', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    var changeCount = 0;

    await tester.pumpWidget(
      _app(
        auth: auth,
        chatController: ChatController(api: FakeChatApi()),
        onChangeServer: () async => changeCount += 1,
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('open-chat-history')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('account-tile')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Change server'));
    await tester.pumpAndSettle();

    expect(changeCount, 1);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('chat remains usable on a narrow dark screen with large text', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(320, 700));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final auth = FakeAuthSession();
    final controller = ChatController(api: FakeChatApi());

    await tester.pumpWidget(
      _app(
        auth: auth,
        chatController: controller,
        textScaler: const TextScaler.linear(1.6),
        theme: VoxosTheme.dark(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey<String>('chat-composer')), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets(
    'image action supports editing when the selected model is text only',
    (tester) async {
      final auth = FakeAuthSession();
      final chat = fakeChatSummary(
        id: 'chat-text-only',
        supportsImageInput: false,
      );
      final controller = ChatController(
        api: FakeChatApi(
          chats: <ChatSummary>[chat],
          details: <String, ChatDetail>{chat.id: fakeChatDetail(chat)},
          imageEditingEnabled: true,
        ),
      );

      await tester.pumpWidget(_app(auth: auth, chatController: controller));
      await tester.pumpAndSettle();

      final addImages = find.byKey(const ValueKey<String>('add-chat-images'));
      expect(addImages, findsOneWidget);
      expect(find.byTooltip('Add images'), findsOneWidget);
      expect(tester.widget<IconButton>(addImages).onPressed, isNotNull);

      await tester.pumpWidget(const SizedBox.shrink());
      await auth.dispose();
    },
  );

  testWidgets('image action offers camera, library, and clipboard paste', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final clipboard = _FakeClipboardImageService(null);

    await tester.pumpWidget(
      _app(
        auth: auth,
        chatController: ChatController(api: FakeChatApi()),
        clipboardImageService: clipboard,
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('add-chat-images')));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey<String>('take-chat-photo')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey<String>('choose-chat-images')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey<String>('paste-chat-image')),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey<String>('paste-chat-image')));
    await tester.pumpAndSettle();

    expect(clipboard.readCount, 1);
    expect(find.text('Copy an image first, then try again.'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('composer long-press menu can paste an image', (tester) async {
    final auth = FakeAuthSession();
    final clipboard = _FakeClipboardImageService(null);

    await tester.pumpWidget(
      _app(
        auth: auth,
        chatController: ChatController(api: FakeChatApi()),
        clipboardImageService: clipboard,
      ),
    );
    await tester.pumpAndSettle();
    final composer = find.byKey(const ValueKey<String>('chat-composer'));
    await tester.enterText(composer, 'Draft');
    await tester.longPress(composer);
    await tester.pumpAndSettle();

    expect(find.text('Paste image'), findsOneWidget);
    await tester.tap(find.text('Paste image'));
    await tester.pumpAndSettle();

    expect(clipboard.readCount, 1);
    expect(find.text('Copy an image first, then try again.'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('pasted images enter the draft and are deleted when removed', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final imageBytes = base64Decode(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=',
    );
    final clipboard = _FakeClipboardImageService(
      ClipboardImage(bytes: imageBytes, mimeType: 'image/png'),
    );
    final temporaryFile = File(
      '${Directory.systemTemp.path}/voxos-paste-widget-test-'
      '${DateTime.now().microsecondsSinceEpoch}.png',
    );
    await tester.runAsync(() => temporaryFile.writeAsBytes(imageBytes));
    addTearDown(() async {
      if (await temporaryFile.exists()) {
        await temporaryFile.delete();
      }
    });
    final store = _FakeClipboardImageStore(temporaryFile.path);
    final controller = ChatController(api: FakeChatApi());

    await tester.pumpWidget(
      _app(
        auth: auth,
        chatController: controller,
        clipboardImageService: clipboard,
        clipboardImageStore: store,
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('add-chat-images')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey<String>('paste-chat-image')));
    await tester.pumpAndSettle();

    expect(clipboard.readCount, 1);
    expect(store.saveCount, 1);
    expect(controller.pendingImages, hasLength(1));
    expect(controller.pendingImages.single.name, 'Pasted image.png');
    expect(controller.pendingImages.single.path, temporaryFile.path);

    await tester.tap(find.byTooltip('Remove Pasted image.png'));
    await tester.pumpAndSettle();

    expect(controller.pendingImages, isEmpty);
    expect(store.deletedPaths, <String>[temporaryFile.path]);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });

  testWidgets('image action remains hidden when the rollout is disabled', (
    tester,
  ) async {
    final auth = FakeAuthSession();
    final controller = ChatController(
      api: FakeChatApi(imageInputEnabled: false),
    );

    await tester.pumpWidget(_app(auth: auth, chatController: controller));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey<String>('add-chat-images')), findsNothing);

    await tester.pumpWidget(const SizedBox.shrink());
    await auth.dispose();
  });
}

Widget _app({
  required FakeAuthSession auth,
  required ChatController chatController,
  ClipboardImageService clipboardImageService =
      const PlatformClipboardImageService(),
  ClipboardImageStore clipboardImageStore =
      const TemporaryClipboardImageStore(),
  GalleryService galleryService = const SystemGalleryService(),
  Future<void> Function()? onChangeServer,
  PushController? pushController,
  TextScaler textScaler = TextScaler.noScaling,
  ThemeData? theme,
  VoiceController? voiceController,
}) {
  return MaterialApp(
    builder: (context, child) {
      return MediaQuery(
        data: MediaQuery.of(context).copyWith(textScaler: textScaler),
        child: child!,
      );
    },
    theme: theme ?? VoxosTheme.light(),
    home: HomeScreen(
      auth: auth,
      chatController: chatController,
      clipboardImageService: clipboardImageService,
      clipboardImageStore: clipboardImageStore,
      connection: _connection(),
      galleryService: galleryService,
      onChangeServer: onChangeServer ?? () async {},
      onSignOut: () async {},
      pushController: pushController,
      user: auth.currentUser!,
      voiceController: voiceController,
    ),
  );
}

final class _FakeClipboardImageService implements ClipboardImageService {
  _FakeClipboardImageService(this.image);

  final ClipboardImage? image;
  int readCount = 0;

  @override
  Future<ClipboardImage?> readImage() async {
    readCount += 1;
    return image;
  }
}

final class _FakeClipboardImageStore implements ClipboardImageStore {
  _FakeClipboardImageStore(this.path);

  final List<String> deletedPaths = <String>[];
  final String path;
  int saveCount = 0;

  @override
  Future<void> delete(String path) async {
    deletedPaths.add(path);
  }

  @override
  Future<String> save(ClipboardImage image, {required String id}) async {
    saveCount += 1;
    return path;
  }
}

final class _HomeTestPushApi implements PushApi {
  @override
  void dispose() {}

  @override
  Future<void> registerInstallation({
    required String installationId,
    required String platform,
    required String registrationToken,
  }) async {}

  @override
  Future<void> revokeInstallation({required String installationId}) async {}
}

final class _HomeTestInstallations implements PushInstallationIdProvider {
  @override
  Future<String> installationId() async => 'fid-123';
}

final class _HomeTestPushMessaging implements PushMessaging {
  final StreamController<PushEnvelope> foreground =
      StreamController<PushEnvelope>.broadcast();
  final StreamController<PushEnvelope> _opened =
      StreamController<PushEnvelope>.broadcast();
  final StreamController<String> _tokens = StreamController<String>.broadcast();

  Future<void> close() async {
    await foreground.close();
    await _opened.close();
    await _tokens.close();
  }

  @override
  Future<PushAuthorizationStatus> authorizationStatus() async =>
      PushAuthorizationStatus.notDetermined;

  @override
  Future<void> deleteToken() async {}

  @override
  Stream<PushEnvelope> get foregroundMessages => foreground.stream;

  @override
  Future<PushEnvelope?> initialMessage() async => null;

  @override
  Stream<PushEnvelope> get openedMessages => _opened.stream;

  @override
  Future<PushAuthorizationStatus> requestAuthorization() async =>
      PushAuthorizationStatus.authorized;

  @override
  Future<String?> registrationToken() async => 'token';

  @override
  Stream<String> get tokenRefreshes => _tokens.stream;
}

ServerConnection _connection() {
  return ServerConnection(
    baseUri: Uri.parse('https://voxos.example.com'),
    config: ServerConfig.fromJson(clientConfigJson()),
  );
}

VoiceController _voiceController({
  required FakeAuthSession auth,
  required List<FakeVoiceTransport> transports,
}) {
  return VoiceController(
    api: VoxosApiClient(
      auth: auth,
      connection: _connection(),
      client: MockClient((request) async {
        if (request.url.path == '/api/voice/provider-settings') {
          return http.Response(
            jsonEncode(<String, Object?>{
              'active_provider': 'openai_realtime',
              'active_settings': <String, Object?>{'configured': true},
              'providers': <Object?>[
                <String, Object?>{
                  'provider': 'openai_realtime',
                  'runtime_supported': true,
                },
              ],
            }),
            200,
          );
        }
        if (request.url.path == '/api/livekit/settings') {
          return http.Response(
            jsonEncode(<String, Object?>{'configured': true}),
            200,
          );
        }
        return http.Response('{}', 404);
      }),
    ),
    microphoneAccess: FakeMicrophoneAccess(),
    participantName: 'Josh',
    wakeLock: FakeVoiceWakeLock(),
    transportFactory: () {
      final transport = FakeVoiceTransport();
      transports.add(transport);
      return transport;
    },
  );
}
