import 'dart:async';

import 'package:flutter/material.dart';
import 'package:voxos/src/theme/voxos_theme.dart';
import 'package:voxos/src/voice/voice_controller.dart';
import 'package:voxos/src/voice/voice_orb.dart';

/// The full-screen live voice conversation.
///
/// Opening the page connects immediately, so talking to Voxos is a single tap
/// from the composer. Leaving the page always disconnects the room.
final class VoiceCallPage extends StatefulWidget {
  const VoiceCallPage({required this.controller, super.key});

  final VoiceController controller;

  static Route<void> route(VoiceController controller) {
    return PageRouteBuilder<void>(
      transitionDuration: const Duration(milliseconds: 320),
      reverseTransitionDuration: const Duration(milliseconds: 240),
      pageBuilder: (context, animation, _) =>
          VoiceCallPage(controller: controller),
      transitionsBuilder: (context, animation, _, child) {
        final curved = CurvedAnimation(
          parent: animation,
          curve: Curves.easeOutCubic,
          reverseCurve: Curves.easeInCubic,
        );
        return FadeTransition(
          opacity: curved,
          child: SlideTransition(
            position: Tween<Offset>(
              begin: const Offset(0, 0.06),
              end: Offset.zero,
            ).animate(curved),
            child: child,
          ),
        );
      },
    );
  }

  @override
  State<VoiceCallPage> createState() => _VoiceCallPageState();
}

final class _VoiceCallPageState extends State<VoiceCallPage> {
  @override
  void initState() {
    super.initState();
    unawaited(widget.controller.openCall());
  }

