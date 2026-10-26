import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';
import 'package:voxos/src/api/voxos_api_client.dart';
import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/chat/chat_composer.dart';
import 'package:voxos/src/chat/chat_controller.dart';
import 'package:voxos/src/chat/chat_history_drawer.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/chat/chat_transcript.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/media/clipboard_image_service.dart';
import 'package:voxos/src/media/gallery_service.dart';
import 'package:voxos/src/notifications/push_controller.dart';
import 'package:voxos/src/notifications/push_message.dart';
import 'package:voxos/src/screens/user_settings_screen.dart';
import 'package:voxos/src/settings/model_settings_api.dart';
import 'package:voxos/src/theme/voxos_theme.dart';
import 'package:voxos/src/voice/livekit_voice_transport.dart';
import 'package:voxos/src/voice/voice_call_page.dart';
import 'package:voxos/src/voice/voice_controller.dart';
import 'package:voxos/src/widgets/voxos_mark.dart';

/// The signed-in shell: one conversation surface for typing and for talking.
final class HomeScreen extends StatefulWidget {
  const HomeScreen({
    required this.auth,
    required this.connection,
    required this.onChangeServer,
    required this.onSignOut,
    required this.user,
    this.chatController,
    this.clipboardImageService = const PlatformClipboardImageService(),
    this.clipboardImageStore = const TemporaryClipboardImageStore(),
    this.galleryService = const SystemGalleryService(),
    this.imagePicker,
    this.modelSettingsApiFactory,
    this.locationController,
    this.preferences,
    this.pushController,
    this.voiceController,
    super.key,
  });

  final AuthSession auth;
  final ChatController? chatController;
  final ClipboardImageService clipboardImageService;
  final ClipboardImageStore clipboardImageStore;
  final ServerConnection connection;
  final GalleryService galleryService;
  final ImagePicker? imagePicker;
  final ModelSettingsApi Function()? modelSettingsApiFactory;
  final LocationSharingController? locationController;
  final Future<void> Function() onChangeServer;
  final Future<void> Function() onSignOut;
  final PushController? pushController;
  final SharedPreferences? preferences;
  final AuthUser user;
  final VoiceController? voiceController;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

final class _HomeScreenState extends State<HomeScreen>
    with WidgetsBindingObserver {
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();
  final TextEditingController _composerController = TextEditingController();
  final FocusNode _composerFocus = FocusNode();
  final ScrollController _messageScroll = ScrollController();
  final Set<String> _ownedClipboardImagePaths = <String>{};
  late final ChatController _chat;
  late final ImagePicker _imagePicker;
  late final LocationSharingController _location;
  late final VoiceController _voice;
  late final bool _ownsLocationController;
  StreamSubscription<PushEvent>? _pushSubscription;
  bool _stickToBottom = true;
  String _messageFingerprint = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _messageScroll.addListener(_trackScrollPosition);
    _ownsLocationController = widget.locationController == null;
    _location =
        widget.locationController ??
        LocationSharingController(
          preferences: widget.preferences,
          storageKey: locationSharingStorageKey(
            serverUri: widget.connection.baseUri,
            userId: widget.user.id,
          ),
        );
    _chat =
        widget.chatController ??
        ChatController(
          api: VoxosApiClient(auth: widget.auth, connection: widget.connection),
          locationProvider: _location,
        );
    _voice =
        widget.voiceController ??
        VoiceController(
          api: VoxosApiClient(auth: widget.auth, connection: widget.connection),
          microphoneAccess: PermissionMicrophoneAccess(),
          locationProvider: _location,
          participantName: _participantName,
          transportFactory: () => LiveKitVoiceTransport(
            auth: widget.auth,
            connection: widget.connection,
          ),
        );
    _imagePicker = widget.imagePicker ?? ImagePicker();
    _chat.addListener(_cleanupOrphanedClipboardImages);
    _pushSubscription = widget.pushController?.events.listen(_handlePushEvent);
    unawaited(_initializeChat());
    unawaited(_recoverLostImages());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.hidden ||
        state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      unawaited(_voice.stopForLifecycle());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _messageScroll
      ..removeListener(_trackScrollPosition)
      ..dispose();
    _composerController.dispose();
    _composerFocus.dispose();
    unawaited(_pushSubscription?.cancel());
    _chat.removeListener(_cleanupOrphanedClipboardImages);
    final clipboardPaths = Set<String>.of(_ownedClipboardImagePaths);
    _ownedClipboardImagePaths.clear();
    for (final path in clipboardPaths) {
      unawaited(_deleteClipboardImage(path));
    }
    _chat.dispose();
    _voice.dispose();
    if (_ownsLocationController) {
      _location.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _chat,
      builder: (context, _) {
        _scheduleScrollIfNeeded();
        return Scaffold(
          key: _scaffoldKey,
          drawer: ChatHistoryDrawer(
            chats: _chat.chats,
            email: widget.user.email,
            isDeleting: _chat.isDeleting,
            onChangeServer: () => _leaveFor(widget.onChangeServer),
            onDeleteChat: _confirmDeleteChat,
            onNewChat: _startNewChat,
            onOpenSettings: _openModelSettings,
            pushController: widget.pushController,
            onSelectChat: _chat.selectChat,
            onSignOut: () => _leaveFor(widget.onSignOut),
            selectedChatId: _chat.selectedChat?.id,
          ),
          appBar: _buildAppBar(),
          body: Column(
            children: <Widget>[
              if (_chat.error case final error?)
                _ErrorBanner(
                  message: error,
                  onDismiss: _chat.clearError,
                  onRetry: _chat.retry,
                ),
              Expanded(child: _buildTranscript()),
              ChatComposer(
                controller: _composerController,
                focusNode: _composerFocus,
                imageInputEnabled: _chat.imageInputEnabled,
                isSending: _chat.isSending,
                onAddImages: _showImageSourcePicker,
                onChanged: () => setState(() {}),
                onPasteImage: _pasteImage,
                onRemoveImage: _chat.removePendingImage,
                onReorderImages: _chat.reorderPendingImage,
                onSend: _sendMessage,
                onStartVoice: _openVoiceCall,
                onStop: _chat.stopStreaming,
                pendingImages: _chat.pendingImages,
                supportsImageInput: _chat.supportsComposerImageInput,
              ),
            ],
          ),
        );
      },
    );
  }

