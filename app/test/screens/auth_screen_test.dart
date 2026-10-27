import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/screens/auth_screen.dart';

import '../support/fakes.dart';

void main() {
  testWidgets('starts Google sign-in from the auth screen', (tester) async {
    final auth = FakeAuthSession();
    addTearDown(auth.dispose);
    await tester.pumpWidget(_authApp(auth));

    final googleButton = find.byKey(
      const ValueKey<String>('google-sign-in-button'),
    );
    await tester.ensureVisible(googleButton);
    await tester.pumpAndSettle();
    await tester.tap(googleButton);
    await tester.pumpAndSettle();

    expect(auth.googleSignInCount, 1);
  });

  testWidgets('shows a safe Google sign-in failure', (tester) async {
    final auth = FakeAuthSession(
      googleSignInError: const AuthFailure('Google sign-in is unavailable.'),
    );
    addTearDown(auth.dispose);
    await tester.pumpWidget(_authApp(auth));

    final googleButton = find.byKey(
      const ValueKey<String>('google-sign-in-button'),
    );
    await tester.ensureVisible(googleButton);
    await tester.pumpAndSettle();
    await tester.tap(googleButton);
    await tester.pumpAndSettle();

    expect(find.text('Google sign-in is unavailable.'), findsOneWidget);
  });

  testWidgets('opens server selection while signed out', (tester) async {
    final auth = FakeAuthSession();
    addTearDown(auth.dispose);
    var changeCount = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: AuthScreen(
          auth: auth,
          onChangeServer: () async => changeCount += 1,
          serverLabel: 'voxos.example.com',
        ),
      ),
    );

    final changeButton = find.widgetWithText(TextButton, 'Change');
    await tester.ensureVisible(changeButton);
    await tester.pumpAndSettle();
    await tester.tap(changeButton);
    await tester.pumpAndSettle();

    expect(changeCount, 1);
  });
}

Widget _authApp(FakeAuthSession auth) {
  return MaterialApp(
    home: AuthScreen(
      auth: auth,
      onChangeServer: () async {},
      serverLabel: 'voxos.example.com',
    ),
  );
}
