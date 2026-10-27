final class ModelOption {
  const ModelOption({
    required this.description,
    required this.label,
    required this.model,
    required this.recommended,
  });

  factory ModelOption.fromJson(Map<String, Object?> json) {
    return ModelOption(
      description: _optionalString(json, 'description'),
      label: _requiredString(json, 'label'),
      model: _requiredString(json, 'model'),
      recommended: json['recommended'] == true,
    );
  }

  final String? description;
  final String label;
  final String model;
  final bool recommended;
}

final class ModelSelection {
  const ModelSelection({
    required this.available,
    required this.model,
    required this.optionLabel,
    required this.provider,
    required this.providerLabel,
    this.recommended = false,
  });

  final bool available;
  final String model;
  final String optionLabel;
  final String provider;
  final String providerLabel;
  final bool recommended;

  String get modelLabel =>
      optionLabel == model ? model : '$optionLabel — $model';

  String get providerModelLabel => '$providerLabel · $modelLabel';

  @override
  bool operator ==(Object other) {
    return other is ModelSelection &&
        other.provider == provider &&
        other.model == model;
  }

  @override
  int get hashCode => Object.hash(provider, model);
}

final class TextModelPreferences {
  const TextModelPreferences({
    required this.agentInstructions,
    required this.allowedSubagentModels,
    required this.imageGenerationModel,
    required this.imageGenerationProvider,
    required this.model,
    required this.preferredLanguage,
    required this.provider,
    required this.subagentModel,
    required this.temperature,
  });

  factory TextModelPreferences.fromJson(Map<String, Object?> json) {
    return TextModelPreferences(
      agentInstructions: _requiredString(json, 'agent_instructions'),
      allowedSubagentModels: _requiredStringList(
        json,
        'allowed_subagent_models',
      ),
      imageGenerationModel: _requiredString(json, 'image_generation_model'),
      imageGenerationProvider: _requiredString(
        json,
        'image_generation_provider',
      ),
      model: _requiredString(json, 'model'),
      preferredLanguage: _requiredString(json, 'preferred_language'),
      provider: _requiredString(json, 'provider'),
      subagentModel: _requiredString(json, 'subagent_model'),
      temperature: _optionalDouble(json, 'temperature'),
    );
  }

  final String agentInstructions;
  final List<String> allowedSubagentModels;
  final String imageGenerationModel;
  final String imageGenerationProvider;
  final String model;
  final String preferredLanguage;
  final String provider;
  final String subagentModel;
  final double? temperature;

  Map<String, Object?> requestBodyWithSelection({
    required ProviderModelCatalog selectedProvider,
    required ModelSelection selection,
  }) {
    final providerChanged = selection.provider != provider;
    final selectedAllowedSubagentModels = providerChanged
        ? selectedProvider.allowedSubagentModels
        : allowedSubagentModels;
    final selectedSubagentModel = providerChanged
        ? selectedProvider.defaultSubagentModel
        : subagentModel;
    return <String, Object?>{
      'provider': selection.provider,
      'model': selection.model,
      'preferred_language': preferredLanguage,
      'agent_instructions': agentInstructions,
      'temperature': providerChanged ? null : temperature,
      'allowed_subagent_models': selectedAllowedSubagentModels,
      'subagent_model': selectedSubagentModel,
    };
  }
}

final class VoiceModelPreferences {
  const VoiceModelPreferences({
    required this.agentInstructions,
    required this.initialReplyInstructions,
    required this.model,
    required this.preferredLanguage,
    required this.provider,
    required this.voice,
  });

  factory VoiceModelPreferences.fromJson(Map<String, Object?> json) {
    return VoiceModelPreferences(
      agentInstructions: _requiredString(json, 'agent_instructions'),
      initialReplyInstructions: _optionalString(
        json,
        'initial_reply_instructions',
      ),
      model: _requiredString(json, 'model'),
      preferredLanguage: _requiredString(json, 'preferred_language'),
      provider: _requiredString(json, 'provider'),
      voice: _requiredString(json, 'voice'),
    );
  }

  final String agentInstructions;
  final String? initialReplyInstructions;
  final String model;
  final String preferredLanguage;
  final String provider;
  final String voice;

