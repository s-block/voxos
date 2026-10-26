import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/screens/user_settings_screen.dart';
import 'package:voxos/src/settings/model_settings.dart';
import 'package:voxos/src/settings/model_settings_api.dart';
import 'package:voxos/src/theme/voxos_theme.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{});
  });

  testWidgets('loads and saves text, image, and voice model defaults', (
    tester,
  ) async {
    final api = _FakeModelSettingsApi(_settings());

    await tester.pumpWidget(
      MaterialApp(
        theme: VoxosTheme.light(),
        home: UserSettingsScreen(api: api),
      ),
    );
    await tester.pumpAndSettle();

    expect(api.loadCount, 1);
    expect(find.text('Text model'), findsOneWidget);
    expect(find.text('Image model'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey<String>('save-model-defaults')),
          )
          .onPressed,
      isNull,
    );

    await _selectDropdown(
      tester,
      key: const ValueKey<String>('default-text-provider'),
      label: 'Google',
    );
    await _selectDropdown(
      tester,
      key: const ValueKey<String>('default-text-model'),
      label: 'Gemini 2.5 Pro — gemini-2.5-pro',
    );
    await _selectDropdown(
      tester,
      key: const ValueKey<String>('default-image-provider'),
      label: 'Google',
    );
    await _selectDropdown(
      tester,
      key: const ValueKey<String>('default-voice-provider'),
      label: 'Google',
    );

    final saveButton = find.byKey(
      const ValueKey<String>('save-model-defaults'),
    );
    expect(tester.widget<FilledButton>(saveButton).onPressed, isNotNull);
    await tester.tap(saveButton);
    await tester.pumpAndSettle();

    expect(api.textModel, 'gemini-2.5-pro');
    expect(api.textProvider, 'google_gemini');
    expect(api.imageModel, 'imagen-4.0-generate-001');
    expect(api.imageProvider, 'google_gemini');
    expect(api.voiceModel, 'gemini-3.1-flash-live-preview');
    expect(api.voiceProvider, 'google_gemini_live');
    expect(find.text('Model defaults saved.'), findsOneWidget);
    expect(tester.widget<FilledButton>(saveButton).onPressed, isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    expect(api.disposed, isTrue);
  });

  testWidgets('enables foreground location from settings', (tester) async {
    final preferences = await SharedPreferences.getInstance();
    final source = _FakeLocationSource();
    final locationSharing = LocationSharingController(
      preferences: preferences,
      source: source,
      storageKey: 'location-key',
    );
    addTearDown(locationSharing.dispose);

    await tester.pumpWidget(
      MaterialApp(
        theme: VoxosTheme.light(),
        home: UserSettingsScreen(
          api: _FakeModelSettingsApi(_settings()),
          locationSharing: locationSharing,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(
      find.byKey(const ValueKey<String>('share-current-location')),
    );
    await tester.pumpAndSettle();

    expect(source.captureCount, 1);
    expect(locationSharing.enabled, isTrue);
    expect(preferences.getBool('location-key'), isTrue);
  });
}

final class _FakeLocationSource implements DeviceLocationSource {
  int captureCount = 0;

  @override
  Future<AgentLocationSnapshot> capture() async {
    captureCount += 1;
    return AgentLocationSnapshot(
      capturedAt: DateTime.utc(2026, 8, 30, 12),
      horizontalAccuracyM: 25,
      latitude: 42.4304,
      longitude: 19.2594,
    );
  }
}

Future<void> _selectDropdown(
  WidgetTester tester, {
  required Key key,
  required String label,
}) async {
  final dropdown = find.byKey(key);
  if (dropdown.evaluate().isEmpty) {
    await tester.scrollUntilVisible(
      dropdown,
      300,
      scrollable: find.byKey(const ValueKey<String>('model-settings-list')),
    );
  } else {
    await tester.ensureVisible(dropdown);
  }
  await tester.pumpAndSettle();
  await tester.tap(dropdown);
  await tester.pumpAndSettle();
  await tester.tap(find.text(label).last);
  await tester.pumpAndSettle();
}

UserModelSettings _settings() {
  return UserModelSettings(
    textPreferences: const TextModelPreferences(
      agentInstructions: 'Keep text concise.',
      allowedSubagentModels: <String>['gpt-5', 'gpt-5-mini'],
      imageGenerationModel: 'gpt-image-1',
      imageGenerationProvider: 'openai',
      model: 'gpt-5',
      preferredLanguage: 'English',
      provider: 'openai',
      subagentModel: 'gpt-5-mini',
      temperature: 0.4,
    ),
    textProviders: const <ProviderModelCatalog>[
      ProviderModelCatalog(
        allowedSubagentModels: <String>['gpt-5', 'gpt-5-mini'],
        configured: true,
        defaultImageModel: 'gpt-image-1',
        defaultModel: 'gpt-5',
        defaultSubagentModel: 'gpt-5-mini',
        defaultVoice: null,
        imageModels: <ModelOption>[
          ModelOption(
            description: 'OpenAI image model',
            label: 'GPT Image 1',
            model: 'gpt-image-1',
            recommended: true,
          ),
        ],
        label: 'OpenAI',
        models: <ModelOption>[
          ModelOption(
            description: 'Default text model',
            label: 'GPT-5',
            model: 'gpt-5',
            recommended: true,
          ),
          ModelOption(
            description: 'Faster text model',
            label: 'GPT-5 mini',
            model: 'gpt-5-mini',
            recommended: false,
          ),
        ],
        provider: 'openai',
        runtimeSupported: true,
      ),
      ProviderModelCatalog(
        allowedSubagentModels: <String>['gemini-2.5-flash', 'gemini-2.5-pro'],
        configured: true,
        defaultImageModel: 'imagen-4.0-generate-001',
        defaultModel: 'gemini-2.5-flash',
        defaultSubagentModel: 'gemini-2.5-flash',
        defaultVoice: null,
        imageModels: <ModelOption>[
          ModelOption(
            description: 'Google image model',
            label: 'Imagen 4',
            model: 'imagen-4.0-generate-001',
            recommended: true,
          ),
        ],
        label: 'Google',
        models: <ModelOption>[
          ModelOption(
            description: 'Default Google text model',
            label: 'Gemini 2.5 Flash',
            model: 'gemini-2.5-flash',
            recommended: true,
          ),
          ModelOption(
            description: 'Larger Google text model',
            label: 'Gemini 2.5 Pro',
            model: 'gemini-2.5-pro',
            recommended: false,
          ),
        ],
        provider: 'google_gemini',
        runtimeSupported: true,
      ),
    ],
    voicePreferences: const VoiceModelPreferences(
      agentInstructions: 'Keep voice answers brief.',
      initialReplyInstructions: 'Say hello.',
      model: 'gpt-realtime',
      preferredLanguage: 'British English',
      provider: 'openai_realtime',
      voice: 'marin',
    ),
    voiceProviders: const <ProviderModelCatalog>[
      ProviderModelCatalog(
        allowedSubagentModels: <String>[],
        configured: true,
        defaultImageModel: null,
        defaultModel: 'gpt-realtime',
        defaultSubagentModel: null,
        defaultVoice: 'marin',
        imageModels: <ModelOption>[],
        label: 'OpenAI',
        models: <ModelOption>[
          ModelOption(
            description: 'Default voice model',
            label: 'GPT Realtime',
            model: 'gpt-realtime',
            recommended: true,
          ),
          ModelOption(
            description: 'Faster voice model',
            label: 'GPT Realtime mini',
            model: 'gpt-realtime-mini',
            recommended: false,
          ),
        ],
        provider: 'openai_realtime',
        runtimeSupported: true,
      ),
      ProviderModelCatalog(
        allowedSubagentModels: <String>[],
        configured: true,
        defaultImageModel: null,
        defaultModel: 'gemini-3.1-flash-live-preview',
        defaultSubagentModel: null,
        defaultVoice: 'Puck',
        imageModels: <ModelOption>[],
        label: 'Google',
        models: <ModelOption>[
          ModelOption(
            description: 'Default Google voice model',
            label: 'Gemini 3.1 Flash Live',
            model: 'gemini-3.1-flash-live-preview',
            recommended: true,
          ),
        ],
        provider: 'google_gemini_live',
        runtimeSupported: true,
      ),
    ],
  );
}

final class _FakeModelSettingsApi implements ModelSettingsApi {
  _FakeModelSettingsApi(this.settings);

  final UserModelSettings settings;
  bool disposed = false;
  String? imageModel;
  String? imageProvider;
  int loadCount = 0;
  String? textModel;
  String? textProvider;
  String? voiceModel;
  String? voiceProvider;

  @override
  void dispose() {
    disposed = true;
  }

  @override
  Future<UserModelSettings> loadModelSettings() async {
    loadCount += 1;
    return settings;
  }

  @override
  Future<void> updateImageModel(ModelSelection selection) async {
    imageModel = selection.model;
    imageProvider = selection.provider;
  }

  @override
  Future<void> updateTextModel({
    required TextModelPreferences current,
    required ProviderModelCatalog provider,
    required ModelSelection selection,
  }) async {
    textModel = selection.model;
    textProvider = selection.provider;
  }

  @override
  Future<void> updateVoiceModel({
    required VoiceModelPreferences current,
    required ProviderModelCatalog provider,
    required ModelSelection selection,
  }) async {
    voiceModel = selection.model;
    voiceProvider = selection.provider;
  }
}
