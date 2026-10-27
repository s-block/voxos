import 'package:flutter/foundation.dart';
import 'package:sentry_flutter/sentry_flutter.dart';

void configureMonitoring(SentryFlutterOptions options) {
  options
    ..dsn = const String.fromEnvironment('SENTRY_DSN')
    ..environment = const String.fromEnvironment(
      'SENTRY_ENVIRONMENT',
      defaultValue: kReleaseMode ? 'production' : 'development',
    )
    ..sampleRate = 1
    ..tracesSampleRate = 0.01
    ..sendDefaultPii = false
    ..enableLogs = false
    ..maxBreadcrumbs = 0
    ..enableAutoSessionTracking = false
    ..attachScreenshot = false
    ..captureFailedRequests = false
    ..maxRequestBodySize = MaxRequestBodySize.never
    ..beforeSend = ((event, hint) => sanitizeEvent(event))
    ..beforeSendTransaction = ((event, hint) => sanitizeEvent(event));
  options.tracePropagationTargets.clear();
  options.replay
    ..sessionSampleRate = 0
    ..onErrorSampleRate = 0;
}

T sanitizeEvent<T extends SentryEvent>(T event) {
  event
    ..request = null
    ..user = null
    ..breadcrumbs = []
    ..message = null
    ..serverName = null
    ..tags = {}
    ..contexts = Contexts(
      runtimes: event.contexts.runtimes,
      trace: event.contexts.trace,
    );
  // Structured contexts replace the SDK's deprecated arbitrary extras.
  // ignore: deprecated_member_use
  event.extra = null;
  event.contexts.trace
    ?..data = null
    ..description = null;
  for (final exception in event.exceptions ?? <SentryException>[]) {
    exception.value = null;
    exception.mechanism?.data = null;
  }
  if (event.transaction != null) event.transaction = 'mobile.navigation';
  if (event is SentryTransaction) {
    for (final span in event.spans) {
      span.context.description = span.context.operation;
      for (final key in span.data.keys.toList()) {
        span.removeData(key);
      }
    }
  }
  return event;
}
