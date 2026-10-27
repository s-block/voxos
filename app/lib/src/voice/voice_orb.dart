import 'dart:math' as math;

import 'package:flutter/material.dart';

/// The animated sphere that represents the state of a voice call.
///
/// [energy] scales how much the orb moves: a calm idle drift near `0`, and a
/// fast, wide bloom near `1` while the agent is speaking.
final class VoiceOrb extends StatefulWidget {
  const VoiceOrb({
    required this.energy,
    required this.highlight,
    required this.label,
    required this.shade,
    this.diameter = 232,
    super.key,
  });

  final double diameter;
  final double energy;
  final Color highlight;
  final String label;
  final Color shade;

  @override
  State<VoiceOrb> createState() => _VoiceOrbState();
}

final class _VoiceOrbState extends State<VoiceOrb>
    with SingleTickerProviderStateMixin {
  late final AnimationController _animation = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 6),
  )..repeat();

  @override
  void dispose() {
    _animation.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: widget.label,
      child: RepaintBoundary(
        child: TweenAnimationBuilder<double>(
          duration: const Duration(milliseconds: 450),
          curve: Curves.easeOut,
          tween: Tween<double>(begin: 0, end: widget.energy.clamp(0, 1)),
          builder: (context, energy, _) {
            return AnimatedBuilder(
              animation: _animation,
              builder: (context, _) {
                return CustomPaint(
                  size: Size.square(widget.diameter),
                  painter: _VoiceOrbPainter(
                    energy: energy,
                    highlight: widget.highlight,
                    shade: widget.shade,
                    time: _animation.value,
                  ),
                );
              },
            );
          },
        ),
      ),
    );
  }
}

final class _VoiceOrbPainter extends CustomPainter {
  const _VoiceOrbPainter({
    required this.energy,
    required this.highlight,
    required this.shade,
    required this.time,
  });

  final double energy;
  final Color highlight;
  final Color shade;
  final double time;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide / 2;
    final speed = 1 + energy * 2.2;
    final phase = time * speed;
    final wobble = 0.010 + energy * 0.048;
    final breath = 1 + math.sin(phase * 2 * math.pi) * (0.012 + energy * 0.05);
    final coreRadius = radius * 0.66 * breath;

    _paintGlow(canvas, center, radius);
    _paintRipples(canvas, center, radius);

    canvas.drawPath(
      _blobPath(center, coreRadius * 1.14, phase + 0.35, wobble * 1.4),
      Paint()
        ..shader =
            RadialGradient(
              colors: <Color>[
                highlight.withValues(alpha: 0.30),
                highlight.withValues(alpha: 0),
              ],
              stops: const <double>[0.55, 1],
            ).createShader(
              Rect.fromCircle(center: center, radius: coreRadius * 1.14),
            )
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 20),
    );

    canvas.drawPath(
      _blobPath(center, coreRadius, phase, wobble),
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.35, -0.45),
          radius: 1.05,
          colors: <Color>[
            Color.lerp(highlight, Colors.white, 0.55)!,
            highlight,
            shade,
          ],
          stops: const <double>[0, 0.45, 1],
        ).createShader(Rect.fromCircle(center: center, radius: coreRadius)),
    );

    canvas.drawCircle(
      center.translate(-coreRadius * 0.3, -coreRadius * 0.36),
      coreRadius * 0.26,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.22 + energy * 0.16)
        ..maskFilter = MaskFilter.blur(BlurStyle.normal, coreRadius * 0.28),
    );
  }

  void _paintGlow(Canvas canvas, Offset center, double radius) {
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..shader = RadialGradient(
          colors: <Color>[
            highlight.withValues(alpha: 0.20 + energy * 0.16),
            highlight.withValues(alpha: 0),
          ],
          stops: const <double>[0.35, 1],
        ).createShader(Rect.fromCircle(center: center, radius: radius)),
    );
  }

  void _paintRipples(Canvas canvas, Offset center, double radius) {
    if (energy <= 0.02) {
      return;
    }
    for (var index = 0; index < 3; index += 1) {
      final progress = (time * (1 + energy) + index / 3) % 1;
      final rippleRadius = radius * (0.66 + progress * 0.34);
      final fade = (1 - progress) * energy * 0.55;
      if (fade <= 0.01) {
        continue;
      }
      canvas.drawCircle(
        center,
        rippleRadius,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.4
          ..color = highlight.withValues(alpha: fade),
      );
    }
  }

  Path _blobPath(Offset center, double radius, double phase, double wobble) {
    const steps = 96;
    final path = Path();
    for (var index = 0; index <= steps; index += 1) {
      final theta = 2 * math.pi * index / steps;
      final distortion =
          math.sin(4 * theta + phase * 2 * math.pi) * 0.55 +
          math.sin(6 * theta - phase * 3.4 * math.pi) * 0.45;
      final pointRadius = radius * (1 + wobble * distortion);
      final point = center.translate(
        math.cos(theta) * pointRadius,
        math.sin(theta) * pointRadius,
      );
      if (index == 0) {
        path.moveTo(point.dx, point.dy);
      } else {
        path.lineTo(point.dx, point.dy);
      }
    }
    return path..close();
  }

  @override
  bool shouldRepaint(_VoiceOrbPainter oldDelegate) {
    return oldDelegate.time != time ||
        oldDelegate.energy != energy ||
        oldDelegate.highlight != highlight ||
        oldDelegate.shade != shade;
  }
}
