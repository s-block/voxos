import 'package:wakelock_plus/wakelock_plus.dart';

abstract interface class VoiceWakeLock {
  Future<void> setEnabled(bool enabled);
}

final class DeviceVoiceWakeLock implements VoiceWakeLock {
  const DeviceVoiceWakeLock();

  @override
  Future<void> setEnabled(bool enabled) {
    return WakelockPlus.toggle(enable: enabled);
  }
}