  PreferredSizeWidget _buildAppBar() {
    final chat = _chat.selectedChat;
    final colors = Theme.of(context).colorScheme;
    return AppBar(
      leading: IconButton(
        key: const ValueKey<String>('open-chat-history'),
        tooltip: 'Chat history',
        onPressed: () => _scaffoldKey.currentState?.openDrawer(),
        icon: const Icon(Icons.menu_rounded),
      ),
      title: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            chat?.displayTitle ?? 'Voxos',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          if (chat != null)
            Text(
              chat.model,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(
                context,
              ).textTheme.labelSmall?.copyWith(color: colors.onSurfaceVariant),
            ),
        ],
      ),
      actions: <Widget>[
        IconButton(
          key: const ValueKey<String>('new-chat-app-bar-action'),
          tooltip: 'New chat',
          onPressed: _startNewChat,
          icon: const Icon(Icons.edit_square, size: 21),
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  Widget _buildTranscript() {
    if (_chat.isLoading && _chat.messages.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_chat.messages.isEmpty) {
      return _EmptyConversation(
        name: _greetingName,
        onStartVoice: _openVoiceCall,
      );
    }
    return Stack(
      children: <Widget>[
        ChatTranscript(
          loadAttachment: _chat.loadAttachment,
          messages: _chat.messages,
          onOpenAttachment: _openAttachment,
          scrollController: _messageScroll,
        ),
        Positioned(
          bottom: 16,
          left: 0,
          right: 0,
          child: Center(
            child: _ScrollToLatestButton(
              onPressed: _scrollToLatest,
              visible: !_stickToBottom,
            ),
          ),
        ),
      ],
    );
  }

  String get _participantName {
    final displayName = widget.user.displayName?.trim();
    if (displayName != null && displayName.isNotEmpty) {
      return displayName;
    }
    final emailName = widget.user.email?.split('@').first.trim();
    if (emailName != null && emailName.isNotEmpty) {
      return emailName;
    }
    return 'Voxos mobile user';
  }

  String? get _greetingName {
    final displayName = widget.user.displayName?.trim();
    if (displayName != null && displayName.isNotEmpty) {
      return displayName.split(' ').first;
    }
    return null;
  }

  Future<void> _openVoiceCall() async {
    _composerFocus.unfocus();
    final navigator = Navigator.of(context);
    await _chat.prepareToLeave();
    await navigator.push(VoiceCallPage.route(_voice));
    await _voice.shutdown();
  }

  Future<void> _openModelSettings() async {
    _scaffoldKey.currentState?.closeDrawer();
    final api =
        widget.modelSettingsApiFactory?.call() ??
        VoxosApiClient(auth: widget.auth, connection: widget.connection);
    await Navigator.of(
      context,
    ).push(UserSettingsScreen.route(api, locationSharing: _location));
  }

  Future<void> _initializeChat() async {
    await _chat.initialize();
    final pending = widget.pushController?.takePendingOpen();
    if (pending != null) {
      await _handlePushEvent(pending);
    }
  }

  Future<void> _handlePushEvent(PushEvent event) async {
    if (!mounted) {
      return;
    }
    final intent = event.intent;
    if (event.foreground) {
      if (intent?.type == PushIntentType.chatMessage &&
          intent?.chatId == _chat.selectedChat?.id) {
        await _chat.refreshSelectedChat();
        return;
      }
      final messenger = ScaffoldMessenger.of(context);
      messenger.hideCurrentSnackBar();
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            event.envelope.body ??
                event.envelope.title ??
                'A VoxOS task has finished.',
          ),
          action: intent?.chatId == null
              ? null
              : SnackBarAction(
                  label: 'View',
                  onPressed: () => unawaited(_openPushIntent(intent!)),
                ),
        ),
      );
      return;
    }
    if (intent != null) {
      await _openPushIntent(intent);
    }
  }

  Future<void> _openPushIntent(PushIntent intent) async {
    final chatId = intent.chatId;
    if (chatId == null) {
      return;
    }
    await _chat.openChatById(chatId);
  }

  Future<void> _leaveFor(Future<void> Function() action) async {
    await _chat.prepareToLeave();
    await _voice.shutdown();
    if (mounted) {
      await action();
    }
  }

  Future<void> _startNewChat() async {
    await _chat.startNewChat();
    if (!mounted) {
      return;
    }
    _composerController.clear();
    _stickToBottom = true;
    _composerFocus.requestFocus();
    setState(() {});
  }

  Future<void> _sendMessage() async {
    final text = _composerController.text.trim();
    if ((text.isEmpty && _chat.pendingImages.isEmpty) || _chat.isSending) {
      return;
    }
    _stickToBottom = true;
    setState(() {});
    final sent = await _chat.sendMessage(text);
    if (mounted) {
      if (sent) {
        _composerController.clear();
      }
      _composerFocus.requestFocus();
      setState(() {});
    }
  }

  Future<void> _pickImages() async {
    final remaining =
        ChatController.maxDraftImages - _chat.pendingImages.length;
    if (remaining <= 0 || _chat.isSending) {
      return;
    }
    try {
      final files = await _imagePicker.pickMultiImage(limit: remaining);
      await _addPickedImages(files);
    } on PlatformException {
      _showImageMessage('The photo library could not be opened.');
    }
  }

  Future<void> _takePhoto() async {
    if (_chat.pendingImages.length >= ChatController.maxDraftImages ||
        _chat.isSending) {
      return;
    }
    try {
      final file = await _imagePicker.pickImage(source: ImageSource.camera);
      if (file != null) {
        await _addPickedImages(<XFile>[file]);
      }
    } on PlatformException {
      _showImageMessage('The camera could not be opened.');
    }
  }

  Future<void> _showImageSourcePicker() async {
    final selection = await showModalBottomSheet<_ImageSourceSelection>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            ListTile(
              key: const ValueKey<String>('take-chat-photo'),
              leading: const Icon(Icons.photo_camera_outlined),
              onTap: () => Navigator.pop(context, _ImageSourceSelection.camera),
              title: const Text('Take a photo'),
            ),
            ListTile(
              key: const ValueKey<String>('choose-chat-images'),
              leading: const Icon(Icons.photo_library_outlined),
              onTap: () =>
                  Navigator.pop(context, _ImageSourceSelection.photoLibrary),
              title: const Text('Choose from photo library'),
            ),
            ListTile(
              key: const ValueKey<String>('paste-chat-image'),
              leading: const Icon(Icons.content_paste_rounded),
              onTap: () =>
                  Navigator.pop(context, _ImageSourceSelection.clipboard),
              title: const Text('Paste image'),
            ),
          ],
        ),
      ),
    );
    if (!mounted || selection == null) {
      return;
    }
    switch (selection) {
      case _ImageSourceSelection.camera:
        await _takePhoto();
      case _ImageSourceSelection.photoLibrary:
        await _pickImages();
      case _ImageSourceSelection.clipboard:
        await _pasteImage();
    }
  }

  Future<void> _pasteImage() async {
    if (_chat.pendingImages.length >= ChatController.maxDraftImages ||
        _chat.isSending) {
      return;
    }
    try {
      final image = await widget.clipboardImageService.readImage();
      if (image == null) {
        _showImageMessage('Copy an image first, then try again.');
        return;
      }
      final id = const Uuid().v4();
      final extension = image.filenameExtension;
      final path = await widget.clipboardImageStore.save(image, id: id);
      if (!mounted) {
        await widget.clipboardImageStore.delete(path);
        return;
      }
      _ownedClipboardImagePaths.add(path);
      _chat.addPendingImages(<PendingChatImage>[
        PendingChatImage(
          byteSize: image.bytes.length,
          id: id,
          mimeType: image.mimeType,
          name: 'Pasted image.$extension',
          path: path,
        ),
      ]);
      _cleanupOrphanedClipboardImages();
    } on ClipboardImageException catch (error) {
      _showImageMessage(error.message);
    } on PlatformException {
      _showImageMessage('The clipboard image could not be pasted.');
    } on FileSystemException {
      _showImageMessage('The clipboard image could not be prepared.');
    }
  }

  void _cleanupOrphanedClipboardImages() {
    if (_ownedClipboardImagePaths.isEmpty) {
      return;
    }
    final activePaths = _chat.pendingImages.map((image) => image.path).toSet();
    final orphanedPaths = _ownedClipboardImagePaths
        .where((path) => !activePaths.contains(path))
        .toList(growable: false);
    _ownedClipboardImagePaths.removeAll(orphanedPaths);
    for (final path in orphanedPaths) {
      unawaited(_deleteClipboardImage(path));
    }
  }

  Future<void> _deleteClipboardImage(String path) async {
    try {
      await widget.clipboardImageStore.delete(path);
    } on FileSystemException catch (error) {
      debugPrint(
        'Could not delete a temporary clipboard image: '
        '${error.osError?.errorCode ?? 'unknown error'}',
      );
    }
  }

  void _showImageMessage(String message) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  /// Recovers images picked just before Android reclaimed the app.
  ///
  /// Only Android loses picker results this way, and the platform call is
  /// best effort: a failure here must never block the conversation.
  Future<void> _recoverLostImages() async {
    if (defaultTargetPlatform != TargetPlatform.android) {
      return;
    }
    try {
      final response = await _imagePicker.retrieveLostData();
      if (response.isEmpty || response.files == null) {
        return;
      }
      await _addPickedImages(response.files!);
    } on Object catch (error) {
      debugPrint('Could not recover images from the picker: $error');
    }
  }

  Future<void> _addPickedImages(List<XFile> files) async {
    final available =
        ChatController.maxDraftImages - _chat.pendingImages.length;
    final pending = <PendingChatImage>[];
    for (final file in files.take(available)) {
      final byteSize = await file.length();
      pending.add(
        PendingChatImage(
          byteSize: byteSize,
          id: const Uuid().v4(),
          mimeType: _imageMimeType(file),
          name: file.name,
          path: file.path,
        ),
      );
    }
    if (pending.isNotEmpty) {
      _chat.addPendingImages(pending);
    }
  }

  Future<void> _openAttachment(String attachmentId) async {
    try {
      final bytes = await _chat.loadAttachment(attachmentId, thumbnail: false);
      if (!mounted) {
        return;
      }
      await showDialog<void>(
        context: context,
        builder: (context) => Dialog.fullscreen(
          backgroundColor: Colors.black,
          child: SafeArea(
            child: Stack(
              children: <Widget>[
                Center(
                  child: InteractiveViewer(
                    child: Image.memory(
                      bytes,
                      semanticLabel: 'Full attached image',
                    ),
                  ),
                ),
                Positioned(
                  right: 8,
                  top: 8,
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: <Widget>[
                      IconButton.filledTonal(
                        key: const ValueKey<String>('save-chat-image'),
                        tooltip: 'Save image',
                        onPressed: () => _saveImage(bytes, attachmentId),
                        icon: const Icon(Icons.download_rounded),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filledTonal(
                        tooltip: 'Close image',
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.close_rounded),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    } on Object {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('The image could not be loaded.')),
        );
      }
    }
  }

  Future<void> _saveImage(Uint8List bytes, String attachmentId) async {
    try {
      final saved = await widget.galleryService.saveImage(
        bytes,
        name: 'voxos-${attachmentId.replaceAll('-', '')}',
      );
      if (!saved) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Photo access is required to save.')),
          );
        }
        return;
      }
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('Image saved to Photos.')));
      }
    } on Object {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('The image could not be saved. Check photo access.'),
          ),
        );
      }
    }
  }

  Future<bool> _confirmDeleteChat(ChatSummary chat) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('Delete this chat?'),
          content: Text(
            '"${chat.displayTitle}" and its saved transcript will be removed.',
          ),
          actions: <Widget>[
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: Theme.of(dialogContext).colorScheme.error,
                foregroundColor: Theme.of(dialogContext).colorScheme.onError,
                minimumSize: const Size(0, 44),
              ),
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text('Delete'),
            ),
          ],
        );
      },
    );
    if (confirmed != true || !mounted) {
      return false;
    }
    return _chat.deleteChat(chat.id);
  }

  void _trackScrollPosition() {
    if (!_messageScroll.hasClients) {
      return;
    }
    final stickToBottom = _messageScroll.position.extentAfter < 120;
    if (stickToBottom != _stickToBottom && mounted) {
      setState(() => _stickToBottom = stickToBottom);
    }
  }

  void _scrollToLatest() {
    if (!_messageScroll.hasClients) {
      return;
    }
    unawaited(
      _messageScroll.animateTo(
        _messageScroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 260),
        curve: Curves.easeOut,
      ),
    );
  }

  void _scheduleScrollIfNeeded() {
    final messages = _chat.messages;
    final fingerprint = messages.isEmpty
        ? ''
        : '${messages.last.id}:${messages.last.text.length}';
    if (fingerprint == _messageFingerprint) {
      return;
    }
    _messageFingerprint = fingerprint;
    if (!_stickToBottom) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_messageScroll.hasClients || !_stickToBottom) {
        return;
      }
      _messageScroll.animateTo(
        _messageScroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
      );
    });
  }
}

