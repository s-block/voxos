import 'package:firebase_app_installations/firebase_app_installations.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:voxos/src/notifications/push_message.dart';

enum PushAuthorizationStatus { authorized, denied, notDetermined, provisional }

abstract interface class PushMessaging {
  Future<PushAuthorizationStatus> authorizationStatus();

  Future<void> deleteToken();

  Stream<PushEnvelope> get foregroundMessages;

  Future<PushEnvelope?> initialMessage();

  Stream<PushEnvelope> get openedMessages;

  Future<PushAuthorizationStatus> requestAuthorization();

  Future<String?> registrationToken();

  Stream<String> get tokenRefreshes;
}

abstract interface class PushInstallationIdProvider {
  Future<String> installationId();
}

final class FirebasePushMessaging implements PushMessaging {
  FirebasePushMessaging({FirebaseMessaging? messaging})
    : _messaging = messaging ?? FirebaseMessaging.instance;

  final FirebaseMessaging _messaging;

  @override
  Future<PushAuthorizationStatus> authorizationStatus() async {
    return _status(
      (await _messaging.getNotificationSettings()).authorizationStatus,
    );
  }

  @override
  Future<void> deleteToken() => _messaging.deleteToken();

  @override
  Stream<PushEnvelope> get foregroundMessages =>
      FirebaseMessaging.onMessage.map(_envelope);

  @override
  Future<PushEnvelope?> initialMessage() async {
    final message = await _messaging.getInitialMessage();
    return message == null ? null : _envelope(message);
  }

  @override
  Stream<PushEnvelope> get openedMessages =>
      FirebaseMessaging.onMessageOpenedApp.map(_envelope);

  @override
  Future<PushAuthorizationStatus> requestAuthorization() async {
    final settings = await _messaging.requestPermission(
      alert: true,
      badge: true,
      sound: true,
    );
    return _status(settings.authorizationStatus);
  }

  @override
  Future<String?> registrationToken() => _messaging.getToken();

  @override
  Stream<String> get tokenRefreshes => _messaging.onTokenRefresh;

  static PushEnvelope _envelope(RemoteMessage message) {
    return PushEnvelope(
      data: Map<String, Object?>.unmodifiable(message.data),
      body: message.notification?.body,
      title: message.notification?.title,
    );
  }

  static PushAuthorizationStatus _status(AuthorizationStatus status) {
    return switch (status) {
      AuthorizationStatus.authorized => PushAuthorizationStatus.authorized,
      AuthorizationStatus.denied => PushAuthorizationStatus.denied,
      AuthorizationStatus.notDetermined =>
        PushAuthorizationStatus.notDetermined,
      AuthorizationStatus.provisional => PushAuthorizationStatus.provisional,
    };
  }
}

final class FirebaseInstallationIdProvider
    implements PushInstallationIdProvider {
  FirebaseInstallationIdProvider({required FirebaseApp app})
    : _installations = FirebaseInstallations.instanceFor(app: app);

  final FirebaseInstallations _installations;

  @override
  Future<String> installationId() => _installations.getId();
}
