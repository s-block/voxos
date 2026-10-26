import 'package:flutter/material.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/settings/model_settings.dart';
import 'package:voxos/src/settings/model_settings_api.dart';

final class UserSettingsScreen extends StatefulWidget {
  const UserSettingsScreen({
    required this.api,
    this.locationSharing,
    super.key,
  });

  final ModelSettingsApi api;
  final LocationSharingController? locationSharing;

  static MaterialPageRoute<void> route(
    ModelSettingsApi api, {
    LocationSharingController? locationSharing,
  }) {
    return MaterialPageRoute<void>(
      builder: (_) =>
          UserSettingsScreen(api: api, locationSharing: locationSharing),
    );
  }

  @override
  State<UserSettingsScreen> createState() => _UserSettingsScreenState();
}

final class _UserSettingsScreenState extends State<UserSettingsScreen> {
  UserModelSettings? _settings;
  ModelSelection? _savedImageModel;
  ModelSelection? _savedTextModel;
  ModelSelection? _savedVoiceModel;
  ModelSelection? _selectedImageModel;
  ModelSelection? _selectedTextModel;
  ModelSelection? _selectedVoiceModel;
  String? _error;
  bool _isLoading = true;
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    widget.api.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: _buildBody(),
      bottomNavigationBar: _settings == null ? null : _buildSaveBar(),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }
    final settings = _settings;
    if (settings == null) {
      return _LoadFailure(message: _error!, onRetry: _load);
    }
    return ListView(
      key: const ValueKey<String>('model-settings-list'),
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
      children: <Widget>[
        if (widget.locationSharing case final locationSharing?) ...<Widget>[
          _LocationSharingCard(controller: locationSharing),
          const SizedBox(height: 24),
        ],
        Text(
          'Model defaults',
          style: Theme.of(
            context,
          ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 6),
        Text(
          'Choose a configured provider and its default model. Text and voice '
          'changes apply to new chats and calls; image changes apply to the '
          'next image tool run.',
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
            color: Theme.of(context).colorScheme.onSurfaceVariant,
          ),
        ),
        const SizedBox(height: 20),
        _ModelSettingsCard(
          description: 'Used when you start a new text chat.',
          dropdownKey: const ValueKey<String>('default-text-model'),
          icon: Icons.chat_bubble_outline_rounded,
          label: 'Text model',
          choices: settings.textModelChoicesFor(_selectedTextModel!.provider),
          providerChoices: settings.textProviderChoices,
          providerDropdownKey: const ValueKey<String>('default-text-provider'),
          selected: _selectedTextModel!,
          selectedProvider: settings.textProvider(_selectedTextModel!.provider),
          onProviderChanged: (provider) {
            setState(() {
              _selectedTextModel = settings.defaultTextModelFor(
                provider.provider,
              );
            });
          },
          onChanged: (selection) {
            setState(() => _selectedTextModel = selection);
          },
        ),
        const SizedBox(height: 14),
        _ModelSettingsCard(
          description: 'Used by image generation and editing tools.',
          dropdownKey: const ValueKey<String>('default-image-model'),
          icon: Icons.image_outlined,
          label: 'Image model',
          choices: settings.imageModelChoicesFor(_selectedImageModel!.provider),
          providerChoices: settings.imageProviderChoices,
          providerDropdownKey: const ValueKey<String>('default-image-provider'),
          selected: _selectedImageModel!,
          selectedProvider: settings.textProvider(
            _selectedImageModel!.provider,
          ),
          onProviderChanged: (provider) {
            setState(() {
              _selectedImageModel = settings.defaultImageModelFor(
                provider.provider,
              );
            });
          },
          onChanged: (selection) {
            setState(() => _selectedImageModel = selection);
          },
        ),
        const SizedBox(height: 14),
        _ModelSettingsCard(
          description: 'Used when you start a new voice call.',
          dropdownKey: const ValueKey<String>('default-voice-model'),
          icon: Icons.graphic_eq_rounded,
          label: 'Voice model',
          choices: settings.voiceModelChoicesFor(_selectedVoiceModel!.provider),
          providerChoices: settings.voiceProviderChoices,
          providerDropdownKey: const ValueKey<String>('default-voice-provider'),
          selected: _selectedVoiceModel!,
          selectedProvider: settings.voiceProvider(
            _selectedVoiceModel!.provider,
          ),
          onProviderChanged: (provider) {
            setState(() {
              _selectedVoiceModel = settings.defaultVoiceModelFor(
                provider.provider,
              );
            });
          },
          onChanged: (selection) {
            setState(() => _selectedVoiceModel = selection);
          },
        ),
        if (_error case final error?) ...<Widget>[
          const SizedBox(height: 16),
          _InlineError(message: error),
        ],
      ],
    );
  }

  Widget _buildSaveBar() {
    final colors = Theme.of(context).colorScheme;
    return Material(
      color: colors.surface,
      elevation: 8,
      child: SafeArea(
        minimum: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        child: FilledButton.icon(
          key: const ValueKey<String>('save-model-defaults'),
          onPressed: _canSave ? _save : null,
          icon: _isSaving
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.check_rounded),
          label: Text(_isSaving ? 'Saving…' : 'Save defaults'),
        ),
      ),
    );
  }

  bool get _canSave {
    if (_isSaving || !_isDirty) {
      return false;
    }
    return (_selectedTextModel == _savedTextModel ||
            _selectedTextModel!.available) &&
        (_selectedImageModel == _savedImageModel ||
            _selectedImageModel!.available) &&
        (_selectedVoiceModel == _savedVoiceModel ||
            _selectedVoiceModel!.available);
  }

  bool get _isDirty {
    return _selectedTextModel != _savedTextModel ||
        _selectedImageModel != _savedImageModel ||
        _selectedVoiceModel != _savedVoiceModel;
  }

  Future<void> _load() async {
    setState(() {
      _error = null;
      _isLoading = true;
    });
    try {
      final settings = await widget.api.loadModelSettings();
      if (!mounted) {
        return;
      }
      final textModel = settings.selectedTextModel;
      final imageModel = settings.selectedImageModel;
      final voiceModel = settings.selectedVoiceModel;
      setState(() {
        _settings = settings;
        _savedTextModel = textModel;
        _savedImageModel = imageModel;
        _savedVoiceModel = voiceModel;
        _selectedTextModel = textModel;
        _selectedImageModel = imageModel;
        _selectedVoiceModel = voiceModel;
        _isLoading = false;
      });
    } on Exception catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = error.toString();
        _isLoading = false;
      });
    }
  }

  Future<void> _save() async {
    final settings = _settings!;
    setState(() {
      _error = null;
      _isSaving = true;
    });
    try {
      if (_selectedTextModel != _savedTextModel) {
        await widget.api.updateTextModel(
          current: settings.textPreferences,
          provider: settings.textProvider(_selectedTextModel!.provider),
          selection: _selectedTextModel!,
        );
        _savedTextModel = _selectedTextModel;
      }
      if (_selectedImageModel != _savedImageModel) {
        await widget.api.updateImageModel(_selectedImageModel!);
        _savedImageModel = _selectedImageModel;
      }
      if (_selectedVoiceModel != _savedVoiceModel) {
        await widget.api.updateVoiceModel(
          current: settings.voicePreferences,
          provider: settings.voiceProvider(_selectedVoiceModel!.provider),
          selection: _selectedVoiceModel!,
        );
        _savedVoiceModel = _selectedVoiceModel;
      }
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(const SnackBar(content: Text('Model defaults saved.')));
    } on Exception catch (error) {
      if (mounted) {
        _error = error.toString();
      }
    } finally {
      if (mounted) {
        setState(() => _isSaving = false);
      }
    }
  }
}

