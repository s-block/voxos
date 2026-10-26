import 'package:flutter/material.dart';
import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/widgets/voxos_mark.dart';

enum _AuthMode { signIn, create }

final class AuthScreen extends StatefulWidget {
  const AuthScreen({
    required this.auth,
    required this.onChangeServer,
    required this.serverLabel,
    super.key,
  });

  final AuthSession auth;
  final Future<void> Function() onChangeServer;
  final String serverLabel;

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

final class _AuthScreenState extends State<AuthScreen> {
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  _AuthMode _mode = _AuthMode.signIn;
  String? _message;
  bool _isGoogleWorking = false;
  bool _isWorking = false;
  bool _obscurePassword = true;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_isWorking) {
      return;
    }
    final email = _emailController.text.trim();
    final password = _passwordController.text;
    if (email.isEmpty || password.isEmpty) {
      setState(() => _message = 'Enter your email and password.');
      return;
    }

    setState(() {
      _isWorking = true;
      _message = null;
    });
    try {
      if (_mode == _AuthMode.signIn) {
        await widget.auth.signIn(email: email, password: password);
      } else {
        await widget.auth.createAccount(email: email, password: password);
      }
    } on AuthFailure catch (error) {
      if (mounted) {
        setState(() => _message = error.message);
      }
    } finally {
      if (mounted) {
        setState(() => _isWorking = false);
      }
    }
  }

  Future<void> _resetPassword() async {
    final email = _emailController.text.trim();
    if (email.isEmpty) {
      setState(() => _message = 'Enter your email first.');
      return;
    }
    setState(() {
      _isWorking = true;
      _message = null;
    });
    try {
      await widget.auth.resetPassword(email);
      if (mounted) {
        setState(() => _message = 'Password reset email sent.');
      }
    } on AuthFailure catch (error) {
      if (mounted) {
        setState(() => _message = error.message);
      }
    } finally {
      if (mounted) {
        setState(() => _isWorking = false);
      }
    }
  }

  Future<void> _signInWithGoogle() async {
    if (_isWorking) {
      return;
    }
    setState(() {
      _isGoogleWorking = true;
      _isWorking = true;
      _message = null;
    });
    try {
      await widget.auth.signInWithGoogle();
    } on AuthFailure catch (error) {
      if (mounted) {
        setState(() => _message = error.message);
      }
    } finally {
      if (mounted) {
        setState(() {
          _isGoogleWorking = false;
          _isWorking = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  const Align(
                    alignment: Alignment.centerLeft,
                    child: VoxosWordmark(markSize: 40),
                  ),
                  const SizedBox(height: 40),
                  Text(
                    _mode == _AuthMode.signIn
                        ? 'Welcome back'
                        : 'Create your account',
                    style: Theme.of(context).textTheme.headlineMedium,
                  ),
                  const SizedBox(height: 10),
                  Text(
                    'Sign in with the same Firebase account you use on the web.',
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: colors.onSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 26),
                  SegmentedButton<_AuthMode>(
                    segments: const <ButtonSegment<_AuthMode>>[
                      ButtonSegment<_AuthMode>(
                        value: _AuthMode.signIn,
                        label: Text('Sign in'),
                      ),
                      ButtonSegment<_AuthMode>(
                        value: _AuthMode.create,
                        label: Text('Create'),
                      ),
                    ],
                    selected: <_AuthMode>{_mode},
                    onSelectionChanged: _isWorking
                        ? null
                        : (selection) {
                            setState(() {
                              _mode = selection.single;
                              _message = null;
                            });
                          },
                    showSelectedIcon: false,
                    style: const ButtonStyle(
                      visualDensity: VisualDensity.comfortable,
                    ),
                  ),
                  const SizedBox(height: 20),
                  AutofillGroup(
                    child: Column(
                      children: <Widget>[
                        TextField(
                          controller: _emailController,
                          autofillHints: const <String>[
                            AutofillHints.username,
                            AutofillHints.email,
                          ],
                          enabled: !_isWorking,
                          keyboardType: TextInputType.emailAddress,
                          textCapitalization: TextCapitalization.none,
                          decoration: const InputDecoration(
                            labelText: 'Email',
                            prefixIcon: Icon(Icons.alternate_email_rounded),
                          ),
                        ),
                        const SizedBox(height: 14),
                        TextField(
                          controller: _passwordController,
                          autofillHints: <String>[
                            _mode == _AuthMode.signIn
                                ? AutofillHints.password
                                : AutofillHints.newPassword,
                          ],
                          enabled: !_isWorking,
                          obscureText: _obscurePassword,
                          onSubmitted: (_) => _submit(),
                          decoration: InputDecoration(
                            labelText: 'Password',
                            prefixIcon: const Icon(Icons.lock_outline_rounded),
                            suffixIcon: IconButton(
                              onPressed: () {
                                setState(
                                  () => _obscurePassword = !_obscurePassword,
                                );
                              },
                              tooltip: _obscurePassword
                                  ? 'Show password'
                                  : 'Hide password',
                              icon: Icon(
                                _obscurePassword
                                    ? Icons.visibility_outlined
                                    : Icons.visibility_off_outlined,
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (_message != null) ...<Widget>[
                    const SizedBox(height: 14),
                    Container(
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        color: colors.surfaceContainerHighest,
                        borderRadius: BorderRadius.circular(16),
                      ),
                      child: Text(_message!),
                    ),
                  ],
                  const SizedBox(height: 20),
                  FilledButton(
                    onPressed: _isWorking ? null : _submit,
                    child: _isWorking && !_isGoogleWorking
                        ? const SizedBox.square(
                            dimension: 22,
                            child: CircularProgressIndicator(strokeWidth: 2.5),
                          )
                        : Text(
                            _mode == _AuthMode.signIn
                                ? 'Sign in'
                                : 'Create account',
                          ),
                  ),
                  if (_mode == _AuthMode.signIn)
                    TextButton(
                      onPressed: _isWorking ? null : _resetPassword,
                      child: const Text('Forgot password?'),
                    ),
                  const SizedBox(height: 12),
                  Row(
                    children: <Widget>[
                      const Expanded(child: Divider()),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                        child: Text(
                          'or',
                          style: Theme.of(context).textTheme.bodySmall
                              ?.copyWith(color: colors.onSurfaceVariant),
                        ),
                      ),
                      const Expanded(child: Divider()),
                    ],
                  ),
                  const SizedBox(height: 16),
                  OutlinedButton.icon(
                    key: const ValueKey<String>('google-sign-in-button'),
                    onPressed: _isWorking ? null : _signInWithGoogle,
                    icon: _isGoogleWorking
                        ? const SizedBox.square(
                            dimension: 20,
                            child: CircularProgressIndicator(strokeWidth: 2.4),
                          )
                        : const Text(
                            'G',
                            style: TextStyle(
                              fontSize: 18,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                    label: Text(
                      _isGoogleWorking ? 'Signing in…' : 'Continue with Google',
                    ),
                  ),
                  const SizedBox(height: 28),
                  Row(
                    children: <Widget>[
                      Icon(
                        Icons.dns_outlined,
                        size: 18,
                        color: colors.onSurfaceVariant,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          widget.serverLabel,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.bodySmall
                              ?.copyWith(color: colors.onSurfaceVariant),
                        ),
                      ),
                      TextButton(
                        onPressed: _isWorking ? null : widget.onChangeServer,
                        child: const Text('Change'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