  Map<String, Object?> requestBodyWithSelection({
    required ProviderModelCatalog selectedProvider,
    required ModelSelection selection,
  }) {
    return <String, Object?>{
      'provider': selection.provider,
      'model': selection.model,
      'voice': selection.provider == provider
          ? voice
          : selectedProvider.requiredDefaultVoice,
      'preferred_language': preferredLanguage,
      'agent_instructions': agentInstructions,
      'initial_reply_instructions': initialReplyInstructions,
    };
  }
}

final class ProviderModelCatalog {
  const ProviderModelCatalog({
    required this.allowedSubagentModels,
    required this.configured,
    required this.defaultImageModel,
    required this.defaultModel,
    required this.defaultSubagentModel,
    required this.defaultVoice,
    required this.imageModels,
    required this.label,
    required this.models,
    required this.provider,
    required this.runtimeSupported,
  });

  factory ProviderModelCatalog.fromJson(Map<String, Object?> json) {
    return ProviderModelCatalog(
      allowedSubagentModels: _optionalStringList(
        json,
        'allowed_subagent_models',
      ),
      configured: _requiredBool(json, 'configured'),
      defaultImageModel: _optionalString(
        json,
        'default_image_generation_model',
      ),
      defaultModel: _requiredString(json, 'default_model'),
      defaultSubagentModel: _optionalString(json, 'default_subagent_model'),
      defaultVoice: _optionalString(json, 'default_voice'),
      imageModels: _modelOptions(
        json,
        'image_generation_model_options',
        optional: true,
      ),
      label: _requiredString(json, 'label'),
      models: _modelOptions(json, 'model_options'),
      provider: _requiredString(json, 'provider'),
      runtimeSupported: _requiredBool(json, 'runtime_supported'),
    );
  }

  final List<String> allowedSubagentModels;
  final bool configured;
  final String? defaultImageModel;
  final String defaultModel;
  final String? defaultSubagentModel;
  final String? defaultVoice;
  final List<ModelOption> imageModels;
  final String label;
  final List<ModelOption> models;
  final String provider;
  final bool runtimeSupported;

  bool get available => configured && runtimeSupported;

  String get requiredDefaultVoice {
    final value = defaultVoice;
    if (value == null) {
      throw FormatException('Missing default voice for $provider.');
    }
    return value;
  }
}

final class UserModelSettings {
  const UserModelSettings({
    required this.textPreferences,
    required this.textProviders,
    required this.voicePreferences,
    required this.voiceProviders,
  });

  factory UserModelSettings.fromProviderResponses({
    required Map<String, Object?> text,
    required Map<String, Object?> voice,
  }) {
    final textSettings = _requiredMap(text, 'active_settings');
    final voiceSettings = _requiredMap(voice, 'active_settings');
    return UserModelSettings(
      textPreferences: TextModelPreferences.fromJson(textSettings),
      textProviders: _providerCatalogs(text),
      voicePreferences: VoiceModelPreferences.fromJson(voiceSettings),
      voiceProviders: _providerCatalogs(voice),
    );
  }

  final TextModelPreferences textPreferences;
  final List<ProviderModelCatalog> textProviders;
  final VoiceModelPreferences voicePreferences;
  final List<ProviderModelCatalog> voiceProviders;

  ProviderModelCatalog get activeTextProvider => _providerFor(
    textProviders,
    textPreferences.provider,
    settingsKind: 'text',
  );

  ProviderModelCatalog get activeVoiceProvider => _providerFor(
    voiceProviders,
    voicePreferences.provider,
    settingsKind: 'voice',
  );

  List<ProviderModelCatalog> get textProviderChoices => textProviders;

  List<ProviderModelCatalog> get imageProviderChoices => List.unmodifiable(
    textProviders.where(
      (provider) =>
          provider.imageModels.isNotEmpty ||
          provider.provider == textPreferences.imageGenerationProvider,
    ),
  );

  List<ProviderModelCatalog> get voiceProviderChoices => voiceProviders;

  ProviderModelCatalog textProvider(String provider) =>
      _providerFor(textProviders, provider, settingsKind: 'text');

  ProviderModelCatalog voiceProvider(String provider) =>
      _providerFor(voiceProviders, provider, settingsKind: 'voice');

  List<ModelSelection> get textModelChoices => _choicesForProvider(
    activeTextProvider,
    currentModel: textPreferences.model,
  );