  Future<void> _close() async {
    final navigator = Navigator.of(context);
    await widget.controller.endCall();
    if (navigator.mounted) {
      navigator.pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final surfaces = VoxosSurfaces.of(context);

    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final controller = widget.controller;
        final speaking =
            controller.phase == VoicePhase.active &&
            controller.isRemoteSpeaking;
        final tone = _tone(colors, surfaces);

        return PopScope<void>(
          canPop: false,
          onPopInvokedWithResult: (didPop, _) {
            if (!didPop) {
              unawaited(_close());
            }
          },
          child: Scaffold(
            body: DecoratedBox(
              decoration: BoxDecoration(
                gradient: RadialGradient(
                  center: const Alignment(0, -0.45),
                  radius: 1.15,
                  colors: <Color>[
                    Color.lerp(colors.surface, tone.$1, 0.14)!,
                    colors.surface,
                  ],
                ),
              ),
              child: SafeArea(
                child: Column(
                  children: <Widget>[
                    _TopBar(
                      label: _statusLabel(controller),
                      onClose: _close,
                      tone: tone.$1,
                    ),
                    Expanded(
                      child: Center(
                        child: SingleChildScrollView(
                          padding: const EdgeInsets.symmetric(horizontal: 28),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: <Widget>[
                              VoiceOrb(
                                energy: _energy(controller, speaking: speaking),
                                highlight: tone.$1,
                                label: _orbSemantics(controller),
                                shade: tone.$2,
                              ),
                              const SizedBox(height: 30),
                              Text(
                                _headline(controller, speaking: speaking),
                                key: const ValueKey<String>(
                                  'voice-call-headline',
                                ),
                                textAlign: TextAlign.center,
                                style: Theme.of(
                                  context,
                                ).textTheme.headlineSmall,
                              ),
                              if (controller.message case final message?)
                                Padding(
                                  padding: const EdgeInsets.only(top: 12),
                                  child: Text(
                                    message,
                                    textAlign: TextAlign.center,
                                    style: Theme.of(context)
                                        .textTheme
                                        .bodyMedium
                                        ?.copyWith(
                                          color: colors.onSurfaceVariant,
                                        ),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    _CallControls(
                      controller: widget.controller,
                      onClose: _close,
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  (Color, Color) _tone(ColorScheme colors, VoxosSurfaces surfaces) {
    return switch (widget.controller.phase) {
      // Setup being incomplete is information, not an alarm, so it stays calm.
      VoicePhase.unavailable => (
        colors.onSurfaceVariant,
        colors.onSurfaceVariant,
      ),
      VoicePhase.checking ||
      VoicePhase.connecting => (surfaces.orbShade, surfaces.orbShade),
      VoicePhase.ready ||
      VoicePhase.active => (surfaces.orbHighlight, surfaces.orbShade),
    };
  }

  double _energy(VoiceController controller, {required bool speaking}) {
    if (speaking) {
      return 1;
    }
    return switch (controller.phase) {
      VoicePhase.active when controller.isMuted => 0.18,
      VoicePhase.active => 0.5,
      VoicePhase.connecting || VoicePhase.checking => 0.3,
      VoicePhase.ready || VoicePhase.unavailable => 0,
    };
  }

  String _headline(VoiceController controller, {required bool speaking}) {
    return switch (controller.phase) {
      VoicePhase.checking => 'Getting ready',
      VoicePhase.connecting => 'Connecting…',
      VoicePhase.active when speaking => 'Voxos is speaking',
      VoicePhase.active when controller.isMuted => 'Microphone muted',
      VoicePhase.active => 'Listening…',
      VoicePhase.ready => 'Call ended',
      VoicePhase.unavailable => 'Voice needs setup',
    };
  }

  String _statusLabel(VoiceController controller) {
    return switch (controller.phase) {
      VoicePhase.checking => 'Checking',
      VoicePhase.connecting => 'Connecting',
      VoicePhase.active when controller.isMuted => 'Live · muted',
      VoicePhase.active => 'Live',
      VoicePhase.ready => 'Not connected',
      VoicePhase.unavailable => 'Unavailable',
    };
  }

  String _orbSemantics(VoiceController controller) {
    return switch (controller.phase) {
      VoicePhase.active when controller.isRemoteSpeaking => 'Voxos is speaking',
      VoicePhase.active => 'Call connected',
      VoicePhase.connecting || VoicePhase.checking => 'Connecting the call',
      VoicePhase.ready || VoicePhase.unavailable => 'Call not connected',
    };
  }
}

final class _TopBar extends StatelessWidget {
  const _TopBar({
    required this.label,
    required this.onClose,
    required this.tone,
  });

  final String label;
  final Future<void> Function() onClose;
  final Color tone;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 10, 10, 0),
      child: Row(
        children: <Widget>[
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
            decoration: BoxDecoration(
              color: tone.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(99),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Container(
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: tone,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  label,
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    color: tone,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
          const Spacer(),
          IconButton(
            key: const ValueKey<String>('minimize-voice-call'),
            tooltip: 'End call and return to chat',
            onPressed: onClose,
            icon: const Icon(Icons.keyboard_arrow_down_rounded, size: 30),
          ),
        ],
      ),
    );
  }
}

final class _CallControls extends StatelessWidget {
  const _CallControls({required this.controller, required this.onClose});

  final VoiceController controller;
  final Future<void> Function() onClose;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final connecting =
        controller.phase == VoicePhase.checking ||
        controller.phase == VoicePhase.connecting;
    final active = controller.phase == VoicePhase.active;

    return Padding(
      padding: const EdgeInsets.fromLTRB(28, 12, 28, 26),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (active || connecting)
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: <Widget>[
                _CallButton(
                  buttonKey: const ValueKey<String>('mute-control'),
                  background: controller.isMuted
                      ? colors.onSurface
                      : colors.surfaceContainerHighest,
                  foreground: controller.isMuted
                      ? colors.surface
                      : colors.onSurface,
                  icon: controller.isMuted
                      ? Icons.mic_off_rounded
                      : Icons.mic_rounded,
                  label: controller.isMuted ? 'Unmute' : 'Mute',
                  onPressed: active ? controller.toggleMute : null,
                ),
                const SizedBox(width: 34),
                _CallButton(
                  buttonKey: const ValueKey<String>('end-call-control'),
                  background: colors.error,
                  foreground: colors.onError,
                  icon: Icons.call_end_rounded,
                  label: 'End',
                  onPressed: onClose,
                ),
              ],
            )
          else
            Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                FilledButton.icon(
                  key: const ValueKey<String>('restart-voice-call'),
                  onPressed: controller.openCall,
                  icon: const Icon(Icons.graphic_eq_rounded),
                  label: Text(
                    controller.phase == VoicePhase.unavailable
                        ? 'Check again'
                        : 'Call again',
                  ),
                ),
                const SizedBox(height: 10),
                TextButton(
                  onPressed: onClose,
                  child: const Text('Back to chat'),
                ),
              ],
            ),
          if (active) ...<Widget>[
            const SizedBox(height: 20),
            Text(
              'Screen stays awake · leaving the app ends the call',
              textAlign: TextAlign.center,
              style: Theme.of(
                context,
              ).textTheme.labelSmall?.copyWith(color: colors.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );
  }
}

final class _CallButton extends StatelessWidget {
  const _CallButton({
    required this.background,
    required this.buttonKey,
    required this.foreground,
    required this.icon,
    required this.label,
    required this.onPressed,
  });

  final Color background;
  final Key buttonKey;
  final Color foreground;
  final IconData icon;
  final String label;
  final Future<void> Function()? onPressed;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        IconButton.filled(
          key: buttonKey,
          onPressed: onPressed,
          tooltip: label,
          icon: Icon(icon, size: 26),
          style: IconButton.styleFrom(
            backgroundColor: background,
            foregroundColor: foreground,
            minimumSize: const Size.square(64),
            fixedSize: const Size.square(64),
            padding: EdgeInsets.zero,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          label,
          style: Theme.of(context).textTheme.labelMedium?.copyWith(
            color: Theme.of(context).colorScheme.onSurfaceVariant,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}
