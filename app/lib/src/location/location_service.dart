import 'package:flutter/foundation.dart';
import 'package:geolocator/geolocator.dart';
import 'package:shared_preferences/shared_preferences.dart';

final class AgentLocationSnapshot {
  const AgentLocationSnapshot({
    required this.capturedAt,
    required this.horizontalAccuracyM,
    required this.latitude,
    required this.longitude,
  });

  final DateTime capturedAt;
  final double horizontalAccuracyM;
  final double latitude;
  final double longitude;

  Map<String, Object> toJson() => <String, Object>{
    'latitude': latitude,
    'longitude': longitude,
    'horizontal_accuracy_m': horizontalAccuracyM,
    'captured_at': capturedAt.toUtc().toIso8601String(),
  };
}

abstract interface class CurrentLocationProvider {
  Future<AgentLocationSnapshot?> snapshotForAction();
}

abstract interface class DeviceLocationSource {
  Future<AgentLocationSnapshot> capture();
}

final class GeolocatorDeviceLocationSource implements DeviceLocationSource {
  const GeolocatorDeviceLocationSource();

  @override
  Future<AgentLocationSnapshot> capture() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      throw const LocationSharingException(
        'Location Services are off. Turn them on to share your location.',
      );
    }
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.deniedForever) {
      throw const LocationSharingException(
        'Location access is blocked. Allow it in system settings to share your location.',
        permissionDenied: true,
      );
    }
    if (permission == LocationPermission.denied) {
      throw const LocationSharingException(
        'Location permission was denied.',
        permissionDenied: true,
      );
    }
    final position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        timeLimit: Duration(seconds: 6),
      ),
    );
    return AgentLocationSnapshot(
      capturedAt: position.timestamp.toUtc(),
      horizontalAccuracyM: position.accuracy,
      latitude: position.latitude,
      longitude: position.longitude,
    );
  }
}

final class LocationSharingException implements Exception {
  const LocationSharingException(this.message, {this.permissionDenied = false});

  final String message;
  final bool permissionDenied;

  @override
  String toString() => message;
}

final class LocationSharingController extends ChangeNotifier
    implements CurrentLocationProvider {
  LocationSharingController({
    required String storageKey,
    SharedPreferences? preferences,
    DeviceLocationSource source = const GeolocatorDeviceLocationSource(),
    DateTime Function()? now,
  }) : _storageKey = storageKey,
       _preferences = preferences,
       _source = source,
       _now = now ?? DateTime.now,
       _enabled = preferences?.getBool(storageKey) ?? false;

  static const _maximumCacheAge = Duration(minutes: 5);

  final DateTime Function() _now;
  final SharedPreferences? _preferences;
  final DeviceLocationSource _source;
  final String _storageKey;

  AgentLocationSnapshot? _cachedLocation;
  String? _error;
  bool _enabled;
  bool _isWorking = false;

  bool get enabled => _enabled;
  String? get error => _error;
  bool get isWorking => _isWorking;

  Future<void> setEnabled(bool enabled) async {
    if (!enabled) {
      _enabled = false;
      _cachedLocation = null;
      _error = null;
      await _removePreference();
      notifyListeners();
      return;
    }
    _isWorking = true;
    _error = null;
    notifyListeners();
    try {
      _cachedLocation = await _source.capture();
      _enabled = true;
      await _persistEnabledPreference();
    } on LocationSharingException catch (error) {
      _enabled = false;
      _cachedLocation = null;
      _error = error.message;
      await _removePreference();
    } on Exception {
      _enabled = false;
      _cachedLocation = null;
      _error = 'Your current location could not be determined.';
      await _removePreference();
    } finally {
      _isWorking = false;
      notifyListeners();
    }
  }

  @override
  Future<AgentLocationSnapshot?> snapshotForAction() async {
    if (!_enabled) {
      return null;
    }
    final cached = _cachedLocation;
    if (cached != null && _isFresh(cached)) {
      return cached;
    }
    try {
      final location = await _source.capture();
      _cachedLocation = location;
      _error = null;
      return location;
    } on LocationSharingException catch (error) {
      _error = error.message;
      if (error.permissionDenied) {
        _enabled = false;
        _cachedLocation = null;
        await _removePreference();
      }
      notifyListeners();
      return null;
    } on Exception {
      _error = 'Your current location could not be determined.';
      notifyListeners();
      return null;
    }
  }

  bool _isFresh(AgentLocationSnapshot location) {
    final age = _now().toUtc().difference(location.capturedAt.toUtc());
    return !age.isNegative && age <= _maximumCacheAge;
  }

  Future<void> _persistEnabledPreference() async {
    try {
      await _preferences?.setBool(_storageKey, true);
    } on Exception {
      // The in-memory setting remains active for this app session.
    }
  }

  Future<void> _removePreference() async {
    try {
      await _preferences?.remove(_storageKey);
    } on Exception {
      // Location capture remains safe even when preferences are unavailable.
    }
  }
}

String locationSharingStorageKey({
  required Uri serverUri,
  required String userId,
}) => 'voxos.location-sharing:${serverUri.toString()}:$userId';
