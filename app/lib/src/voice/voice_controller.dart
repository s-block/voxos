import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:uuid/uuid.dart';
import 'package:voxos/src/api/voxos_api_client.dart';
import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/voice/voice_transport.dart';
import 'package:voxos/src/voice/voice_wake_lock.dart';

enum VoicePhase { checking, ready, connecting, active, unavailable }

typedef VoiceTransportFactory = VoiceTransport Function();

/// Owns the lifecycle of a live voice conversation.
///
/// Nothing connects until [openCall] runs, and every call ends by
/// disconnecting the room so a backgrounded app cannot keep spending.
final class VoiceController extends ChangeNotifier {
  VoiceController({
    required VoxosApiClient api,
    required MicrophoneAccess microphoneAccess,
    CurrentLocationProvider? locationProvider,
    required String participantName,
    required VoiceTransportFactory transportFactory,
    Uuid uuid = const Uuid(),
    VoiceWakeLock wakeLock = const DeviceVoiceWakeLock(),
  }) : _api = api,
       _microphoneAccess = microphoneAccess,
       _locationProvider = locationProvider,
       _participantName = participantName,
       _transportFactory = transportFactory,
       _uuid = uuid,
       _wakeLock = wakeLock,
       _roomName = _newRoomName(uuid);

  final VoxosApiClient _api;
  final MicrophoneAccess _microphoneAccess;
  final CurrentLocationProvider? _locationProvider;
  final String _participantName;
  final VoiceTransportFactory _transportFactory;
  final Uuid _uuid;
  final VoiceWakeLock _wakeLock;

  StreamSubscription<VoiceTransportEvent>? _transportSubscription;
  Future<void> _wakeLockUpdate = Future<void>.value();
  VoiceTransport? _transport;
  VoicePhase _phase = VoicePhase.checking;
  String _roomName;
  String? _message;
  bool _isMuted = true;
  bool _disposed = false;
  bool _wakeLockDesired = false;
  bool _wakeLockEnabled = false;
  int _operation = 0;

  VoicePhase get phase => _phase;
  String? get message => _message;
  bool get isMuted => _isMuted;
  bool get isRemoteSpeaking => _transport?.isRemoteSpeaking ?? false;
  bool get hasLiveConnection =>
      _phase == VoicePhase.connecting || _phase == VoicePhase.active;

  /// Checks readiness and connects a fresh conversation in one step.
  ///
  /// This is the only entry point a caller needs, including for retrying
  /// after a failed or unavailable check.
  Future<void> openCall() async {
    if (_disposed || hasLiveConnection) {
      return;
    }
    await refreshReadiness();
    if (_disposed || _phase != VoicePhase.ready) {
      return;
    }
    _roomName = _newRoomName(_uuid);
    await _connect();
  }

  Future<void> refreshReadiness() async {
    if (_disposed || hasLiveConnection) {
      return;
    }
    _setState(phase: VoicePhase.checking, clearMessage: true);
    try {
      final readiness = await _api.loadVoiceReadiness();
      if (_disposed) {
        return;
      }
      if (readiness.isReady) {
        _setState(phase: VoicePhase.ready);
      } else {
        _setState(
          phase: VoicePhase.unavailable,
          message: readiness.reason ?? 'Voice is not ready.',
        );
      }
    } catch (error) {
      if (!_disposed) {
        _setState(
          phase: VoicePhase.unavailable,
          message: _friendlyError(error),
        );
      }
    }
  }

  Future<void> toggleMute() async {
    final transport = _transport;
    if (_disposed || _phase != VoicePhase.active || transport == null) {
      return;
    }

    final shouldEnable = _isMuted;
    if (shouldEnable && !await _microphoneAccess.request()) {
      _setState(
        message:
            'Microphone access is disabled. Enable it in system settings to unmute.',
      );
      return;
    }

    try {
      await transport.setMicrophoneEnabled(shouldEnable);
      if (_disposed || _transport != transport) {
        return;
      }
      // The call screen shows the mute state itself, so no message is needed.
      _isMuted = !shouldEnable;
      _setState(clearMessage: true);
    } catch (error) {
      if (!_disposed) {
        _setState(message: _friendlyError(error));
      }
    }
  }

  /// Ends the conversation and releases the room.
  Future<void> endCall() async {
    if (_phase == VoicePhase.checking || _phase == VoicePhase.unavailable) {
      return;
    }
    await _disconnect(nextPhase: VoicePhase.ready, message: 'Call ended.');
  }

  Future<void> stopForLifecycle() async {
    if (!hasLiveConnection) {
      return;
    }
    await _disconnect(
      nextPhase: VoicePhase.ready,
      message: 'The call ended when the app left the foreground.',
    );
  }

