import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/notifications/push_api.dart';
import 'package:voxos/src/notifications/push_message.dart';
import 'package:voxos/src/notifications/push_messaging.dart';

enum PushPreferenceStatus {
  unavailable,
  disabled,
  enabling,
  enabled,
  disabling,
  denied,
}

final class PushController extends ChangeNotifier {
  PushController({
    required PushApi api,
    required bool available,
    required PushInstallationIdProvider? installations,
    required PushMessaging? messaging,
    required SharedPreferences preferences,
    required Uri serverUri,
    required String userId,
    Future<bool> Function()? openSettings,
    String? platform,
  }) : _api = api,
       _available = available,
       _installations = installations,
       _messaging = messaging,
       _openSettings = openSettings ?? openAppSettings,
       _platform = platform ?? _platformName(),
       _preferences = preferences,
       _preferenceKey = _buildPreferenceKey(serverUri, userId),
       _status = available
           ? PushPreferenceStatus.disabled
           : PushPreferenceStatus.unavailable;

  final PushApi _api;
  final bool _available;
  final PushInstallationIdProvider? _installations;
  final PushMessaging? _messaging;
  final Future<bool> Function() _openSettings;
  final String _platform;
  final String _preferenceKey;
  final SharedPreferences _preferences;
  final StreamController<PushEvent> _events =
      StreamController<PushEvent>.broadcast();

  StreamSubscription<PushEnvelope>? _foregroundSubscription;
  StreamSubscription<PushEnvelope>? _openedSubscription;
  StreamSubscription<String>? _tokenSubscription;
  PushEvent? _pendingOpen;
  PushPreferenceStatus _status;
  String? _error;
  bool _disposed = false;

  String? get error => _error;
  Stream<PushEvent> get events => _events.stream;
  bool get isAvailable => _available;
  bool get isBusy =>
      _status == PushPreferenceStatus.enabling ||
      _status == PushPreferenceStatus.disabling;
  bool get isDenied => _status == PushPreferenceStatus.denied;
  bool get isEnabled => _status == PushPreferenceStatus.enabled;
  PushPreferenceStatus get status => _status;

  Future<void> initialize() async {
    final messaging = _messaging;
    if (!_available || messaging == null || _installations == null) {
      return;
    }
    _foregroundSubscription = messaging.foregroundMessages.listen(
      (message) => _events.add(PushEvent(envelope: message, foreground: true)),
    );
    _openedSubscription = messaging.openedMessages.listen(_queueOrEmitOpen);
    _tokenSubscription = messaging.tokenRefreshes.listen((_) {
      if (_preferences.getBool(_preferenceKey) ?? false) {
        unawaited(_refreshRegistration());
      }
    });

    final initial = await messaging.initialMessage();
    if (initial != null) {
      _queueOrEmitOpen(initial);
    }
    if (!(_preferences.getBool(_preferenceKey) ?? false)) {
      return;
    }
    final authorization = await messaging.authorizationStatus();
    if (_isAuthorized(authorization)) {
      _status = PushPreferenceStatus.enabled;
      _notify();
      await _syncRegistration();
    } else if (authorization == PushAuthorizationStatus.denied) {
      _status = PushPreferenceStatus.denied;
      _notify();
    }
  }

  Future<void> enable() async {
    final messaging = _messaging;
    if (!_available || messaging == null || _installations == null || isBusy) {
      return;
    }
    _status = PushPreferenceStatus.enabling;
    _error = null;
    _notify();
    try {
      final authorization = await messaging.requestAuthorization();
      if (!_isAuthorized(authorization)) {
        await _preferences.setBool(_preferenceKey, false);
        _status = authorization == PushAuthorizationStatus.denied
            ? PushPreferenceStatus.denied
            : PushPreferenceStatus.disabled;
        return;
      }
      await _preferences.setBool(_preferenceKey, true);
      await _syncRegistration();
      _status = PushPreferenceStatus.enabled;
    } on Object {
      _error = 'Notifications could not be enabled. Check your connection.';
      _status = PushPreferenceStatus.disabled;
    } finally {
      _notify();
    }
  }

  Future<void> disable() async {
    if (!_available || isBusy) {
      return;
    }
    _status = PushPreferenceStatus.disabling;
    _error = null;
    _notify();
    try {
      await _revokeAndDeleteToken();
      await _preferences.setBool(_preferenceKey, false);
      _status = PushPreferenceStatus.disabled;
    } on Object {
      _error = 'Notifications could not be disabled. Check your connection.';
      _status = PushPreferenceStatus.enabled;
    } finally {
      _notify();
    }
  }

  Future<void> prepareForExit() async {
    if (!_available) {
      return;
    }
    try {
      await _revokeAndDeleteToken();
    } on Object {
      try {
        await _messaging?.deleteToken();
      } on Object {
        // Invalid registrations are deactivated by the backend after send.
      }
    }
    await _preferences.setBool(_preferenceKey, false);
  }

  Future<bool> openSettings() => _openSettings();

  PushEvent? takePendingOpen() {
    final event = _pendingOpen;
    _pendingOpen = null;
    return event;
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(_foregroundSubscription?.cancel());
    unawaited(_openedSubscription?.cancel());
    unawaited(_tokenSubscription?.cancel());
    unawaited(_events.close());
    _api.dispose();
    super.dispose();
  }

  Future<void> _syncRegistration() async {
    final messaging = _messaging;
    final installations = _installations;
    if (messaging == null || installations == null) {
      return;
    }
    final registrationToken = await messaging.registrationToken();
    if (registrationToken == null || registrationToken.trim().isEmpty) {
      throw StateError('Firebase did not provide a messaging token.');
    }
    await _api.registerInstallation(
      installationId: await installations.installationId(),
      platform: _platform,
      registrationToken: registrationToken,
    );
  }

  Future<void> _refreshRegistration() async {
    try {
      await _syncRegistration();
      _error = null;
    } on Object {
      _error = 'Notifications will reconnect when the app is next opened.';
    }
    _notify();
  }

  Future<void> _revokeAndDeleteToken() async {
    final messaging = _messaging;
    final installations = _installations;
    if (messaging == null || installations == null) {
      return;
    }
    final installationId = await installations.installationId();
    await _api.revokeInstallation(installationId: installationId);
    await messaging.deleteToken();
  }

  void _queueOrEmitOpen(PushEnvelope envelope) {
    final event = PushEvent(envelope: envelope, foreground: false);
    if (_events.hasListener) {
      _events.add(event);
    } else {
      _pendingOpen = event;
    }
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  static bool _isAuthorized(PushAuthorizationStatus status) {
    return status == PushAuthorizationStatus.authorized ||
        status == PushAuthorizationStatus.provisional;
  }

  static String _buildPreferenceKey(Uri serverUri, String userId) {
    final source = '${serverUri.origin}|$userId';
    final safe = source.replaceAll(RegExp('[^a-zA-Z0-9._-]'), '_');
    return 'voxos.push.v1.$safe';
  }

  static String _platformName() {
    return switch (defaultTargetPlatform) {
      TargetPlatform.android => 'android',
      TargetPlatform.iOS => 'ios',
      _ => throw StateError('Push notifications require iOS or Android.'),
    };
  }
}
