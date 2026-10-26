import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/screens/setup_screen.dart';

import '../support/config_fixtures.dart';

void main() {
  testWidgets('shows saved servers and can select one', (tester) async {
    String? selectedEndpoint;
    final connection = _connection('https://one.example.com');

    await tester.pumpWidget(
      MaterialApp(
        home: SetupScreen(
          connections: <ServerConnection>[connection],
          onConnect: (endpoint) async => selectedEndpoint = endpoint,
        ),
      ),
    );

    expect(find.text('Choose a server'), findsOneWidget);
    expect(find.text('one.example.com'), findsOneWidget);
    expect(
      find.byKey(const ValueKey<String>('server-address-field')),
      findsNothing,
    );

    await tester.tap(
      find.byKey(const ValueKey<String>('server-https://one.example.com')),
    );
    await tester.pumpAndSettle();

    expect(selectedEndpoint, 'https://one.example.com');
  });

  testWidgets('adds another server from the saved-server list', (tester) async {
    String? selectedEndpoint;

    await tester.pumpWidget(
      MaterialApp(
        home: SetupScreen(
          connections: <ServerConnection>[
            _connection('https://one.example.com'),
          ],
          initialEndpoint: 'https://one.example.com',
          onConnect: (endpoint) async => selectedEndpoint = endpoint,
        ),
      ),
    );

    await tester.tap(find.byKey(const ValueKey<String>('add-server-button')));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<TextField>(
            find.byKey(const ValueKey<String>('server-address-field')),
          )
          .controller
          ?.text,
      isEmpty,
    );
    await tester.enterText(
      find.byKey(const ValueKey<String>('server-address-field')),
      'https://two.example.com',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Continue'));
    await tester.pumpAndSettle();

    expect(selectedEndpoint, 'https://two.example.com');
  });
}

ServerConnection _connection(String endpoint) {
  return ServerConnection(
    baseUri: Uri.parse(endpoint),
    config: ServerConfig.fromJson(clientConfigJson()),
  );
}
