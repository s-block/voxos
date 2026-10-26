import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:livekit_client/livekit_client.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/voice/voice_transport.dart';

final class PermissionMicrophoneAccess implements MicrophoneAccess {
  bool _requestedBluetoothAccess = false;

  @override
  Future<bool> request() async {
    final status = await Permission.microphone.request();
    if (status.isGranted &&
        defaultTargetPlatform == TargetPlatform.android &&
        !_requestedBluetoothAccess) {
      _requestedBluetoothAccess = true;
      await Permission.bluetoothConnect.request();
    }
    return status.isGranted;
  }
}

final class LiveKitVoiceTransport implements VoiceTransport {
  LiveKitVoiceTransport({
    required AuthSession auth,
    required ServerConnection connection,
  }) : _auth = auth,
       _connection = connection;

  static const _tokenTimeout = Duration(seconds: 60);

  final AuthSession _auth;
  final ServerConnection _connection;
  final StreamController<VoiceTransportEvent> _events =
      StreamController<VoiceTransportEvent>.broadcast();

  EventsListener<RoomEvent>? _listener;
  Room? _room;
  bool _disposed = false;
  int _operation = 0;

  @override
  Stream<VoiceTransportEvent> get events => _events.stream;

  @override
  bool get isRemoteSpeaking {
    final room = _room;
    if (room == null) {
      return false;
    }
    return room.remoteParticipants.values.any(
      (participant) => participant.isSpeaking,
    );
  }

  @override
  Future<void> connect({
    AgentLocationSnapshot? currentLocation,
    required String participantName,
    required String roomName,
    required bool startWithMicrophone,
  }) async {
    if (_disposed) {
      throw StateError('Voice transport is closed.');
    }
    final operation = ++_operation;
    await _closeRoom();
    _ensureConnectionCurrent(operation);

    final token = await _auth.idToken();
    _ensureConnectionCurrent(operation);
    final tokenSource = EndpointTokenSource(
      url: _connection.resolvePath(_connection.config.voice.tokenEndpointPath),
      headers: <String, String>{'Authorization': 'Bearer $token'},
    );
    late final TokenSourceResponse credentials;
    try {
      credentials = await tokenSource
          .fetch(
            TokenRequestOptions(
              agentName: _connection.config.voice.agentName,
              participantMetadata: currentLocation == null
                  ? null
                  : jsonEncode(<String, Object>{
                      'voxos_location': currentLocation.toJson(),
                    }),
              participantName: participantName,
              roomName: roomName,
            ),
          )
          .timeout(_tokenTimeout);
    } on TokenSourceHttpException catch (error) {
      throw StateError(_tokenErrorMessage(error));
    } on TimeoutException {
      throw StateError(
        'Voice took too long to become available. Please try again.',
      );
    }
    _ensureConnectionCurrent(operation);

    final room = Room(
      roomOptions: const RoomOptions(adaptiveStream: true, dynacast: true),
    );
    _room = room;
    room.addListener(_onRoomChanged);
    _listener = room.createListener()
      ..on<RoomDisconnectedEvent>((_) {
        if (!_events.isClosed) {
          _events.add(const VoiceTransportEvent.disconnected());
        }
      })
      ..on<ActiveSpeakersChangedEvent>((_) {
        if (!_events.isClosed) {
          _events.add(const VoiceTransportEvent.stateChanged());
        }
      });

    try {
      await room.connect(
        credentials.serverUrl,
        credentials.participantToken,
        fastConnectOptions: startWithMicrophone
            ? FastConnectOptions(
                microphone: const TrackOption<bool, LocalAudioTrack>(
                  enabled: true,
                ),
              )
            : null,
      );
      _ensureConnectionCurrent(operation);
      if (startWithMicrophone &&
          !(room.localParticipant?.isMicrophoneEnabled() ?? false)) {
        throw StateError(
          'The microphone did not start. Check microphone access and try again.',
        );
      }
    } catch (_) {
      await _closeRoomIfCurrent(room);
      rethrow;
    }
  }

  @override
  Future<void> setMicrophoneEnabled(bool enabled) async {
    final participant = _room?.localParticipant;
    if (participant == null) {
      throw StateError('Voice is not connected.');
    }
    await participant.setMicrophoneEnabled(enabled);
  }

  @override
  Future<void> disconnect() async {
    _operation++;
    await _closeRoom();
  }

  @override
  Future<void> dispose() async {
    if (_disposed) {
      return;
    }
    _disposed = true;
    _operation++;
    await _closeRoom();
    await _events.close();
  }

  void _ensureConnectionCurrent(int operation) {
    if (_disposed || operation != _operation) {
      throw StateError('Voice start was cancelled.');
    }
  }

  Future<void> _closeRoomIfCurrent(Room room) async {
    if (identical(_room, room)) {
      await _closeRoom();
    }
  }

  Future<void> _closeRoom() async {
    final room = _room;
    final listener = _listener;
    _room = null;
    _listener = null;

    if (room == null) {
      if (listener != null) {
        await listener.dispose();
      }
      return;
    }

    room.removeListener(_onRoomChanged);
    try {
      await room.localParticipant?.setMicrophoneEnabled(false);
    } on Exception {
      // Disconnection below still releases the room and microphone resources.
    }
    try {
      await room.disconnect();
    } on Exception {
      // Disposal is the final safety net if the signaling disconnect fails.
    }
    if (listener != null) {
      await listener.dispose();
    }
    await room.dispose();
  }

  void _onRoomChanged() {
    if (!_events.isClosed) {
      _events.add(const VoiceTransportEvent.stateChanged());
    }
  }

  String _tokenErrorMessage(TokenSourceHttpException error) {
    if (error.statusCode == 401) {
      return 'Your sign-in is no longer valid. Sign out and try again.';
    }
    try {
      final decoded = jsonDecode(error.body);
      if (decoded is Map<String, Object?>) {
        final detail = decoded['detail'];
        if (detail is String && detail.trim().isNotEmpty) {
          return detail.trim();
        }
      }
    } on FormatException {
      // Use the safe generic message below.
    }
    return 'Voxos could not start the voice session.';
  }
}