final class _LocationSharingCard extends StatelessWidget {
  const _LocationSharingCard({required this.controller});

  final LocationSharingController controller;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final colors = Theme.of(context).colorScheme;
        return Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                SwitchListTile.adaptive(
                  contentPadding: EdgeInsets.zero,
                  key: const ValueKey<String>('share-current-location'),
                  onChanged: controller.isWorking
                      ? null
                      : controller.setEnabled,
                  secondary: const Icon(Icons.location_on_outlined),
                  subtitle: const Text(
                    'Request your foreground location before each message and '
                    'when a voice call starts. Location failures never block chat or calls.',
                  ),
                  title: const Text('Location for your agent'),
                  value: controller.enabled,
                ),
                if (controller.isWorking)
                  const Padding(
                    padding: EdgeInsets.only(top: 4, right: 8),
                    child: LinearProgressIndicator(),
                  ),
                if (controller.error case final error?)
                  Padding(
                    padding: const EdgeInsets.only(top: 8, right: 8),
                    child: Text(
                      error,
                      style: Theme.of(
                        context,
                      ).textTheme.bodySmall?.copyWith(color: colors.error),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }
}

final class _ModelSettingsCard extends StatelessWidget {
  const _ModelSettingsCard({
    required this.choices,
    required this.description,
    required this.dropdownKey,
    required this.icon,
    required this.label,
    required this.onChanged,
    required this.onProviderChanged,
    required this.providerChoices,
    required this.providerDropdownKey,
    required this.selected,
    required this.selectedProvider,
  });

