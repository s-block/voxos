import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/main.dart' as application;
import 'package:voxos/src/screens/setup_screen.dart';

void main() {
  testWidgets(
    'first launch reaches server setup without native Firebase config',
    (tester) async {
      SharedPreferences.setMockInitialValues({});

      await application.main();
      await tester.pumpAndSettle();

      expect(find.byType(SetupScreen), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
