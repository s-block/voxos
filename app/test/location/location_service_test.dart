import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/location/location_service.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  test(
    'location sharing is off until a successful foreground opt-in',
    () async {
      final preferences = await SharedPreferences.getInstance();
      final source = _FakeLocationSource(snapshot: _snapshot());
      final controller = LocationSharingController(
        preferences: preferences,
        source: source,
        storageKey: 'location-key',
        now: () => DateTime.utc(2026, 8, 30, 12, 1),
      );

      expect(await controller.snapshotForAction(), isNull);
      await controller.setEnabled(true);

      expect(controller.enabled, isTrue);
      expect(preferences.getBool('location-key'), isTrue);
      expect(await controller.snapshotForAction(), same(source.snapshot));
      expect(source.captureCount, 1);
    },
  );

  test('a revoked permission disables sharing without throwing', () async {
    SharedPreferences.setMockInitialValues(<String, Object>{
      'location-key': true,
    });
    final preferences = await SharedPreferences.getInstance();
    final controller = LocationSharingController(
      preferences: preferences,
      source: _FakeLocationSource(
        error: const LocationSharingException(
          'Location permission was denied.',
          permissionDenied: true,
        ),
      ),
      storageKey: 'location-key',
    );

    expect(await controller.snapshotForAction(), isNull);
    expect(controller.enabled, isFalse);
    expect(controller.error, contains('denied'));
    expect(preferences.getBool('location-key'), isNull);
  });

  test('preference keys are scoped to both server and user', () {
    final first = locationSharingStorageKey(
      serverUri: Uri.parse('https://api.example.test'),
      userId: 'user-123',
    );
    final second = locationSharingStorageKey(
      serverUri: Uri.parse('https://api.example.test'),
      userId: 'user-456',
    );

    expect(first, isNot(second));
  });
}

final class _FakeLocationSource implements DeviceLocationSource {
  _FakeLocationSource({this.error, AgentLocationSnapshot? snapshot})
    : snapshot = snapshot ?? _snapshot();

  final LocationSharingException? error;
  final AgentLocationSnapshot snapshot;
  int captureCount = 0;

  @override
  Future<AgentLocationSnapshot> capture() async {
    captureCount += 1;
    final error = this.error;
    if (error != null) {
      throw error;
    }
    return snapshot;
  }
}

AgentLocationSnapshot _snapshot() => AgentLocationSnapshot(
  capturedAt: DateTime.utc(2026, 8, 30, 12),
  horizontalAccuracyM: 25,
  latitude: 42.4304,
  longitude: 19.2594,
);