  final List<ModelSelection> choices;
  final String description;
  final Key dropdownKey;
  final IconData icon;
  final String label;
  final ValueChanged<ModelSelection> onChanged;
  final ValueChanged<ProviderModelCatalog> onProviderChanged;
  final List<ProviderModelCatalog> providerChoices;
  final Key providerDropdownKey;
  final ModelSelection selected;
  final ProviderModelCatalog selectedProvider;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final hasAvailableChoice = choices.any((choice) => choice.available);
    final hasAvailableProvider = providerChoices.any(
      (provider) => provider.available,
    );
    return Card(
      margin: EdgeInsets.zero,
      color: colors.surfaceContainer,
      elevation: 0,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(icon, size: 22),
                const SizedBox(width: 10),
                Text(
                  label,
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              description,
              style: Theme.of(
                context,
              ).textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
            ),
            const SizedBox(height: 14),
            KeyedSubtree(
              key: providerDropdownKey,
              child: DropdownButtonFormField<ProviderModelCatalog>(
                key: ValueKey<String>(selectedProvider.provider),
                initialValue: selectedProvider,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: 'Provider',
                  border: const OutlineInputBorder(),
                  helperText: _providerHelperText(selectedProvider),
                ),
                items: providerChoices.map((provider) {
                  return DropdownMenuItem<ProviderModelCatalog>(
                    enabled: provider.available,
                    value: provider,
                    child: Text(
                      provider.label,
                      overflow: TextOverflow.ellipsis,
                    ),
                  );
                }).toList(),
                onChanged: !hasAvailableProvider
                    ? null
                    : (provider) {
                        if (provider != null) {
                          onProviderChanged(provider);
                        }
                      },
              ),
            ),
            const SizedBox(height: 12),
            KeyedSubtree(
              key: dropdownKey,
              child: DropdownButtonFormField<ModelSelection>(
                key: ValueKey<String>('${selected.provider}-${selected.model}'),
                initialValue: selected,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: 'Default model',
                  border: const OutlineInputBorder(),
                ),
                items: choices.map((choice) {
                  return DropdownMenuItem<ModelSelection>(
                    enabled: choice.available,
                    value: choice,
                    child: Row(
                      children: <Widget>[
                        Expanded(
                          child: Text(
                            choice.modelLabel,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        if (choice.recommended) ...<Widget>[
                          const SizedBox(width: 8),
                          Text(
                            'Recommended',
                            style: Theme.of(context).textTheme.labelSmall
                                ?.copyWith(color: colors.onSurfaceVariant),
                          ),
                        ],
                      ],
                    ),
                  );
                }).toList(),
                onChanged: !selectedProvider.available || !hasAvailableChoice
                    ? null
                    : (selection) {
                        if (selection != null) {
                          onChanged(selection);
                        }
                      },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

String _providerHelperText(ProviderModelCatalog provider) {
  if (!provider.configured) {
    return 'Configure ${provider.label} in the web app first.';
  }
  if (!provider.runtimeSupported) {
    return '${provider.label} is not available on this deployment.';
  }
  return 'Configured and available.';
}

final class _LoadFailure extends StatelessWidget {
  const _LoadFailure({required this.message, required this.onRetry});

  final String message;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const Icon(Icons.error_outline_rounded, size: 38),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            FilledButton.tonal(onPressed: onRetry, child: const Text('Retry')),
          ],
        ),
      ),
    );
  }
}

final class _InlineError extends StatelessWidget {
  const _InlineError({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Material(
      color: colors.errorContainer,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: <Widget>[
            Icon(Icons.error_outline_rounded, color: colors.onErrorContainer),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                message,
                style: TextStyle(color: colors.onErrorContainer),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
