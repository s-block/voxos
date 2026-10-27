import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';
import 'package:voxos/src/auth/google_identity.dart';
import 'package:voxos/src/config/server_config.dart';

final class AuthUser {
  const AuthUser({required this.id, this.displayName, this.email});

  final String? displayName;
  final String? email;
  final String id;
}

final class AuthFailure implements Exception {
  const AuthFailure(this.message);

  final String message;

  @override
  String toString() => message;
}

abstract interface class AuthSession {
  AuthUser? get currentUser;

  Stream<AuthUser?> authStateChanges();

  Future<void> createAccount({required String email, required String password});

  Future<String> idToken();

  Future<void> resetPassword(String email);

  Future<void> signIn({required String email, required String password});

  Future<void> signInWithGoogle();

  Future<void> signOut();
}

final class FirebaseAuthBootstrap {
  FirebaseAuthBootstrap({GoogleIdentityAuthenticator? googleIdentity})
    : _googleIdentity = googleIdentity ?? NativeGoogleIdentityAuthenticator();

  final GoogleIdentityAuthenticator _googleIdentity;

  Future<FirebaseBootstrapResult> initialize(
    ServerConnection connection,
  ) async {
    final firebase = connection.config.firebase;
    final appId = switch (defaultTargetPlatform) {
      TargetPlatform.android => firebase.androidAppId,
      TargetPlatform.iOS => firebase.iosAppId,
      _ => throw const AuthFailure(
        'This build supports iPhone and Android only.',
      ),
    };
    final options = FirebaseOptions(
      apiKey: firebase.apiKey,
      appId: appId,
      authDomain: firebase.authDomain,
      messagingSenderId: firebase.messagingSenderId,
      projectId: firebase.projectId,
      storageBucket: firebase.storageBucket,
    );
    // Explicit options allow first launch without a bundled Firebase project.
    // Initializing a named app also loads any optional native default app.
    final platformName = defaultTargetPlatform.name;
    final safeProject = firebase.projectId.replaceAll(
      RegExp('[^a-zA-Z0-9_-]'),
      '_',
    );
    final appName = 'voxos_${safeProject}_$platformName';
    final existing = Firebase.apps.where(
      (candidate) => candidate.name == appName,
    );
    final FirebaseApp configuredApp;
    if (existing.isNotEmpty) {
      final current = existing.single;
      if (!_matches(current.options, options)) {
        await current.delete();
        configuredApp = await Firebase.initializeApp(
          name: appName,
          options: options,
        );
      } else {
        configuredApp = current;
      }
    } else {
      configuredApp = await Firebase.initializeApp(
        name: appName,
        options: options,
      );
    }
    final defaultApp = Firebase.apps
        .where((candidate) => candidate.name == defaultFirebaseAppName)
        .firstOrNull;
    final usesBundledDefault =
        defaultApp != null && _matches(defaultApp.options, options);
    final app = usesBundledDefault ? defaultApp : configuredApp;

    return FirebaseBootstrapResult(
      app: app,
      auth: FirebaseAuthSession(
        FirebaseAuth.instanceFor(app: app),
        googleIdentity: _googleIdentity,
        googleIosClientId: firebase.googleIosClientId,
        googleServerClientId: firebase.googleServerClientId,
        platform: defaultTargetPlatform,
      ),
      messagingAvailable: usesBundledDefault && connection.config.push.enabled,
    );
  }

  static bool _matches(FirebaseOptions current, FirebaseOptions expected) {
    return current.apiKey == expected.apiKey &&
        current.appId == expected.appId &&
        current.messagingSenderId == expected.messagingSenderId &&
        current.projectId == expected.projectId;
  }
}

final class FirebaseBootstrapResult {
  const FirebaseBootstrapResult({
    required this.app,
    required this.auth,
    required this.messagingAvailable,
  });

  final FirebaseApp app;
  final AuthSession auth;
  final bool messagingAvailable;
}

final class FirebaseAuthSession implements AuthSession {
  const FirebaseAuthSession(
    this._auth, {
    required GoogleIdentityAuthenticator googleIdentity,
    required String googleIosClientId,
    required String googleServerClientId,
    required TargetPlatform platform,
  }) : _googleIdentity = googleIdentity,
       _googleIosClientId = googleIosClientId,
       _googleServerClientId = googleServerClientId,
       _platform = platform;

  final FirebaseAuth _auth;
  final GoogleIdentityAuthenticator _googleIdentity;
  final String _googleIosClientId;
  final String _googleServerClientId;
  final TargetPlatform _platform;

  @override
  AuthUser? get currentUser => _profile(_auth.currentUser);

  @override
  Stream<AuthUser?> authStateChanges() {
    return _auth.authStateChanges().map(_profile);
  }

  @override
  Future<void> createAccount({
    required String email,
    required String password,
  }) async {
    try {
      await _auth.createUserWithEmailAndPassword(
        email: email,
        password: password,
      );
    } on FirebaseAuthException catch (error) {
      throw AuthFailure(_messageFor(error));
    }
  }

  @override
  Future<String> idToken() async {
    final user = _auth.currentUser;
    if (user == null) {
      throw const AuthFailure('Sign in before starting voice.');
    }
    final token = await user.getIdToken();
    if (token == null || token.isEmpty) {
      throw const AuthFailure('Your sign-in could not be refreshed.');
    }
    return token;
  }

  @override
  Future<void> resetPassword(String email) async {
    try {
      await _auth.sendPasswordResetEmail(email: email);
    } on FirebaseAuthException catch (error) {
      throw AuthFailure(_messageFor(error));
    }
  }

  @override
  Future<void> signIn({required String email, required String password}) async {
    try {
      await _auth.signInWithEmailAndPassword(email: email, password: password);
    } on FirebaseAuthException catch (error) {
      throw AuthFailure(_messageFor(error));
    }
  }

  @override
  Future<void> signInWithGoogle() async {
    try {
      final idToken = await _googleIdentity.authenticate(
        iosClientId: _googleIosClientId,
        platform: _platform,
        serverClientId: _googleServerClientId,
      );
      if (idToken == null) {
        return;
      }
      await _auth.signInWithCredential(
        GoogleAuthProvider.credential(idToken: idToken),
      );
    } on GoogleIdentityFailure catch (error) {
      throw AuthFailure(error.message);
    } on FirebaseAuthException catch (error) {
      throw AuthFailure(_messageFor(error));
    }
  }

  @override
  Future<void> signOut() => _auth.signOut();

  static AuthUser? _profile(User? user) {
    if (user == null) {
      return null;
    }
    return AuthUser(
      id: user.uid,
      displayName: user.displayName,
      email: user.email,
    );
  }

  static String _messageFor(FirebaseAuthException error) {
    return switch (error.code) {
      'email-already-in-use' => 'An account already uses that email.',
      'invalid-credential' ||
      'user-not-found' ||
      'wrong-password' => // pragma: allowlist secret
      'The email or password is incorrect.',
      'invalid-email' => 'Enter a valid email address.',
      'network-request-failed' => 'You appear to be offline. Try again.',
      'too-many-requests' => 'Too many attempts. Wait a moment and try again.',
      'weak-password' =>
        'Use a stronger password with at least six characters.',
      'user-disabled' => 'This account has been disabled.',
      _ =>
        error.message?.trim().isNotEmpty == true
            ? error.message!.trim()
            : 'Firebase sign-in failed. Try again.',
    };
  }
}
