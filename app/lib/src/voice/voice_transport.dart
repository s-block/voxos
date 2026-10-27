import 'package:voxos/src/location/location_service.dart';

final class VoiceTransportEvent {
  const VoiceTransportEvent._(this.type);

  const VoiceTransportEvent.disconnected()
    : this._(VoiceTransportEventType.disconnected);

  const VoiceTransportEvent.stateChanged()
    : this._(VoiceTransportEventType.stateChanged);

  final VoiceTransportEventType type;
}

enum VoiceTransportEventType { disconnected, stateChanged }

abstract interface class VoiceTransport {
  bool get isRemoteSpeaking;

  Stream<VoiceTransportEvent> get events;

  Future<void> connect({
    AgentLocationSnapshot? currentLocation,
    required String participantName,
    required String roomName,
    required bool startWithMicrophone,
  });

  Future<void> disconnect();

  Future<void> dispose();

  Future<void> setMicrophoneEnabled(bool enabled);
}

abstract interface class MicrophoneAccess {
  Future<bool> request();
}