final class _ScrollToLatestButton extends StatelessWidget {
  const _ScrollToLatestButton({required this.onPressed, required this.visible});

  final VoidCallback onPressed;
  final bool visible;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final surfaces = VoxosSurfaces.of(context);
    return IgnorePointer(
      ignoring: !visible,
      child: AnimatedOpacity(
        duration: const Duration(milliseconds: 160),
        opacity: visible ? 1 : 0,
        child: Material(
          color: colors.surface,
          elevation: 6,
          shadowColor: Colors.black.withValues(alpha: 0.28),
          shape: CircleBorder(side: BorderSide(color: surfaces.hairline)),
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: onPressed,
            child: Padding(
              padding: const EdgeInsets.all(9),
              child: Icon(
                Icons.arrow_downward_rounded,
                size: 20,
                color: colors.onSurface,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

final class _EmptyConversation extends StatelessWidget {
  const _EmptyConversation({required this.name, required this.onStartVoice});

  final String? name;
  final VoidCallback onStartVoice;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final surfaces = VoxosSurfaces.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const VoxosMark(size: 56),
              const SizedBox(height: 22),
              Text(
                name == null ? 'How can I help?' : 'How can I help, $name?',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              const SizedBox(height: 10),
              Text(
                'Type a message, or start a live voice conversation.',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: colors.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 24),
              OutlinedButton.icon(
                key: const ValueKey<String>('start-voice-call-empty'),
                onPressed: onStartVoice,
                icon: const Icon(Icons.graphic_eq_rounded, size: 20),
                label: const Text('Start voice chat'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(0, 48),
                  side: BorderSide(color: surfaces.hairline),
                  padding: const EdgeInsets.symmetric(horizontal: 22),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

final class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({
    required this.message,
    required this.onDismiss,
    required this.onRetry,
  });

  final String message;
  final VoidCallback onDismiss;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Material(
      color: colors.errorContainer,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(18, 6, 6, 6),
        child: Row(
          children: <Widget>[
            Icon(
              Icons.error_outline_rounded,
              color: colors.onErrorContainer,
              size: 20,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                message,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(
                  context,
                ).textTheme.bodySmall?.copyWith(color: colors.onErrorContainer),
              ),
            ),
            TextButton(
              onPressed: onRetry,
              style: TextButton.styleFrom(
                foregroundColor: colors.onErrorContainer,
              ),
              child: const Text('Retry'),
            ),
            IconButton(
              tooltip: 'Dismiss error',
              onPressed: onDismiss,
              icon: const Icon(Icons.close_rounded, size: 18),
              color: colors.onErrorContainer,
            ),
          ],
        ),
      ),
    );
  }
}

String _imageMimeType(XFile file) {
  final declared = file.mimeType?.toLowerCase();
  if (declared != null && declared.startsWith('image/')) {
    return declared;
  }
  final name = file.name.toLowerCase();
  if (name.endsWith('.png')) {
    return 'image/png';
  }
  if (name.endsWith('.heic') || name.endsWith('.heif')) {
    return 'image/heic';
  }
  return 'image/jpeg';
}

enum _ImageSourceSelection { camera, photoLibrary, clipboard }
