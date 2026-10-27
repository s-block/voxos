import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:markdown/markdown.dart' as markdown;
import 'package:url_launcher/url_launcher.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/chat/place_card.dart';

typedef ExternalLinkLauncher = Future<bool> Function(Uri uri);

Future<bool> launchExternalWebUrl(Uri uri) {
  return launchUrl(uri, mode: LaunchMode.externalApplication);
}

final class MarkdownMessage extends StatefulWidget {
  const MarkdownMessage({
    required this.foreground,
    required this.text,
    this.launchExternalLink = launchExternalWebUrl,
    this.placeCards = const <ChatPlaceCard>[],
    this.showSources = true,
    super.key,
  });

  final Color foreground;
  final ExternalLinkLauncher launchExternalLink;
  final List<ChatPlaceCard> placeCards;
  final bool showSources;
  final String text;

  @override
  State<MarkdownMessage> createState() => _MarkdownMessageState();
}

final class _MarkdownMessageState extends State<MarkdownMessage> {
  late List<_ChatSource> _sources;

  @override
  void initState() {
    super.initState();
    _sources = _extractSources();
  }

  @override
  void didUpdateWidget(covariant MarkdownMessage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.text != widget.text ||
        oldWidget.placeCards != widget.placeCards ||
        oldWidget.showSources != widget.showSources) {
      _sources = _extractSources();
    }
  }

  List<_ChatSource> _extractSources() {
    return widget.showSources
        ? _extractChatSources(
            widget.text,
            excludedUris: widget.placeCards
                .expand((card) => <Uri>[card.placeUri, card.attributionUri])
                .toSet(),
          )
        : const <_ChatSource>[];
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final bodyStyle = (theme.textTheme.bodyLarge ?? const TextStyle()).copyWith(
      color: widget.foreground,
    );
    final bodyFontSize = bodyStyle.fontSize ?? 16;
    final mutedForeground = widget.foreground.withValues(alpha: 0.78);
    final codeBackground = Color.alphaBlend(
      widget.foreground.withValues(alpha: 0.08),
      colors.surfaceContainerHighest,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        MarkdownBody(
          data: widget.text,
          imageBuilder: (_, _, alt) {
            final label = alt?.trim();
            return Text(
              '[${label == null || label.isEmpty ? 'Image' : label}]',
              style: bodyStyle.copyWith(
                color: mutedForeground,
                fontStyle: FontStyle.italic,
              ),
            );
          },
          onTapLink: (_, href, _) {
            final uri = _safeWebUri(href);
            if (uri != null) {
              unawaited(_openLink(context, uri));
            }
          },
          selectable: true,
          styleSheet: MarkdownStyleSheet.fromTheme(theme).copyWith(
            a: bodyStyle.copyWith(
              color: colors.primary,
              decoration: TextDecoration.underline,
              decorationColor: colors.primary,
            ),
            blockSpacing: 12,
            blockquote: bodyStyle.copyWith(color: mutedForeground),
            blockquoteDecoration: BoxDecoration(
              border: Border(
                left: BorderSide(color: colors.outlineVariant, width: 3),
              ),
            ),
            blockquotePadding: const EdgeInsets.only(left: 12),
            code: bodyStyle.copyWith(
              backgroundColor: codeBackground,
              fontFamily: 'monospace',
              fontSize: bodyFontSize * 0.9,
            ),
            codeblockDecoration: BoxDecoration(
              color: codeBackground,
              border: Border.all(color: colors.outlineVariant),
              borderRadius: BorderRadius.circular(8),
            ),
            codeblockPadding: const EdgeInsets.all(12),
            h1: bodyStyle.copyWith(
              fontSize: bodyFontSize + 4,
              fontWeight: FontWeight.w700,
              height: 1.3,
            ),
            h2: bodyStyle.copyWith(
              fontSize: bodyFontSize + 2,
              fontWeight: FontWeight.w700,
              height: 1.3,
            ),
            h3: bodyStyle.copyWith(fontWeight: FontWeight.w700, height: 1.3),
            h4: bodyStyle.copyWith(fontWeight: FontWeight.w700, height: 1.3),
            h5: bodyStyle.copyWith(fontWeight: FontWeight.w700, height: 1.3),
            h6: bodyStyle.copyWith(fontWeight: FontWeight.w700, height: 1.3),
            horizontalRuleDecoration: BoxDecoration(
              border: Border(top: BorderSide(color: colors.outlineVariant)),
            ),
            listBullet: bodyStyle,
            p: bodyStyle,
            tableBody: bodyStyle,
            tableBorder: TableBorder.all(color: colors.outlineVariant),
            tableCellsPadding: const EdgeInsets.symmetric(
              horizontal: 8,
              vertical: 6,
            ),
            tableColumnWidth: const IntrinsicColumnWidth(),
            tableHead: bodyStyle.copyWith(fontWeight: FontWeight.w700),
          ),
        ),
        if (widget.showSources && widget.placeCards.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 14),
            child: PlaceCardList(
              onOpen: (uri) => _openLink(context, uri),
              placeCards: widget.placeCards,
            ),
          ),
        if (_sources.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 14),
            child: _SourceList(onOpen: _openLink, sources: _sources),
          ),
      ],
    );
  }

  Future<void> _openLink(BuildContext context, Uri uri) async {
    if (await widget.launchExternalLink(uri) || !context.mounted) {
      return;
    }
    ScaffoldMessenger.maybeOf(
      context,
    )?.showSnackBar(const SnackBar(content: Text('Could not open that link.')));
  }
}

