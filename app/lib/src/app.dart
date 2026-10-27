import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/api/voxos_api_client.dart';
import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/config/server_config_repository.dart';
import 'package:voxos/src/screens/auth_screen.dart';
import 'package:voxos/src/screens/home_screen.dart';
import 'package:voxos/src/screens/setup_screen.dart';
import 'package:voxos/src/notifications/push_controller.dart';
import 'package:voxos/src/notifications/push_messaging.dart';
import 'package:voxos/src/theme/voxos_theme.dart';
import 'package:voxos/src/widgets/voxos_mark.dart';

final class VoxosApp extends StatelessWidget {
  VoxosApp({
    required this.preferences,
    required this.serverRepository,
    FirebaseAuthBootstrap? authBootstrap,
    super.key,
  }) : authBootstrap = authBootstrap ?? FirebaseAuthBootstrap();

  final FirebaseAuthBootstrap authBootstrap;
  final SharedPreferences preferences;
  final ServerConfigRepository serverRepository;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      darkTheme: VoxosTheme.dark(),
      home: _AppCoordinator(
        authBootstrap: authBootstrap,
        preferences: preferences,
        serverRepository: serverRepository,
      ),
      theme: VoxosTheme.light(),
      themeMode: ThemeMode.system,
      title: 'Voxos',
    );
  }
}

final class _AppCoordinator extends StatefulWidget {
  const _AppCoordinator({
    required this.authBootstrap,
    required this.preferences,
    required this.serverRepository,
  });

  final FirebaseAuthBootstrap authBootstrap;
  final SharedPreferences preferences;
  final ServerConfigRepository serverRepository;

  @override
  State<_AppCoordinator> createState() => _AppCoordinatorState();
}

final class _AppCoordinatorState extends State<_AppCoordinator> {
  StreamSubscription<AuthUser?>? _authSubscription;
  AuthSession? _auth;
  FirebaseBootstrapResult? _firebase;
  PushController? _push;
  AuthUser? _user;
  ServerConnection? _connection;
  List<ServerConnection> _connections = const <ServerConnection>[];
  String _endpointPrefill = '';
  String? _setupError;
  bool _loading = true;
  int _activation = 0;

  @override
  void initState() {
    super.initState();
    unawaited(_restore());
  }

  @override
  void dispose() {
    _activation += 1;
    unawaited(_authSubscription?.cancel());
    _push?.dispose();
    widget.serverRepository.dispose();
    super.dispose();
  }

  Future<void> _restore() async {
    _connections = widget.serverRepository.loadAll();
    final stored = widget.serverRepository.load();
    if (stored == null) {
      if (mounted) {
        setState(() => _loading = false);
      }
      return;
    }

    _endpointPrefill = stored.baseUri.toString();
    try {
      final refreshed = await widget.serverRepository.fetchAndSave(
        stored.baseUri.toString(),
      );
      _connections = widget.serverRepository.loadAll();
      await _activate(refreshed);
    } on FormatException catch (error) {
      _showSetupError(error.message);
    } on ServerConnectionException catch (error) {
      _showSetupError(error.message);
    }
  }

  Future<void> _configure(String endpoint) async {
    if (mounted) {
      setState(() {
        _loading = true;
        _setupError = null;
        _endpointPrefill = endpoint;
      });
    }
    try {
      final connection = await widget.serverRepository.fetchAndSave(endpoint);
      _connections = widget.serverRepository.loadAll();
      await _activate(connection);
    } on FormatException catch (error) {
      _showSetupError(error.message);
    } on ServerConnectionException catch (error) {
      _showSetupError(error.message);
    }
  }

  Future<void> _activate(ServerConnection connection) async {
    final activation = ++_activation;
    try {
      final firebase = await widget.authBootstrap.initialize(connection);
      final auth = firebase.auth;
      await _authSubscription?.cancel();
      if (activation != _activation) {
        return;
      }
      _connection = connection;
      _firebase = firebase;
      _auth = auth;
      _user = auth.currentUser;
      await _replacePushController(user: _user);
      if (activation != _activation) {
        return;
      }
      _authSubscription = auth.authStateChanges().listen((user) {
        unawaited(_handleAuthChange(user));
      });
      if (mounted) {
        setState(() {
          _loading = false;
          _setupError = null;
        });
      }
    } on Exception {
      _showSetupError(
        'Firebase could not start with this server configuration.',
      );
    }
  }

  Future<void> _changeServer() async {
    _activation += 1;
    final currentEndpoint = _connection?.baseUri.toString() ?? _endpointPrefill;
    setState(() => _loading = true);
    await _authSubscription?.cancel();
    _authSubscription = null;
    await _preparePushForExit();
    if (!mounted) {
      return;
    }
    setState(() {
      _auth = null;
      _connection = null;
      _firebase = null;
      _endpointPrefill = currentEndpoint;
      _connections = widget.serverRepository.loadAll();
      _loading = false;
      _setupError = null;
      _user = null;
    });
  }

  Future<void> _signOut() async {
    await _preparePushForExit();
    await _auth?.signOut();
  }

  Future<void> _handleAuthChange(AuthUser? user) async {
    if (!mounted) {
      return;
    }
    if (_user?.id == user?.id && (user == null || _push != null)) {
      return;
    }
    _user = user;
    await _replacePushController(user: user);
    if (mounted) {
      setState(() {});
    }
  }

  Future<void> _replacePushController({required AuthUser? user}) async {
    _push?.dispose();
    _push = null;
    final firebase = _firebase;
    final connection = _connection;
    final auth = _auth;
    if (user == null ||
        firebase == null ||
        connection == null ||
        auth == null) {
      return;
    }
    final controller = PushController(
      api: VoxosApiClient(auth: auth, connection: connection),
      available: firebase.messagingAvailable,
      installations: firebase.messagingAvailable
          ? FirebaseInstallationIdProvider(app: firebase.app)
          : null,
      messaging: firebase.messagingAvailable ? FirebasePushMessaging() : null,
      preferences: widget.preferences,
      serverUri: connection.baseUri,
      userId: user.id,
    );
    _push = controller;
    await controller.initialize();
  }

  Future<void> _preparePushForExit() async {
    final push = _push;
    _push = null;
    if (push == null) {
      return;
    }
    await push.prepareForExit();
    push.dispose();
  }

  void _showSetupError(String message) {
    if (!mounted) {
      return;
    }
    setState(() {
      _auth = null;
      _connection = null;
      _firebase = null;
      _push?.dispose();
      _push = null;
      _loading = false;
      _setupError = message;
      _connections = widget.serverRepository.loadAll();
      _user = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const _LoadingScreen();
    }
    final connection = _connection;
    final auth = _auth;
    if (connection == null || auth == null) {
      return SetupScreen(
        connections: _connections,
        error: _setupError,
        initialEndpoint: _endpointPrefill,
        onConnect: _configure,
      );
    }
    final user = _user;
    if (user == null) {
      return AuthScreen(
        auth: auth,
        onChangeServer: _changeServer,
        serverLabel: connection.baseUri.host,
      );
    }
    final push = _push;
    if (push == null) {
      return const _LoadingScreen();
    }
    return HomeScreen(
      key: ValueKey<String>('${connection.baseUri}|${user.id}'),
      auth: auth,
      connection: connection,
      onChangeServer: _changeServer,
      onSignOut: _signOut,
      preferences: widget.preferences,
      pushController: push,
      user: user,
    );
  }
}

final class _LoadingScreen extends StatelessWidget {
  const _LoadingScreen();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            VoxosMark(size: 64),
            SizedBox(height: 22),
            SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.4),
            ),
          ],
        ),
      ),
    );
  }
}
