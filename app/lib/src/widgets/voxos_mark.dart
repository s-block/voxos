import 'package:flutter/material.dart';
import 'package:voxos/src/theme/voxos_theme.dart';

/// The Voxos logo mark: a soft gradient tile with the waveform glyph.
final class VoxosMark extends StatelessWidget {
  const VoxosMark({this.size = 36, super.key});

  final double size;

  @override
  Widget build(BuildContext context) {
    final surfaces = VoxosSurfaces.of(context);
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: <Color>[surfaces.orbHighlight, surfaces.orbShade],
        ),
        borderRadius: BorderRadius.circular(size * 0.32),
      ),
      child: Icon(
        Icons.graphic_eq_rounded,
        color: Colors.white,
        size: size * 0.56,
      ),
    );
  }
}

/// The logo mark beside the Voxos wordmark.
final class VoxosWordmark extends StatelessWidget {
  const VoxosWordmark({this.markSize = 36, super.key});

  final double markSize;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        VoxosMark(size: markSize),
        const SizedBox(width: 10),
        Text(
          'voxos',
          style: Theme.of(context).textTheme.titleLarge?.copyWith(
            fontWeight: FontWeight.w700,
            letterSpacing: -0.6,
          ),
        ),
      ],
    );
  }
}