final class _SourceList extends StatelessWidget {
  const _SourceList({required this.onOpen, required this.sources});

  final Future<void> Function(BuildContext context, Uri uri) onOpen;
  final List<_ChatSource> sources;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final cardWidth = math.min(280.0, constraints.maxWidth);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(
              'SOURCES',
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
                for (final source in sources)
                  SizedBox(
                    width: cardWidth,
                    child: _SourceCard(
                      onOpen: () => unawaited(onOpen(context, source.uri)),
                      source: source,
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

final class _SourceCard extends StatelessWidget {
  const _SourceCard({required this.onOpen, required this.source});

  final VoidCallback onOpen;
  final _ChatSource source;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final borderRadius = BorderRadius.circular(10);
    return Semantics(
      button: true,
      label: '${source.title}, ${source.hostname}, opens in browser',
      link: true,
      child: Material(
        color: colors.surfaceContainerHighest,
        shape: RoundedRectangleBorder(
          borderRadius: borderRadius,
          side: BorderSide(color: colors.outlineVariant),
        ),
        child: InkWell(
          borderRadius: borderRadius,
          key: ValueKey<String>('chat-source-${source.uri}'),
          onTap: onOpen,
          child: ExcludeSemantics(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
              child: Row(
                children: <Widget>[
                  Icon(Icons.language_rounded, color: colors.primary, size: 18),
                  const SizedBox(width: 9),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: <Widget>[
                        Text(
                          source.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelLarge?.copyWith(
                            color: colors.onSurface,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        Text(
                          source.hostname,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: colors.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  Icon(
                    Icons.open_in_new_rounded,
                    color: colors.onSurfaceVariant,
                    size: 15,
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

final class _ChatSource {
  const _ChatSource({
    required this.hostname,
    required this.title,
    required this.uri,
  });

  final String hostname;
  final String title;
  final Uri uri;
}

List<_ChatSource> _extractChatSources(
  String text, {
  Set<Uri> excludedUris = const <Uri>{},
}) {
  final nodes = markdown.Document(
    extensionSet: markdown.ExtensionSet.gitHubFlavored,
  ).parse(text);
  final sources = <_ChatSource>[];
  final seenUris = <String>{};

  void visit(markdown.Node node) {
    if (sources.length >= 3) {
      return;
    }
    if (node case markdown.Element(tag: 'a', :final attributes)) {
      final uri = _safeWebUri(attributes['href']);
      if (uri != null &&
          !excludedUris.contains(uri) &&
          seenUris.add(uri.toString())) {
        final linkText = node.textContent
            .replaceAll(RegExp(r'\s+'), ' ')
            .trim();
        final title = _safeWebUri(linkText) == uri || linkText.isEmpty
            ? uri.host
            : linkText;
        sources.add(_ChatSource(hostname: uri.host, title: title, uri: uri));
      }
    }
    if (node case markdown.Element(:final children?)) {
      for (final child in children) {
        visit(child);
      }
    }
  }

  for (final node in nodes) {
    visit(node);
  }
  return sources;
}

Uri? _safeWebUri(String? value) {
  final uri = value == null ? null : Uri.tryParse(value);
  if (uri == null ||
      !uri.hasAuthority ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      (uri.scheme != 'http' && uri.scheme != 'https')) {
    return null;
  }
  return uri;
}