  List<ModelSelection> textModelChoicesFor(String provider) {
    final catalog = textProvider(provider);
    return _choicesForProvider(
      catalog,
      currentModel: provider == textPreferences.provider
          ? textPreferences.model
          : catalog.defaultModel,
    );
  }

  List<ModelSelection> get voiceModelChoices => _choicesForProvider(
    activeVoiceProvider,
    currentModel: voicePreferences.model,
  );

  List<ModelSelection> voiceModelChoicesFor(String provider) {
    final catalog = voiceProvider(provider);
    return _choicesForProvider(
      catalog,
      currentModel: provider == voicePreferences.provider
          ? voicePreferences.model
          : catalog.defaultModel,
    );
  }

  List<ModelSelection> imageModelChoicesFor(String provider) {
    final catalog = textProvider(provider);
    final defaultModel = provider == textPreferences.imageGenerationProvider
        ? textPreferences.imageGenerationModel
        : _defaultImageModel(catalog);
    return _choicesForOptions(
      catalog,
      options: catalog.imageModels,
      currentModel: defaultModel,
    );
  }

  List<ModelSelection> get imageModelChoices {
    final choices = <ModelSelection>[];
    for (final provider in textProviders) {
      for (final option in provider.imageModels) {
        choices.add(_selection(provider, option));
      }
    }
    final current = ModelSelection(
      available: _providerAvailability(
        textProviders,
        textPreferences.imageGenerationProvider,
      ),
      model: textPreferences.imageGenerationModel,
      optionLabel: textPreferences.imageGenerationModel,
      provider: textPreferences.imageGenerationProvider,
      providerLabel: _providerLabel(
        textProviders,
        textPreferences.imageGenerationProvider,
      ),
    );
    if (!choices.contains(current)) {
      choices.insert(0, current);
    }
    return List<ModelSelection>.unmodifiable(choices);
  }

  ModelSelection get selectedTextModel => ModelSelection(
    available: activeTextProvider.available,
    model: textPreferences.model,
    optionLabel: _optionLabel(activeTextProvider.models, textPreferences.model),
    provider: textPreferences.provider,
    providerLabel: activeTextProvider.label,
  );

  ModelSelection get selectedImageModel => ModelSelection(
    available: _providerAvailability(
      textProviders,
      textPreferences.imageGenerationProvider,
    ),
    model: textPreferences.imageGenerationModel,
    optionLabel: _optionLabel(
      _providerFor(
        textProviders,
        textPreferences.imageGenerationProvider,
        settingsKind: 'image',
      ).imageModels,
      textPreferences.imageGenerationModel,
    ),
    provider: textPreferences.imageGenerationProvider,
    providerLabel: _providerLabel(
      textProviders,
      textPreferences.imageGenerationProvider,
    ),
  );

  ModelSelection get selectedVoiceModel => ModelSelection(
    available: activeVoiceProvider.available,
    model: voicePreferences.model,
    optionLabel: _optionLabel(
      activeVoiceProvider.models,
      voicePreferences.model,
    ),
    provider: voicePreferences.provider,
    providerLabel: activeVoiceProvider.label,
  );

  ModelSelection defaultTextModelFor(String provider) {
    final catalog = textProvider(provider);
    return _selectionForModel(
      catalog,
      options: catalog.models,
      model: catalog.defaultModel,
    );
  }

  ModelSelection defaultImageModelFor(String provider) {
    final catalog = textProvider(provider);
    return _selectionForModel(
      catalog,
      options: catalog.imageModels,
      model: _defaultImageModel(catalog),
    );
  }

  ModelSelection defaultVoiceModelFor(String provider) {
    final catalog = voiceProvider(provider);
    return _selectionForModel(
      catalog,
      options: catalog.models,
      model: catalog.defaultModel,
    );
  }
}

ProviderModelCatalog _providerFor(
  List<ProviderModelCatalog> providers,
  String provider, {
  required String settingsKind,
}) {
  for (final candidate in providers) {
    if (candidate.provider == provider) {
      return candidate;
    }
  }
  throw FormatException('Missing active $settingsKind provider.');
}

List<ModelSelection> _choicesForProvider(
  ProviderModelCatalog provider, {
  required String currentModel,
}) {
  return _choicesForOptions(
    provider,
    options: provider.models,
    currentModel: currentModel,
  );
}

