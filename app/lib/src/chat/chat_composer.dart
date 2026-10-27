import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:voxos/src/chat/chat_controller.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/theme/voxos_theme.dart';

/// The rounded input bar that owns typing, attachments, and voice entry.
final class ChatComposer extends StatelessWidget {
  const ChatComposer({
    required this.controller,
    required this.focusNode,
    required this.imageInputEnabled,
    required this.isSending,
    required this.onAddImages,
    required this.onChanged,
    required this.onPasteImage,
    required this.onRemoveImage,
    required this.onReorderImages,
    required this.onSend,
    required this.onStartVoice,
    required this.onStop,
    required this.pendingImages,
    required this.supportsImageInput,
    super.key,
  });

  final TextEditingController controller;
  final FocusNode focusNode;
  final bool imageInputEnabled;
  final bool isSending;
  final Future<void> Function() onAddImages;
  final VoidCallback onChanged;
  final Future<void> Function() onPasteImage;
  final Future<void> Function(String imageId) onRemoveImage;
  final void Function(int fromIndex, int toIndex) onReorderImages;
  final Future<void> Function() onSend;
  final VoidCallback onStartVoice;
  final Future<void> Function() onStop;
  final List<PendingChatImage> pendingImages;
  final bool supportsImageInput;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final surfaces = VoxosSurfaces.of(context);
    final canSend =
        (controller.text.trim().isNotEmpty || pendingImages.isNotEmpty) &&
        !isSending;
    final canAttach =
        supportsImageInput &&
        !isSending &&
        pendingImages.length < ChatController.maxDraftImages;

    return SafeArea(
      top: false,
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 6, 12, 10),
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: surfaces.composer,
                borderRadius: BorderRadius.circular(28),
                border: Border.all(color: surfaces.hairline),
              ),
              child: Padding(
                padding: const EdgeInsets.all(6),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    if (pendingImages.isNotEmpty)
                      _PendingImageStrip(
                        isSending: isSending,
                        images: pendingImages,
                        onRemove: onRemoveImage,
                        onReorder: onReorderImages,
                      ),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: <Widget>[
                        if (imageInputEnabled)
                          _ComposerIconButton(
                            buttonKey: const ValueKey<String>(
                              'add-chat-images',
                            ),
                            icon: Icons.add_rounded,
                            onPressed: canAttach ? onAddImages : null,
                            tooltip: supportsImageInput
                                ? 'Add images'
                                : 'The selected model does not support images and no image editor is enabled',
                          ),
                        Expanded(
                          child: Padding(
                            padding: EdgeInsets.only(
                              left: imageInputEnabled ? 2 : 12,
                              right: 6,
                            ),
                            child: TextField(
                              key: const ValueKey<String>('chat-composer'),
                              controller: controller,
                              contextMenuBuilder: (context, editableTextState) {
                                final items = <ContextMenuButtonItem>[
                                  ...editableTextState.contextMenuButtonItems,
                                ];
                                if (canAttach) {
                                  items.add(
                                    ContextMenuButtonItem(
                                      label: 'Paste image',
                                      onPressed: () {
                                        editableTextState.hideToolbar();
                                        unawaited(onPasteImage());
                                      },
                                    ),
                                  );
                                }
                                return AdaptiveTextSelectionToolbar.buttonItems(
                                  anchors: editableTextState.contextMenuAnchors,
                                  buttonItems: items,
                                );
                              },
                              enabled: !isSending,
                              focusNode: focusNode,
                              maxLength: ChatController.maxMessageLength,
                              maxLines: 6,
                              minLines: 1,
                              onChanged: (_) => onChanged(),
                              style: Theme.of(context).textTheme.bodyLarge,
                              textCapitalization: TextCapitalization.sentences,
                              textInputAction: TextInputAction.newline,
                              decoration: InputDecoration(
                                counterText: '',
                                border: InputBorder.none,
                                enabledBorder: InputBorder.none,
                                focusedBorder: InputBorder.none,
                                disabledBorder: InputBorder.none,
                                filled: false,
                                isDense: true,
                                contentPadding: const EdgeInsets.symmetric(
                                  vertical: 12,
                                ),
                                hintText: 'Message Voxos',
                                hintStyle: Theme.of(context).textTheme.bodyLarge
                                    ?.copyWith(color: colors.onSurfaceVariant),
                              ),
                            ),
                          ),
                        ),
                        if (isSending)
                          _ComposerActionButton(
                            buttonKey: const ValueKey<String>(
                              'stop-chat-stream',
                            ),
                            icon: Icons.stop_rounded,
                            onPressed: onStop,
                            tooltip: 'Stop response',
                          )
                        else if (canSend)
                          _ComposerActionButton(
                            buttonKey: const ValueKey<String>(
                              'send-chat-message',
                            ),
                            icon: Icons.arrow_upward_rounded,
                            onPressed: onSend,
                            tooltip: 'Send message',
                          )
                        else
                          _VoiceCallButton(onPressed: onStartVoice),
                      ],
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

final class _ComposerIconButton extends StatelessWidget {
  const _ComposerIconButton({
    required this.buttonKey,
    required this.icon,
    required this.onPressed,
    required this.tooltip,
  });

  final Key buttonKey;
  final IconData icon;
  final Future<void> Function()? onPressed;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return IconButton(
      key: buttonKey,
      onPressed: onPressed,
      tooltip: tooltip,
      icon: Icon(icon, size: 24),
      style: IconButton.styleFrom(
        foregroundColor: colors.onSurface,
        minimumSize: const Size.square(40),
        fixedSize: const Size.square(40),
        padding: EdgeInsets.zero,
      ),
    );
  }
}

