import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/chat/markdown_message.dart';
import 'package:voxos/src/theme/voxos_theme.dart';

typedef AttachmentLoader =
    Future<Uint8List> Function(String attachmentId, {required bool thumbnail});

/// The scrolling conversation transcript.
final class ChatTranscript extends StatelessWidget {
  const ChatTranscript({
    required this.loadAttachment,
    required this.messages,
    required this.onOpenAttachment,
    required this.scrollController,
    super.key,
  });

  final AttachmentLoader loadAttachment;
  final List<ChatViewMessage> messages;
  final Future<void> Function(String attachmentId) onOpenAttachment;
  final ScrollController scrollController;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 760),
        child: ListView.builder(
          key: const ValueKey<String>('chat-transcript'),
          controller: scrollController,
          keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          itemCount: messages.length,
          itemBuilder: (context, index) {
            return ChatMessageView(
              loadAttachment: loadAttachment,
              message: messages[index],
              onOpenAttachment: onOpenAttachment,
            );
          },
        ),
      ),
    );
  }
}

/// A single transcript entry, styled by the role that produced it.
final class ChatMessageView extends StatelessWidget {
  const ChatMessageView({
    required this.loadAttachment,
    required this.message,
    required this.onOpenAttachment,
    super.key,
  });

  final AttachmentLoader loadAttachment;
  final ChatViewMessage message;
  final Future<void> Function(String attachmentId) onOpenAttachment;

  @override
  Widget build(BuildContext context) {
    final label = switch (message.role) {
      'user' => 'You',
      'assistant' => 'Voxos',
      'tool' => 'Tool',
      'system' => 'System',
      _ => 'Message',
    };
    return Semantics(
      label: '$label: ${message.text}',
      child: Padding(
        padding: const EdgeInsets.only(bottom: 18),
        child: switch (message.role) {
          'user' => _UserMessage(
            loadAttachment: loadAttachment,
            message: message,
            onOpenAttachment: onOpenAttachment,
          ),
          'assistant' => _AssistantMessage(
            loadAttachment: loadAttachment,
            message: message,
            onOpenAttachment: onOpenAttachment,
          ),
          _ => _NoticeMessage(label: label, text: message.text),
        },
      ),
    );
  }
}

final class _UserMessage extends StatelessWidget {
  const _UserMessage({
    required this.loadAttachment,
    required this.message,
    required this.onOpenAttachment,
  });

  final AttachmentLoader loadAttachment;
  final ChatViewMessage message;
  final Future<void> Function(String attachmentId) onOpenAttachment;

  @override
  Widget build(BuildContext context) {
    final surfaces = VoxosSurfaces.of(context);
    final colors = Theme.of(context).colorScheme;
    return Align(
      alignment: Alignment.centerRight,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            if (message.attachments.isNotEmpty)
              Padding(
                padding: EdgeInsets.only(bottom: message.text.isEmpty ? 0 : 8),
                child: Wrap(
                  alignment: WrapAlignment.end,
                  spacing: 8,
                  runSpacing: 8,
                  children: <Widget>[
                    for (
                      var index = 0;
                      index < message.attachments.length;
                      index += 1
                    )
                      ChatAttachmentThumbnail(
                        attachment: message.attachments[index],
                        index: index,
                        loadAttachment: loadAttachment,
                        onOpen: onOpenAttachment,
                      ),
                  ],
                ),
              ),
            if (message.text.isNotEmpty)
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 11,
                ),
                decoration: BoxDecoration(
                  color: surfaces.bubble,
                  borderRadius: BorderRadius.circular(VoxosTheme.bubbleRadius),
                ),
                child: SelectableText(
                  message.text,
                  style: Theme.of(
                    context,
                  ).textTheme.bodyLarge?.copyWith(color: colors.onSurface),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

final class _AssistantMessage extends StatelessWidget {
  const _AssistantMessage({
    required this.loadAttachment,
    required this.message,
    required this.onOpenAttachment,
  });

  final AttachmentLoader loadAttachment;
  final ChatViewMessage message;
  final Future<void> Function(String attachmentId) onOpenAttachment;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        if (message.attachments.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                for (
                  var index = 0;
                  index < message.attachments.length;
                  index += 1
                )
                  ChatAttachmentThumbnail(
                    attachment: message.attachments[index],
                    index: index,
                    loadAttachment: loadAttachment,
                    onOpen: onOpenAttachment,
                  ),
              ],
            ),
          ),
        if (message.text.isNotEmpty)
          MarkdownMessage(
            foreground: colors.onSurface,
            placeCards: message.placeCards,
            showSources: !message.isStreaming,
            text: message.text,
          ),
        if (message.isStreaming &&
            (message.text.isEmpty || message.activity != null))
          _ThinkingIndicator(
            activity: message.activity ?? ChatActivityPresentation.thinking(),
          )
        else if (message.isStreaming && message.text.isNotEmpty)
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: _StreamingCaret(),
          )
        else if (!message.isStreaming && message.text.isNotEmpty)
          _CopyMessageButton(text: message.text),
      ],
    );
  }
}

