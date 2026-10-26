import 'package:voxos/src/settings/model_settings.dart';

abstract interface class ModelSettingsApi {
  Future<UserModelSettings> loadModelSettings();

  Future<void> updateImageModel(ModelSelection selection);

  Future<void> updateTextModel({
    required TextModelPreferences current,
    required ProviderModelCatalog provider,
    required ModelSelection selection,
  });

  Future<void> updateVoiceModel({
    required VoiceModelPreferences current,
    required ProviderModelCatalog provider,
    required ModelSelection selection,
  });

  void dispose();
}
