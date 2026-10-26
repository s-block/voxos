import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:voxos/src/auth/google_identity.dart';

void main() {
  test('initializes Google once and returns native ID tokens', () async {
    final client = _FakeGoogleSignInClient(idToken: 'google-id-token');
    final authenticator = NativeGoogleIdentityAuthenticator(client: client);

    final first = await authenticator.authenticate(
      iosClientId: 'ios-client-id',
      platform: TargetPlatform.iOS,
      serverClientId: 'server-client-id',
    );
    final second = await authenticator.authenticate(
      iosClientId: 'ios-client-id',
      platform: TargetPlatform.iOS,
      serverClientId: 'server-client-id',
    );

    expect(first, 'google-id-token');
    expect(second, 'google-id-token');
    expect(client.initializeCount, 1);
    expect(client.authenticateCount, 2);
    expect(client.clientId, 'ios-client-id');
    expect(client.serverClientId, 'server-client-id');
  });

  test(
    'uses the server client ID without an iOS client ID on Android',
    () async {
      final client = _FakeGoogleSignInClient(idToken: 'google-id-token');
      final authenticator = NativeGoogleIdentityAuthenticator(client: client);

      await authenticator.authenticate(
        iosClientId: 'ios-client-id',
        platform: TargetPlatform.android,
        serverClientId: 'server-client-id',
      );

      expect(client.clientId, isNull);
      expect(client.serverClientId, 'server-client-id');
    },
  );

  test('treats closing the Google chooser as cancellation', () async {
    final client = _FakeGoogleSignInClient(
      error: const GoogleSignInException(
        code: GoogleSignInExceptionCode.canceled,
      ),
    );
    final authenticator = NativeGoogleIdentityAuthenticator(client: client);

    final result = await authenticator.authenticate(
      iosClientId: 'ios-client-id',
      platform: TargetPlatform.iOS,
      serverClientId: 'server-client-id',
    );

    expect(result, isNull);
  });

  test('reports invalid Google client configuration safely', () async {
    final client = _FakeGoogleSignInClient(
      error: const GoogleSignInException(
        code: GoogleSignInExceptionCode.clientConfigurationError,
        description: 'sensitive native detail',
      ),
    );
    final authenticator = NativeGoogleIdentityAuthenticator(client: client);

    await expectLater(
      authenticator.authenticate(
        iosClientId: 'ios-client-id',
        platform: TargetPlatform.iOS,
        serverClientId: 'server-client-id',
      ),
      throwsA(
        isA<GoogleIdentityFailure>().having(
          (failure) => failure.message,
          'message',
          'Google sign-in is not configured correctly for this app.',
        ),
      ),
    );
  });

  test('rejects changing OAuth clients after Google is initialized', () async {
    final client = _FakeGoogleSignInClient(idToken: 'google-id-token');
    final authenticator = NativeGoogleIdentityAuthenticator(client: client);
    await authenticator.authenticate(
      iosClientId: 'first-ios-client-id',
      platform: TargetPlatform.iOS,
      serverClientId: 'first-server-client-id',
    );

    await expectLater(
      authenticator.authenticate(
        iosClientId: 'second-ios-client-id',
        platform: TargetPlatform.iOS,
        serverClientId: 'second-server-client-id',
      ),
      throwsA(isA<GoogleIdentityFailure>()),
    );
    expect(client.initializeCount, 1);
  });
}

final class _FakeGoogleSignInClient implements GoogleSignInClient {
  _FakeGoogleSignInClient({this.error, this.idToken});

  final GoogleSignInException? error;
  final String? idToken;
  int authenticateCount = 0;
  int initializeCount = 0;
  String? clientId;
  String? serverClientId;

  @override
  Future<String?> authenticate() async {
    authenticateCount++;
    final authenticationError = error;
    if (authenticationError != null) {
      throw authenticationError;
    }
    return idToken;
  }

  @override
  Future<void> initialize({String? clientId, String? serverClientId}) async {
    initializeCount++;
    this.clientId = clientId;
    this.serverClientId = serverClientId;
  }
}
