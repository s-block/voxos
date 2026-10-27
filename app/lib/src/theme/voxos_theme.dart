import 'package:flutter/material.dart';

abstract final class VoxosColors {
  static const Color teal = Color(0xFF23C7B7);
  static const Color sky = Color(0xFF6C9BFF);
  static const Color violet = Color(0xFF9B7BFF);

  static const Color lightBackground = Color(0xFFFFFFFF);
  static const Color lightSurface = Color(0xFFF7F7F8);
  static const Color lightBubble = Color(0xFFF1F1F2);
  static const Color lightHairline = Color(0xFFE4E4E7);
  static const Color lightInk = Color(0xFF10100F);
  static const Color lightMutedInk = Color(0xFF6B6B72);

  static const Color darkBackground = Color(0xFF181818);
  static const Color darkSurface = Color(0xFF232323);
  static const Color darkBubble = Color(0xFF2F2F2F);
  static const Color darkHairline = Color(0xFF3A3A3A);
  static const Color darkInk = Color(0xFFECECEC);
  static const Color darkMutedInk = Color(0xFF9E9EA6);
}

/// Surface tokens the conversation UI needs beyond the Material color scheme.
@immutable
final class VoxosSurfaces extends ThemeExtension<VoxosSurfaces> {
  const VoxosSurfaces({
    required this.bubble,
    required this.composer,
    required this.hairline,
    required this.orbHighlight,
    required this.orbShade,
  });

  /// Background of a message the signed-in person sent.
  final Color bubble;

  /// Background of the composer field and its inline controls.
  final Color composer;

  /// Hairline separators and outlines used across the shell.
  final Color hairline;

  /// Bright end of the voice orb gradient.
  final Color orbHighlight;

  /// Deep end of the voice orb gradient.
  final Color orbShade;

  static VoxosSurfaces of(BuildContext context) {
    final theme = Theme.of(context);
    return theme.extension<VoxosSurfaces>() ??
        VoxosSurfaces.fromBrightness(theme.brightness);
  }

  factory VoxosSurfaces.fromBrightness(Brightness brightness) {
    return brightness == Brightness.light
        ? const VoxosSurfaces(
            bubble: VoxosColors.lightBubble,
            composer: VoxosColors.lightSurface,
            hairline: VoxosColors.lightHairline,
            orbHighlight: VoxosColors.teal,
            orbShade: VoxosColors.violet,
          )
        : const VoxosSurfaces(
            bubble: VoxosColors.darkBubble,
            composer: VoxosColors.darkSurface,
            hairline: VoxosColors.darkHairline,
            orbHighlight: VoxosColors.teal,
            orbShade: VoxosColors.violet,
          );
  }

  @override
  VoxosSurfaces copyWith({
    Color? bubble,
    Color? composer,
    Color? hairline,
    Color? orbHighlight,
    Color? orbShade,
  }) {
    return VoxosSurfaces(
      bubble: bubble ?? this.bubble,
      composer: composer ?? this.composer,
      hairline: hairline ?? this.hairline,
      orbHighlight: orbHighlight ?? this.orbHighlight,
      orbShade: orbShade ?? this.orbShade,
    );
  }

  @override
  VoxosSurfaces lerp(ThemeExtension<VoxosSurfaces>? other, double t) {
    if (other is! VoxosSurfaces) {
      return this;
    }
    return VoxosSurfaces(
      bubble: Color.lerp(bubble, other.bubble, t)!,
      composer: Color.lerp(composer, other.composer, t)!,
      hairline: Color.lerp(hairline, other.hairline, t)!,
      orbHighlight: Color.lerp(orbHighlight, other.orbHighlight, t)!,
      orbShade: Color.lerp(orbShade, other.orbShade, t)!,
    );
  }
}

abstract final class VoxosTheme {
  /// Corner radius shared by the composer, bubbles, and sheets.
  static const double bubbleRadius = 22;

  static ThemeData light() {
    final scheme =
        ColorScheme.fromSeed(
          seedColor: VoxosColors.teal,
          brightness: Brightness.light,
        ).copyWith(
          primary: VoxosColors.lightInk,
          onPrimary: VoxosColors.lightBackground,
          secondary: VoxosColors.teal,
          surface: VoxosColors.lightBackground,
          onSurface: VoxosColors.lightInk,
          surfaceContainer: VoxosColors.lightSurface,
          surfaceContainerHighest: VoxosColors.lightBubble,
          onSurfaceVariant: VoxosColors.lightMutedInk,
          outlineVariant: VoxosColors.lightHairline,
          error: const Color(0xFFB3261E),
        );
    return _theme(scheme, VoxosSurfaces.fromBrightness(Brightness.light));
  }

