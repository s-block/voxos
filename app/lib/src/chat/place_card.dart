import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:voxos/src/chat/chat_models.dart';

typedef PlaceCardOpener = Future<void> Function(Uri uri);

final class PlaceCardList extends StatelessWidget {
  const PlaceCardList({
    required this.onOpen,
    required this.placeCards,
    super.key,
  });

  final PlaceCardOpener onOpen;
  final List<ChatPlaceCard> placeCards;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final cardWidth = math.min(300.0, constraints.maxWidth);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(
              'PLACES',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.1,
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                for (final placeCard in placeCards)
                  SizedBox(
                    width: cardWidth,
                    child: _PlaceCard(
                      onOpen: () => unawaited(onOpen(placeCard.placeUri)),
                      placeCard: placeCard,
                    ),
                  ),
              ],
            ),
          ],
        );
      },
    );
  }
}

final class _PlaceCard extends StatelessWidget {
  const _PlaceCard({required this.onOpen, required this.placeCard});

  final VoidCallback onOpen;
  final ChatPlaceCard placeCard;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final borderRadius = BorderRadius.circular(12);
    return Semantics(
      label: 'Open ${placeCard.title} in Google Maps',
      link: true,
      child: Material(
        color: colors.surfaceContainerHighest,
        shape: RoundedRectangleBorder(
          borderRadius: borderRadius,
          side: BorderSide(color: colors.outlineVariant),
        ),
        child: InkWell(
          borderRadius: borderRadius,
          key: ValueKey<String>('chat-place-${placeCard.placeId}'),
          onTap: onOpen,
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 56),
            child: ExcludeSemantics(
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 10,
                ),
                child: Row(
                  children: <Widget>[
                    Icon(
                      Icons.location_on_rounded,
                      color: colors.primary,
                      size: 21,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: <Widget>[
                          Text(
                            placeCard.title,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.labelLarge?.copyWith(
                              color: colors.onSurface,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          Text(
                            'Google Maps',
                            maxLines: 1,
                            overflow: TextOverflow.clip,
                            softWrap: false,
                            style: theme.textTheme.labelSmall?.copyWith(
                              color: colors.onSurfaceVariant,
                              fontSize: 12,
                              fontWeight: FontWeight.w400,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    Icon(
                      Icons.open_in_new_rounded,
                      color: colors.onSurfaceVariant,
                      size: 16,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
