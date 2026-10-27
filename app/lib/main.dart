import 'package:flutter/widgets.dart';
import 'package:sentry_flutter/sentry_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/app.dart';
import 'package:voxos/src/config/server_config_repository.dart';
import 'package:voxos/src/monitoring.dart';

Future<void> main() async {
  const dsn = String.fromEnvironment('SENTRY_DSN');
  if (dsn.isEmpty) {
    await _runApp();
    return;
  }
  await SentryFlutter.init(configureMonitoring, appRunner: _runApp);
}

Future<void> _runApp() async {
  WidgetsFlutterBinding.ensureInitialized();
  final preferences = await SharedPreferences.getInstance();
  final repository = ServerConfigRepository(preferences: preferences);

  runApp(VoxosApp(preferences: preferences, serverRepository: repository));
}