  Future<void> shutdown() => _disconnect(nextPhase: VoicePhase.ready);

  @override
  void dispose() {
    if (_disposed) {
      return;
    }
    _disposed = true;
    _operation++;
    _scheduleWakeLockUpdate(enabled: false);
    final transport = _transport;
    _transport = null;
    final subscription = _transportSubscription;
    _transportSubscription = null;
    unawaited(subscription?.cancel());
    if (transport != null) {
      unawaited(transport.dispose());
    }
    _api.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    final operation = ++_operation;
    _setState(phase: VoicePhase.connecting, clearMessage: true);
    final hasPermission = await _microphoneAccess.request();
    if (_disposed || operation != _operation) {
      return;
    }
    if (!hasPermission) {
      _setState(
        phase: VoicePhase.ready,
        message:
            'Microphone access is required. Enable it in system settings and try again.',
      );
      await _wakeLockUpdate;
      return;
    }

    final transport = _transportFactory();
    _transport = transport;
    await _transportSubscription?.cancel();
    _transportSubscription = transport.events.listen(
      (event) => _handleTransportEvent(transport, event),
    );
    _isMuted = true;

    try {
      final currentLocation = await _currentLocation();
      await transport.connect(
        currentLocation: currentLocation,
        participantName: _participantName,
        roomName: _roomName,
        startWithMicrophone: true,
      );
      if (_disposed || operation != _operation || _transport != transport) {
        await transport.dispose();
        return;
      }
      // The transport publishes the microphone during the room join so the
      // agent receives audio as soon as it is dispatched. A successful
      // connection therefore represents an active, unmuted call.
      _isMuted = false;
      _setState(phase: VoicePhase.active, clearMessage: true);
      await _wakeLockUpdate;
    } catch (error) {
      if (_transport == transport) {
        _transport = null;
      }
      await _transportSubscription?.cancel();
      _transportSubscription = null;
      await transport.dispose();
      if (!_disposed && operation == _operation) {
        _setState(phase: VoicePhase.ready, message: _friendlyError(error));
        await _wakeLockUpdate;
      }
    }
  }

  Future<void> _disconnect({
    required VoicePhase nextPhase,
    String? message,
  }) async {
    _operation++;
    final transport = _transport;
    _transport = null;
    final subscription = _transportSubscription;
    _transportSubscription = null;
    _isMuted = true;

    if (!_disposed) {
      _setState(
        phase: nextPhase,
        message: message,
        clearMessage: message == null,
      );
    }

    await subscription?.cancel();
    if (transport != null) {
      await transport.dispose();
    }
    await _wakeLockUpdate;
  }

  Future<AgentLocationSnapshot?> _currentLocation() async {
    try {
      return await _locationProvider?.snapshotForAction();
    } on Exception {
      return null;
    }
  }

  void _handleTransportEvent(VoiceTransport source, VoiceTransportEvent event) {
    if (_disposed || source != _transport) {
      return;
    }
    if (event.type == VoiceTransportEventType.stateChanged) {
      notifyListeners();
      return;
    }

    _operation++;
    _transport = null;
    final subscription = _transportSubscription;
    _transportSubscription = null;
    _isMuted = true;
    unawaited(subscription?.cancel());
    unawaited(source.dispose());
    _setState(
      phase: VoicePhase.ready,
      message: 'The call ended after silence or a connection change.',
    );
  }

  void _setState({
    VoicePhase? phase,
    String? message,
    bool clearMessage = false,
  }) {
    if (_disposed) {
      return;
    }
    if (phase != null) {
      _phase = phase;
      _scheduleWakeLockUpdate(enabled: hasLiveConnection);
    }
    if (clearMessage) {
      _message = null;
    } else if (message != null) {
      _message = message;
    }
    notifyListeners();
  }

  void _scheduleWakeLockUpdate({required bool enabled}) {
    _wakeLockDesired = enabled;
    _wakeLockUpdate = _wakeLockUpdate.then((_) async {
      final desired = _wakeLockDesired;
      if (_wakeLockEnabled == desired) {
        return;
      }
      try {
        await _wakeLock.setEnabled(desired);
        _wakeLockEnabled = desired;
      } catch (error, stackTrace) {
        debugPrint('Could not update the voice display wake lock: $error');
        debugPrintStack(stackTrace: stackTrace);
      }
    });
  }

  static String _newRoomName(Uuid uuid) => 'voxos-mobile-${uuid.v4()}';

  static String _friendlyError(Object error) {
    if (error is ApiException) {
      return error.message;
    }
    if (error is AuthFailure) {
      return error.message;
    }
    if (error is StateError) {
      return error.message;
    }
    return 'Voice could not start. Please try again.';
  }
}
