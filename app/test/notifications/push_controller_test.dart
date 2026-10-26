import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/notifications/push_api.dart';
import 'package:voxos/src/notifications/push_controller.dart';
import 'package:voxos/src/notifications/push_message.dart';
import 'package:voxos/src/notifications/push_messaging.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test(
    'initialization never requests permission without user action',
    () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final messaging = FakePushMessaging();
      final controller = PushController(
        api: FakePushApi(),
        available: true,
        installations: FakeInstallationIdProvider(),
        messaging: messaging,
        platform: 'ios',
        preferences: await SharedPreferences.getInstance(),
        serverUri: Uri.parse('https://voxos.example.com'),
        userId: 'user-123',
      );

      await controller.initialize();

      expect(messaging.permissionRequests, 0);
      expect(messaging.tokenRequests, 0);
      expect(controller.status, PushPreferenceStatus.disabled);
      controller.dispose();
      await messaging.close();
    },
  );

  test(
    'opt in registers and opt out revokes before deleting the token',
    () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final api = FakePushApi();
      final messaging = FakePushMessaging(
        requestedStatus: PushAuthorizationStatus.authorized,
      );
      final controller = PushController(
        api: api,
        available: true,
        installations: FakeInstallationIdProvider(),
        messaging: messaging,
        platform: 'ios',
        preferences: await SharedPreferences.getInstance(),
        serverUri: Uri.parse('https://voxos.example.com'),
        userId: 'user-123',
      );
      await controller.initialize();

      await controller.enable();

      expect(controller.isEnabled, isTrue);
      expect(api.registeredInstallationId, 'fid-123');
      expect(api.registeredPlatform, 'ios');
      expect(api.registeredToken, 'fcm-token');

      await controller.disable();

      expect(controller.status, PushPreferenceStatus.disabled);
      expect(api.calls, <String>['register', 'revoke']);
      expect(messaging.deleteTokenCalls, 1);
      controller.dispose();
      await messaging.close();
    },
  );

  test('denied permission exposes the settings recovery action', () async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    var settingsOpened = false;
    final messaging = FakePushMessaging(
      requestedStatus: PushAuthorizationStatus.denied,
    );
    final controller = PushController(
      api: FakePushApi(),
      available: true,
      installations: FakeInstallationIdProvider(),
      messaging: messaging,
      openSettings: () async {
        settingsOpened = true;
        return true;
      },
      platform: 'ios',
      preferences: await SharedPreferences.getInstance(),
      serverUri: Uri.parse('https://voxos.example.com'),
      userId: 'user-123',
    );
    await controller.initialize();

    await controller.enable();
    await controller.openSettings();

    expect(controller.isDenied, isTrue);
    expect(settingsOpened, isTrue);
    controller.dispose();
    await messaging.close();
  });

  test('run terminal payload parses a typed chat intent', () {
    final intent = PushIntent.fromData(<String, Object?>{
      'schema_version': '1',
      'type': 'run_terminal',
      'notification_id': 'run:run-123:succeeded',
      'run_id': 'run-123',
      'chat_id': 'chat-123',
      'status': 'succeeded',
    });

    expect(intent.chatId, 'chat-123');
    expect(intent.runId, 'run-123');
    expect(intent.type, PushIntentType.runTerminal);
    expect(
      () => PushIntent.fromData(<String, Object?>{'schema_version': '2'}),
      throwsFormatException,
    );
  });

  test('chat message payload parses a typed chat intent', () {
    final intent = PushIntent.fromData(<String, Object?>{
      'schema_version': '1',
      'type': 'chat_message',
      'notification_id': 'message:message-123',
      'message_id': 'message-123',
      'chat_id': 'chat-123',
    });

    expect(intent.type, PushIntentType.chatMessage);
    expect(intent.chatId, 'chat-123');
    expect(intent.messageId, 'message-123');
    expect(intent.runId, isNull);
    expect(intent.status, isNull);
  });
}

final class FakePushApi implements PushApi {
  final List<String> calls = <String>[];
  String? registeredInstallationId;
  String? registeredPlatform;
  String? registeredToken;

  @override
  void dispose() {}

  @override
  Future<void> registerInstallation({
    required String installationId,
    required String platform,
    required String registrationToken,
  }) async {
    calls.add('register');
    registeredInstallationId = installationId;
    registeredPlatform = platform;
    registeredToken = registrationToken;
  }

  @override
  Future<void> revokeInstallation({required String installationId}) async {
    calls.add('revoke');
  }
}

final class FakeInstallationIdProvider implements PushInstallationIdProvider {
  @override
  Future<String> installationId() async => 'fid-123';
}

final class FakePushMessaging implements PushMessaging {
  FakePushMessaging({
    this.currentStatus = PushAuthorizationStatus.notDetermined,
    this.requestedStatus = PushAuthorizationStatus.authorized,
  });

  final PushAuthorizationStatus currentStatus;
  final StreamController<PushEnvelope> foreground =
      StreamController<PushEnvelope>.broadcast();
  final StreamController<PushEnvelope> opened =
      StreamController<PushEnvelope>.broadcast();
  final PushAuthorizationStatus requestedStatus;
  final StreamController<String> tokens = StreamController<String>.broadcast();
  int deleteTokenCalls = 0;
  int permissionRequests = 0;
  int tokenRequests = 0;

  Future<void> close() async {
    await foreground.close();
    await opened.close();
    await tokens.close();
  }

  @override
  Future<PushAuthorizationStatus> authorizationStatus() async => currentStatus;

  @override
  Future<void> deleteToken() async {
    deleteTokenCalls += 1;
  }

  @override
  Stream<PushEnvelope> get foregroundMessages => foreground.stream;

  @override
  Future<PushEnvelope?> initialMessage() async => null;

  @override
  Stream<PushEnvelope> get openedMessages => opened.stream;

  @override
  Future<PushAuthorizationStatus> requestAuthorization() async {
    permissionRequests += 1;
    return requestedStatus;
  }

  @override
  Future<String?> registrationToken() async {
    tokenRequests += 1;
    return 'fcm-token';
  }

  @override
  Stream<String> get tokenRefreshes => tokens.stream;
}