  static ThemeData dark() {
    final scheme =
        ColorScheme.fromSeed(
          seedColor: VoxosColors.teal,
          brightness: Brightness.dark,
        ).copyWith(
          primary: VoxosColors.darkInk,
          onPrimary: VoxosColors.darkBackground,
          secondary: VoxosColors.teal,
          surface: VoxosColors.darkBackground,
          onSurface: VoxosColors.darkInk,
          surfaceContainer: VoxosColors.darkSurface,
          surfaceContainerHighest: VoxosColors.darkBubble,
          onSurfaceVariant: VoxosColors.darkMutedInk,
          outlineVariant: VoxosColors.darkHairline,
          error: const Color(0xFFFF8A8A),
        );
    return _theme(scheme, VoxosSurfaces.fromBrightness(Brightness.dark));
  }

  static ThemeData _theme(ColorScheme scheme, VoxosSurfaces surfaces) {
    final base = ThemeData(
      colorScheme: scheme,
      useMaterial3: true,
      visualDensity: VisualDensity.standard,
    );
    return base.copyWith(
      extensions: <ThemeExtension<Object?>>[surfaces],
      scaffoldBackgroundColor: scheme.surface,
      splashFactory: InkSparkle.splashFactory,
      appBarTheme: AppBarTheme(
        backgroundColor: scheme.surface,
        surfaceTintColor: Colors.transparent,
        foregroundColor: scheme.onSurface,
        centerTitle: true,
        elevation: 0,
        scrolledUnderElevation: 0,
        titleTextStyle: base.textTheme.titleMedium?.copyWith(
          color: scheme.onSurface,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.2,
        ),
      ),
      dividerTheme: DividerThemeData(
        color: surfaces.hairline,
        space: 1,
        thickness: 1,
      ),
      drawerTheme: DrawerThemeData(
        backgroundColor: scheme.surfaceContainer,
        surfaceTintColor: Colors.transparent,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.horizontal(right: Radius.circular(24)),
        ),
        width: 316,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: scheme.surface,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
        ),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: scheme.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(26)),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: scheme.surfaceContainer,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: BorderSide(color: surfaces.hairline),
        ),
      ),
      listTileTheme: ListTileThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        selectedTileColor: scheme.surfaceContainerHighest,
        selectedColor: scheme.onSurface,
        iconColor: scheme.onSurfaceVariant,
        minVerticalPadding: 12,
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: scheme.inverseSurface,
        contentTextStyle: TextStyle(color: scheme.onInverseSurface),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      textTheme: base.textTheme.copyWith(
        displaySmall: base.textTheme.displaySmall?.copyWith(
          fontWeight: FontWeight.w700,
          letterSpacing: -1.1,
        ),
        headlineMedium: base.textTheme.headlineMedium?.copyWith(
          fontWeight: FontWeight.w600,
          letterSpacing: -0.8,
        ),
        headlineSmall: base.textTheme.headlineSmall?.copyWith(
          fontWeight: FontWeight.w600,
          letterSpacing: -0.5,
        ),
        titleLarge: base.textTheme.titleLarge?.copyWith(
          fontWeight: FontWeight.w600,
          letterSpacing: -0.3,
        ),
        bodyLarge: base.textTheme.bodyLarge?.copyWith(height: 1.5),
        bodyMedium: base.textTheme.bodyMedium?.copyWith(height: 1.45),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surfaces.composer,
        hintStyle: TextStyle(color: scheme.onSurfaceVariant),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 16,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: surfaces.hairline),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: scheme.onSurface, width: 1.5),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: scheme.onSurface,
          foregroundColor: scheme.surface,
          minimumSize: const Size.fromHeight(52),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(26),
          ),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: scheme.onSurface,
          minimumSize: const Size.fromHeight(52),
          side: BorderSide(color: surfaces.hairline),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(26),
          ),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(foregroundColor: scheme.onSurface),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: scheme.surfaceContainer,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(color: surfaces.hairline),
        ),
      ),
    );
  }
}
