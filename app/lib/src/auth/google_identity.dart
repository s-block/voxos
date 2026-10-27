import 'package:flutter/foundation.dart';
import 'package:google_sign_in/google_sign_in.dart';

final class GoogleIdentityFailure implements Exception {
  const GoogleIdentityFailure(this.message);

  final String message;

  @override
  String toString() => message;
}

abstract interface class GoogleIdentityAuthenticator {
  Future<String?> authenticate({
    required String iosClientId,
    required TargetPlatform platform,
    required String serverClientId,
  });
}

abstract interface class GoogleSignInClient {
  Future<String?> authenticate();

  Future<void> initialize({String? clientId, String? serverClientId});
}

final class NativeGoogleSignInClient implements GoogleSignInClient {
  const NativeGoogleSignInClient();

  @override
  Future<String?> authenticate() async {
    final account = await GoogleSignIn.instance.authenticate();
    return account.authentication.idToken;
  }

  @override
  Future<void> initialize({String? clientId, String? serverClientId}) {
    return GoogleSignIn.instance.initialize(
      clientId: clientId,
      serverClientId: serverClientId,
    );
  }
}

final class NativeGoogleIdentityAuthenticator
    implements GoogleIdentityAuthenticator {
  NativeGoogleIdentityAuthenticator({GoogleSignInClient? client})
    : _client = client ?? const NativeGoogleSignInClient();

  final GoogleSignInClient _client;
  _GoogleIdentityConfiguration? _configuration;
  Future<void>? _initialization;

  @override
  Future<String?> authenticate({
    required String iosClientId,
    required TargetPlatform platform,
    required String serverClientId,
  }) async {
    final configuration = _GoogleIdentityConfiguration(
      clientId: switch (platform) {
        TargetPlatform.iOS => iosClientId,
        TargetPlatform.android => null,
        _ => throw const GoogleIdentityFailure(
          'Google sign-in is available on iPhone and Android only.',
        ),
      },
      serverClientId: serverClientId,
    );
    final existingConfiguration = _configuration;
    if (existingConfiguration != null &&
        existingConfiguration != configuration) {
      throw const GoogleIdentityFailure(
        'Restart Voxos before using Google sign-in with a different server.',
      );
    }
    _configuration = configuration;

    try {
      await (_initialization ??= _client.initialize(
        clientId: configuration.clientId,
        serverClientId: configuration.serverClientId,
      ));
      final idToken = await _client.authenticate();
      if (idToken == null || idToken.trim().isEmpty) {
        throw const GoogleIdentityFailure(
          'Google did not return a valid sign-in token. Try again.',
        );
      }
      return idToken;
    } on GoogleSignInException catch (error) {
      if (error.code == GoogleSignInExceptionCode.canceled) {
        return null;
      }
      throw GoogleIdentityFailure(_messageFor(error.code));
    }
  }

  static String _messageFor(GoogleSignInExceptionCode code) {
    return switch (code) {
      GoogleSignInExceptionCode.clientConfigurationError ||
      GoogleSignInExceptionCode.providerConfigurationError =>
        'Google sign-in is not configured correctly for this app.',
      GoogleSignInExceptionCode.interrupted ||
      GoogleSignInExceptionCode.uiUnavailable =>
        'Google sign-in could not open. Try again.',
      GoogleSignInExceptionCode.userMismatch =>
        'Sign out of Google and try the account again.',
      _ => 'Google sign-in failed. Try again.',
    };
  }
}

final class _GoogleIdentityConfiguration {
  const _GoogleIdentityConfiguration({
    required this.clientId,
    required this.serverClientId,
  });

  final String? clientId;
  final String serverClientId;

  @override
  bool operator ==(Object other) {
    return other is _GoogleIdentityConfiguration &&
        other.clientId == clientId &&
        other.serverClientId == serverClientId;
  }

  @override
  int get hashCode => Object.hash(clientId, serverClientId);
}
