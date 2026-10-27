import 'package:flutter/material.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/widgets/voxos_mark.dart';

final class SetupScreen extends StatefulWidget {
  const SetupScreen({
    required this.connections,
    required this.onConnect,
    this.error,
    this.initialEndpoint = '',
    super.key,
  });

  final String? error;
  final List<ServerConnection> connections;
  final String initialEndpoint;
  final Future<void> Function(String endpoint) onConnect;

  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

final class _SetupScreenState extends State<SetupScreen> {
  late final TextEditingController _endpointController;
  String? _localError;
  String? _workingEndpoint;
  late bool _showAddServer;
  bool _isWorking = false;

  @override
  void initState() {
    super.initState();
    _endpointController = TextEditingController(text: widget.initialEndpoint);
    _showAddServer = widget.connections.isEmpty;
  }

  @override
  void dispose() {
    _endpointController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_isWorking) {
      return;
    }
    final endpoint = _endpointController.text.trim();
    if (endpoint.isEmpty) {
      setState(() => _localError = 'Enter your Voxos server address.');
      return;
    }

    setState(() {
      _isWorking = true;
      _workingEndpoint = endpoint;
      _localError = null;
    });
    await widget.onConnect(endpoint);
    if (mounted) {
      setState(() {
        _isWorking = false;
        _workingEndpoint = null;
      });
    }
  }

  Future<void> _select(ServerConnection connection) async {
    if (_isWorking) {
      return;
    }
    final endpoint = connection.baseUri.toString();
    setState(() {
      _isWorking = true;
      _workingEndpoint = endpoint;
      _localError = null;
    });
    await widget.onConnect(endpoint);
    if (mounted) {
      setState(() {
        _isWorking = false;
        _workingEndpoint = null;
      });
    }
  }

  void _toggleAddServer() {
    if (!_showAddServer) {
      final endpoint = _endpointController.text.trim();
      final matchesSavedServer = widget.connections.any(
        (connection) => connection.baseUri.toString() == endpoint,
      );
      if (matchesSavedServer) {
        _endpointController.clear();
      }
    }
    setState(() {
      _showAddServer = !_showAddServer;
      _localError = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final error = _localError ?? widget.error;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(28, 30, 28, 28),
                  child: AutofillGroup(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        const VoxosWordmark(markSize: 40),
                        const SizedBox(height: 30),
                        Text(
                          widget.connections.isEmpty
                              ? 'Connect your Voxos'
                              : 'Choose a server',
                          style: Theme.of(context).textTheme.headlineMedium,
                        ),
                        const SizedBox(height: 12),
                        Text(
                          widget.connections.isEmpty
                              ? 'Enter the root address of your current Voxos deployment. '
                                    'The app will securely fetch everything it needs to sign you in.'
                              : 'Select a saved Voxos deployment or add another one.',
                          style: Theme.of(context).textTheme.bodyMedium
                              ?.copyWith(color: colors.onSurfaceVariant),
                        ),
                        if (widget.connections.isNotEmpty) ...<Widget>[
                          const SizedBox(height: 22),
                          ...widget.connections.map(
                            (connection) => _ServerOption(
                              connection: connection,
                              isWorking:
                                  _workingEndpoint ==
                                  connection.baseUri.toString(),
                              onTap: _isWorking
                                  ? null
                                  : () => _select(connection),
                            ),
                          ),
                          const SizedBox(height: 8),
                          OutlinedButton.icon(
                            key: const ValueKey<String>('add-server-button'),
                            onPressed: _isWorking ? null : _toggleAddServer,
                            icon: Icon(
                              _showAddServer
                                  ? Icons.close_rounded
                                  : Icons.add_rounded,
                            ),
                            label: Text(
                              _showAddServer ? 'Cancel' : 'Add server',
                            ),
                          ),
                        ],
                        if (_showAddServer) ...<Widget>[
                          const SizedBox(height: 22),
                          TextField(
                            key: const ValueKey<String>('server-address-field'),
                            controller: _endpointController,
                            autofillHints: const <String>[AutofillHints.url],
                            autocorrect: false,
                            enabled: !_isWorking,
                            keyboardType: TextInputType.url,
                            onSubmitted: (_) => _submit(),
                            textCapitalization: TextCapitalization.none,
                            decoration: const InputDecoration(
                              hintText: 'https://voxos.example.com',
                              labelText: 'Server address',
                              prefixIcon: Icon(Icons.language_rounded),
                            ),
                          ),
                        ],
                        if (error != null) ...<Widget>[
                          const SizedBox(height: 14),
                          _InlineMessage(message: error),
                        ],
                        if (_showAddServer) ...<Widget>[
                          const SizedBox(height: 22),
                          FilledButton.icon(
                            onPressed: _isWorking ? null : _submit,
                            icon: _isWorking
                                ? const SizedBox.square(
                                    dimension: 20,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2.4,
                                    ),
                                  )
                                : const Icon(Icons.arrow_forward_rounded),
                            label: Text(
                              _isWorking ? 'Connecting…' : 'Continue',
                            ),
                          ),
                        ],
                        const SizedBox(height: 20),
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Icon(
                              Icons.shield_outlined,
                              color: colors.onSurfaceVariant,
                              size: 20,
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text(
                                'Only public server and Firebase app settings are stored. '
                                'No provider keys or LiveKit secrets are saved here.',
                                style: Theme.of(context).textTheme.bodySmall
                                    ?.copyWith(
                                      color: colors.onSurfaceVariant,
                                      height: 1.4,
                                    ),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

final class _ServerOption extends StatelessWidget {
  const _ServerOption({
    required this.connection,
    required this.isWorking,
    required this.onTap,
  });

  final ServerConnection connection;
  final bool isWorking;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        key: ValueKey<String>('server-${connection.baseUri}'),
        onTap: onTap,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: Theme.of(context).colorScheme.outlineVariant),
        ),
        leading: const Icon(Icons.dns_outlined),
        title: Text(connection.baseUri.host),
        subtitle: Text(
          connection.baseUri.toString(),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: isWorking
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2.4),
              )
            : const Icon(Icons.chevron_right_rounded),
      ),
    );
  }
}

final class _InlineMessage extends StatelessWidget {
  const _InlineMessage({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: colors.errorContainer,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Text(message, style: TextStyle(color: colors.onErrorContainer)),
    );
  }
}
