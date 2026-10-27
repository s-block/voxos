import 'package:flutter_test/flutter_test.dart';
import 'package:sentry_flutter/sentry_flutter.dart';
import 'package:voxos/src/monitoring.dart';

void main() {
  test('monitoring uses conservative collection settings', () {
    final options = SentryFlutterOptions();
    configureMonitoring(options);
    expect(options.sampleRate, 1);
    expect(options.tracesSampleRate, 0.01);
    expect(options.sendDefaultPii, isFalse);
    expect(options.enableLogs, isFalse);
    expect(options.maxBreadcrumbs, 0);
    expect(options.replay.sessionSampleRate, 0);
    expect(options.replay.onErrorSampleRate, 0);
    expect(options.tracePropagationTargets, isEmpty);
  });

  test('events retain exception type and remove private payloads', () {
    const privateValue = 'private-tenant-content';
    final event = SentryEvent(
      user: SentryUser(email: privateValue),
      request: SentryRequest(url: privateValue),
      message: SentryMessage(privateValue),
      breadcrumbs: [Breadcrumb(message: privateValue)],
      exceptions: [SentryException(type: 'StateError', value: privateValue)],
      contexts: Contexts()..['conversation'] = {'prompt': privateValue},
    );
    final sanitized = sanitizeEvent(event);
    expect(sanitized.toJson().toString(), isNot(contains(privateValue)));
    expect(sanitized.exceptions?.single.type, 'StateError');
  });
}