final class _NoticeMessage extends StatelessWidget {
  const _NoticeMessage({required this.label, required this.text});

  final String label;
  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final surfaces = VoxosSurfaces.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        border: Border.all(color: surfaces.hairline),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.build_outlined, size: 16, color: colors.onSurfaceVariant),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text.isEmpty ? label : text,
              style: Theme.of(
                context,
              ).textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }
}

final class _CopyMessageButton extends StatefulWidget {
  const _CopyMessageButton({required this.text});

  final String text;

  @override
  State<_CopyMessageButton> createState() => _CopyMessageButtonState();
}

final class _CopyMessageButtonState extends State<_CopyMessageButton> {
  bool _copied = false;

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.text));
    if (!mounted) {
      return;
    }
    setState(() => _copied = true);
    await Future<void>.delayed(const Duration(seconds: 2));
    if (mounted) {
      setState(() => _copied = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Align(
      alignment: Alignment.centerLeft,
      child: TextButton.icon(
        onPressed: _copy,
        icon: Icon(
          _copied ? Icons.check_rounded : Icons.copy_rounded,
          size: 15,
        ),
        label: Text(_copied ? 'Copied' : 'Copy'),
        style: TextButton.styleFrom(
          foregroundColor: colors.onSurfaceVariant,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          minimumSize: const Size(0, 32),
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
          textStyle: Theme.of(context).textTheme.labelMedium,
        ),
      ),
    );
  }
}

final class _ThinkingIndicator extends StatelessWidget {
  const _ThinkingIndicator({required this.activity});

  final ChatActivityPresentation activity;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Semantics(
      label: activity.detail == null
          ? activity.label
          : '${activity.label}. ${activity.detail}',
      liveRegion: true,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (activity.icon case final icon?)
            Icon(
              switch (icon) {
                ChatActivityIcon.mcp => Icons.hub_outlined,
                ChatActivityIcon.web => Icons.public_rounded,
              },
              color: colors.primary,
              size: 24,
            )
          else if (activity.logo case final logo?)
            Container(
              width: 28,
              height: 28,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: colors.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                _logoInitials(logo.label),
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: colors.onSurfaceVariant,
                  fontWeight: FontWeight.w700,
                ),
              ),
            )
          else
            SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(
                color: colors.primary,
                strokeWidth: 2,
              ),
            ),
          const SizedBox(width: 10),
          Flexible(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  activity.label,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: colors.onSurface,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (activity.detail case final detail?)
                  Text(
                    detail,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: colors.onSurfaceVariant,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _logoInitials(String label) {
    final words = label.trim().split(RegExp(r'\s+'));
    return words
        .where((word) => word.isNotEmpty)
        .take(2)
        .map((word) => word.substring(0, 1).toUpperCase())
        .join();
  }
}

final class _StreamingCaret extends StatefulWidget {
  const _StreamingCaret();

  @override
  State<_StreamingCaret> createState() => _StreamingCaretState();
}

final class _StreamingCaretState extends State<_StreamingCaret>
    with SingleTickerProviderStateMixin {
  late final AnimationController _animation = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 700),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _animation.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return FadeTransition(
      opacity: _animation,
      child: Container(
        width: 9,
        height: 9,
        decoration: BoxDecoration(
          color: colors.onSurface,
          shape: BoxShape.circle,
        ),
      ),
    );
  }
}

/// A tappable thumbnail for an image already attached to a message.
final class ChatAttachmentThumbnail extends StatelessWidget {
  const ChatAttachmentThumbnail({
    required this.attachment,
    required this.index,
    required this.loadAttachment,
    required this.onOpen,
    super.key,
  });

  static const double _size = 148;

  final ChatViewAttachment attachment;
  final int index;
  final AttachmentLoader loadAttachment;
  final Future<void> Function(String attachmentId) onOpen;

  @override
  Widget build(BuildContext context) {
    final localPath = attachment.localPath;
    final Widget image;
    if (localPath != null) {
      image = Image.file(
        File(localPath),
        fit: BoxFit.cover,
        height: _size,
        semanticLabel: 'Attached image ${index + 1}',
        width: _size,
      );
    } else {
      image = FutureBuilder<Uint8List>(
        future: loadAttachment(attachment.id, thumbnail: true),
        builder: (context, snapshot) {
          if (snapshot.hasData) {
            return Image.memory(
              snapshot.data!,
              fit: BoxFit.cover,
              height: _size,
              semanticLabel: 'Attached image ${index + 1}',
              width: _size,
            );
          }
          return SizedBox.square(
            dimension: _size,
            child: ColoredBox(
              color: VoxosSurfaces.of(context).bubble,
              child: const Center(
                child: SizedBox.square(
                  dimension: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            ),
          );
        },
      );
    }
    return Semantics(
      button: true,
      label: 'Open attached image ${index + 1}',
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: () => onOpen(attachment.id),
        child: ClipRRect(borderRadius: BorderRadius.circular(18), child: image),
      ),
    );
  }
}