List<ModelSelection> _choicesForOptions(
  ProviderModelCatalog provider, {
  required List<ModelOption> options,
  required String currentModel,
}) {
  final choices = options
      .map((option) => _selection(provider, option))
      .toList();
  final current = ModelSelection(
    available: provider.available,
    model: currentModel,
    optionLabel: currentModel,
    provider: provider.provider,
    providerLabel: provider.label,
  );
  if (!choices.contains(current)) {
    choices.insert(0, current);
  }
  return List<ModelSelection>.unmodifiable(choices);
}

ModelSelection _selectionForModel(
  ProviderModelCatalog provider, {
  required List<ModelOption> options,
  required String model,
}) {
  return ModelSelection(
    available: provider.available,
    model: model,
    optionLabel: _optionLabel(options, model),
    provider: provider.provider,
    providerLabel: provider.label,
  );
}

String _defaultImageModel(ProviderModelCatalog provider) {
  final configured = provider.defaultImageModel;
  if (configured != null) {
    return configured;
  }
  if (provider.imageModels.isNotEmpty) {
    return provider.imageModels.first.model;
  }
  throw FormatException(
    'Missing image model catalog for ${provider.provider}.',
  );
}

ModelSelection _selection(ProviderModelCatalog provider, ModelOption option) {
  return ModelSelection(
    available: provider.available,
    model: option.model,
    optionLabel: option.label,
    provider: provider.provider,
    providerLabel: provider.label,
    recommended: option.recommended,
  );
}

String _optionLabel(List<ModelOption> options, String model) {
  for (final option in options) {
    if (option.model == model) {
      return option.label;
    }
  }
  return model;
}

String _providerLabel(List<ProviderModelCatalog> providers, String provider) {
  return _providerFor(providers, provider, settingsKind: 'model').label;
}

bool _providerAvailability(
  List<ProviderModelCatalog> providers,
  String provider,
) {
  return _providerFor(providers, provider, settingsKind: 'model').available;
}

List<ProviderModelCatalog> _providerCatalogs(Map<String, Object?> json) {
  final values = json['providers'];
  if (values is! List<Object?>) {
    throw const FormatException('Invalid provider catalog.');
  }
  return List<ProviderModelCatalog>.unmodifiable(
    values.map((value) {
      if (value is! Map<String, Object?>) {
        throw const FormatException('Invalid provider catalog entry.');
      }
      return ProviderModelCatalog.fromJson(value);
    }),
  );
}

List<ModelOption> _modelOptions(
  Map<String, Object?> json,
  String field, {
  bool optional = false,
}) {
  final values = json[field];
  if (values == null && optional) {
    return const <ModelOption>[];
  }
  if (values is! List<Object?>) {
    throw FormatException('Invalid $field.');
  }
  return List<ModelOption>.unmodifiable(
    values.map((value) {
      if (value is! Map<String, Object?>) {
        throw FormatException('Invalid $field entry.');
      }
      return ModelOption.fromJson(value);
    }),
  );
}

Map<String, Object?> _requiredMap(Map<String, Object?> json, String field) {
  final value = json[field];
  if (value is! Map<String, Object?>) {
    throw FormatException('Invalid $field.');
  }
  return value;
}

List<String> _requiredStringList(Map<String, Object?> json, String field) {
  final value = json[field];
  if (value is! List<Object?> || value.any((item) => item is! String)) {
    throw FormatException('Invalid $field.');
  }
  return List<String>.unmodifiable(value.cast<String>());
}

List<String> _optionalStringList(Map<String, Object?> json, String field) {
  if (json[field] == null) {
    return const <String>[];
  }
  return _requiredStringList(json, field);
}

String _requiredString(Map<String, Object?> json, String field) {
  final value = json[field];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Invalid $field.');
  }
  return value;
}

String? _optionalString(Map<String, Object?> json, String field) {
  final value = json[field];
  if (value == null) {
    return null;
  }
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Invalid $field.');
  }
  return value;
}

bool _requiredBool(Map<String, Object?> json, String field) {
  final value = json[field];
  if (value is! bool) {
    throw FormatException('Invalid $field.');
  }
  return value;
}

double? _optionalDouble(Map<String, Object?> json, String field) {
  final value = json[field];
  if (value == null) {
    return null;
  }
  if (value is! num) {
    throw FormatException('Invalid $field.');
  }
  return value.toDouble();
}