final class _ComposerActionButton extends StatelessWidget {
  const _ComposerActionButton({
    required this.buttonKey,
    required this.icon,
    required this.onPressed,
    required this.tooltip,
  });

  final Key buttonKey;
  final IconData icon;
  final Future<void> Function() onPressed;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 1),
      child: IconButton.filled(
        key: buttonKey,
        onPressed: onPressed,
        tooltip: tooltip,
        icon: Icon(icon, size: 22),
        style: IconButton.styleFrom(
          backgroundColor: colors.onSurface,
          foregroundColor: colors.surface,
          minimumSize: const Size.square(40),
          fixedSize: const Size.square(40),
          padding: EdgeInsets.zero,
        ),
      ),
    );
  }
}

final class _VoiceCallButton extends StatelessWidget {
  const _VoiceCallButton({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final surfaces = VoxosSurfaces.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 1),
      child: Semantics(
        button: true,
        label: 'Start a voice conversation',
        child: Tooltip(
          message: 'Start a voice conversation',
          child: InkWell(
            key: const ValueKey<String>('start-voice-call'),
            borderRadius: BorderRadius.circular(20),
            onTap: onPressed,
            child: Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: <Color>[surfaces.orbHighlight, surfaces.orbShade],
                ),
              ),
              child: const Icon(
                Icons.graphic_eq_rounded,
                color: Colors.white,
                size: 22,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

final class _PendingImageStrip extends StatelessWidget {
  const _PendingImageStrip({
    required this.images,
    required this.isSending,
    required this.onRemove,
    required this.onReorder,
  });

  final List<PendingChatImage> images;
  final bool isSending;
  final Future<void> Function(String imageId) onRemove;
  final void Function(int fromIndex, int toIndex) onReorder;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 84,
      child: ReorderableListView.builder(
        buildDefaultDragHandles: !isSending,
        itemCount: images.length,
        onReorderItem: onReorder,
        padding: const EdgeInsets.fromLTRB(6, 6, 6, 8),
        proxyDecorator: (child, _, animation) => FadeTransition(
          opacity: Tween<double>(begin: 1, end: 0.85).animate(animation),
          child: child,
        ),
        scrollDirection: Axis.horizontal,
        itemBuilder: (context, index) {
          final image = images[index];
          return Padding(
            key: ValueKey<String>('pending-image-${image.id}'),
            padding: const EdgeInsets.only(right: 8),
            child: _PendingImagePreview(
              image: image,
              index: index,
              isSending: isSending,
              onRemove: onRemove,
              total: images.length,
            ),
          );
        },
      ),
    );
  }
}

final class _PendingImagePreview extends StatelessWidget {
  const _PendingImagePreview({
    required this.image,
    required this.index,
    required this.isSending,
    required this.onRemove,
    required this.total,
  });

  static const double _size = 70;

  final PendingChatImage image;
  final int index;
  final bool isSending;
  final Future<void> Function(String imageId) onRemove;
  final int total;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final failed = image.error != null;
    return Semantics(
      label: 'Selected image ${index + 1} of $total, ${image.name}',
      child: SizedBox.square(
        dimension: _size,
        child: Stack(
          clipBehavior: Clip.none,
          children: <Widget>[
            ClipRRect(
              borderRadius: BorderRadius.circular(14),
              child: Image.file(
                File(image.path),
                fit: BoxFit.cover,
                height: _size,
                width: _size,
              ),
            ),
            if (image.status == PendingImageStatus.uploading)
              Positioned.fill(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.45),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: const Center(
                    child: SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(
                        color: Colors.white,
                        strokeWidth: 2,
                      ),
                    ),
                  ),
                ),
              ),
            if (failed)
              Positioned.fill(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    border: Border.all(color: colors.error, width: 2),
                    borderRadius: BorderRadius.circular(14),
                  ),
                ),
              ),
            Positioned(
              right: -6,
              top: -6,
              child: IconButton(
                tooltip: 'Remove ${image.name}',
                onPressed: isSending ? null : () => onRemove(image.id),
                icon: const Icon(Icons.close_rounded, size: 14),
                style: IconButton.styleFrom(
                  backgroundColor: colors.inverseSurface,
                  foregroundColor: colors.onInverseSurface,
                  minimumSize: const Size.square(22),
                  fixedSize: const Size.square(22),
                  padding: EdgeInsets.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
