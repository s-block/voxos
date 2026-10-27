import 'dart:async';

import 'package:flutter/material.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/notifications/push_controller.dart';
import 'package:voxos/src/theme/voxos_theme.dart';
import 'package:voxos/src/widgets/voxos_mark.dart';

/// The sliding sidebar holding chat history, search, and the account menu.
final class ChatHistoryDrawer extends StatefulWidget {
  const ChatHistoryDrawer({
    required this.chats,
    required this.email,
    required this.isDeleting,
    required this.onChangeServer,
    required this.onDeleteChat,
    required this.onNewChat,
    required this.onOpenSettings,
    required this.onSelectChat,
    required this.onSignOut,
    required this.selectedChatId,
    this.pushController,
    super.key,
  });

  final List<ChatSummary> chats;
  final String? email;
  final bool Function(String chatId) isDeleting;
  final Future<void> Function() onChangeServer;
  final Future<bool> Function(ChatSummary chat) onDeleteChat;
  final Future<void> Function() onNewChat;
  final Future<void> Function() onOpenSettings;
  final Future<void> Function(ChatSummary chat) onSelectChat;
  final Future<void> Function() onSignOut;
  final PushController? pushController;
  final String? selectedChatId;

  @override
  State<ChatHistoryDrawer> createState() => _ChatHistoryDrawerState();
}

final class _ChatHistoryDrawerState extends State<ChatHistoryDrawer> {
  final TextEditingController _searchController = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final surfaces = VoxosSurfaces.of(context);
    final matches = _matchingChats();

    return Drawer(
      child: SafeArea(
        child: Column(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(18, 14, 12, 6),
              child: Row(
                children: <Widget>[
                  const VoxosWordmark(markSize: 30),
                  const Spacer(),
                  IconButton(
                    key: const ValueKey<String>('new-chat-action'),
                    tooltip: 'New chat',
                    onPressed: () => _run(widget.onNewChat),
                    icon: const Icon(Icons.edit_square, size: 21),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 6, 14, 10),
              child: TextField(
                key: const ValueKey<String>('chat-search'),
                controller: _searchController,
                onChanged: (value) => setState(() => _query = value),
                textInputAction: TextInputAction.search,
                decoration: InputDecoration(
                  hintText: 'Search chats',
                  isDense: true,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 12,
                  ),
                  fillColor: colors.surface,
                  prefixIcon: const Icon(Icons.search_rounded, size: 20),
                  prefixIconConstraints: const BoxConstraints(
                    minWidth: 40,
                    minHeight: 40,
                  ),
                  suffixIcon: _query.isEmpty
                      ? null
                      : IconButton(
                          tooltip: 'Clear search',
                          onPressed: () {
                            _searchController.clear();
                            setState(() => _query = '');
                          },
                          icon: const Icon(Icons.close_rounded, size: 18),
                        ),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(14),
                    borderSide: BorderSide(color: surfaces.hairline),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(14),
                    borderSide: BorderSide(color: surfaces.hairline),
                  ),
                ),
              ),
            ),
            Expanded(
              child: matches.isEmpty
                  ? _EmptyHistory(hasQuery: _query.trim().isNotEmpty)
                  : ListView(
                      key: const ValueKey<String>('chat-history-list'),
                      padding: const EdgeInsets.fromLTRB(10, 2, 10, 16),
                      children: _historyChildren(matches),
                    ),
            ),
            Divider(color: surfaces.hairline, height: 1),
            _AccountTile(
              email: widget.email,
              onChangeServer: widget.onChangeServer,
              onOpenSettings: widget.onOpenSettings,
              onSignOut: widget.onSignOut,
              pushController: widget.pushController,
            ),
          ],
        ),
      ),
    );
  }

  List<ChatSummary> _matchingChats() {
    final query = _query.trim().toLowerCase();
    if (query.isEmpty) {
      return widget.chats;
    }
    return widget.chats
        .where((chat) => chat.displayTitle.toLowerCase().contains(query))
        .toList();
  }

  List<Widget> _historyChildren(List<ChatSummary> chats) {
    final children = <Widget>[];
    String? currentGroup;
    final now = DateTime.now();
    for (final chat in chats) {
      final group = _groupLabel(chat.updatedAt.toLocal(), now);
      if (group != currentGroup) {
        currentGroup = group;
        children.add(_GroupHeading(label: group));
      }
      children.add(
        _HistoryRow(
          chat: chat,
          isDeleting: widget.isDeleting(chat.id),
          isSelected: widget.selectedChatId == chat.id,
          onDelete: () => widget.onDeleteChat(chat),
          onSelect: () => _run(() => widget.onSelectChat(chat)),
        ),
      );
    }
    return children;
  }

  /// Closes the sidebar first so loading happens on the conversation itself.
  void _run(Future<void> Function() action) {
    Scaffold.of(context).closeDrawer();
    unawaited(action());
  }

  static String _groupLabel(DateTime updatedAt, DateTime now) {
    final today = DateTime(now.year, now.month, now.day);
    final day = DateTime(updatedAt.year, updatedAt.month, updatedAt.day);
    final age = today.difference(day).inDays;
    if (age <= 0) {
      return 'Today';
    }
    if (age == 1) {
      return 'Yesterday';
    }
    if (age <= 7) {
      return 'Previous 7 days';
    }
    if (age <= 30) {
      return 'Previous 30 days';
    }
    return 'Older';
  }
}

final class _GroupHeading extends StatelessWidget {
  const _GroupHeading({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 16, 10, 6),
      child: Text(
        label,
        style: Theme.of(context).textTheme.labelMedium?.copyWith(
          color: Theme.of(context).colorScheme.onSurfaceVariant,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

final class _HistoryRow extends StatelessWidget {
  const _HistoryRow({
    required this.chat,
    required this.isDeleting,
    required this.isSelected,
    required this.onDelete,
    required this.onSelect,
  });

  final ChatSummary chat;
  final bool isDeleting;
  final bool isSelected;
  final Future<bool> Function() onDelete;
  final VoidCallback onSelect;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Dismissible(
      key: ValueKey<String>('history-${chat.id}'),
      direction: DismissDirection.endToStart,
      confirmDismiss: (_) => onDelete(),
      background: Container(
        alignment: Alignment.centerRight,
        margin: const EdgeInsets.symmetric(vertical: 2),
        padding: const EdgeInsets.symmetric(horizontal: 20),
        decoration: BoxDecoration(
          color: colors.errorContainer,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Icon(
          Icons.delete_outline_rounded,
          color: colors.onErrorContainer,
        ),
      ),
      child: ListTile(
        dense: true,
        selected: isSelected,
        onTap: isDeleting ? null : onSelect,
        contentPadding: const EdgeInsets.only(left: 14, right: 4),
        minVerticalPadding: 10,
        title: Text(
          chat.displayTitle,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
            fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
        trailing: isDeleting
            ? const SizedBox.square(
                dimension: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : PopupMenuButton<void>(
                tooltip: 'Delete ${chat.displayTitle}',
                icon: Icon(
                  Icons.more_horiz_rounded,
                  size: 19,
                  color: colors.onSurfaceVariant,
                ),
                padding: EdgeInsets.zero,
                itemBuilder: (context) => <PopupMenuEntry<void>>[
                  PopupMenuItem<void>(
                    onTap: onDelete,
                    child: ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(
                        Icons.delete_outline_rounded,
                        color: colors.error,
                      ),
                      title: Text(
                        'Delete chat',
                        style: TextStyle(color: colors.error),
                      ),
                    ),
                  ),
                ],
              ),
      ),
    );
  }
}

final class _EmptyHistory extends StatelessWidget {
  const _EmptyHistory({required this.hasQuery});

  final bool hasQuery;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Text(
          hasQuery ? 'No chats match that search.' : 'No saved chats yet.',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
            color: Theme.of(context).colorScheme.onSurfaceVariant,
          ),
        ),
      ),
    );
  }
}

final class _AccountTile extends StatelessWidget {
  const _AccountTile({
    required this.email,
    required this.onChangeServer,
    required this.onOpenSettings,
    required this.onSignOut,
    required this.pushController,
  });

  final String? email;
  final Future<void> Function() onChangeServer;
  final Future<void> Function() onOpenSettings;
  final Future<void> Function() onSignOut;
  final PushController? pushController;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return ListTile(
      key: const ValueKey<String>('account-tile'),
      onTap: () => _showAccountSheet(context),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      leading: CircleAvatar(
        backgroundColor: colors.surfaceContainerHighest,
        foregroundColor: colors.onSurface,
        radius: 17,
        child: Text(
          _initial,
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
      ),
      title: Text(
        email ?? 'Signed in',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: Theme.of(context).textTheme.bodyMedium,
      ),
      trailing: const Icon(Icons.more_horiz_rounded, size: 20),
    );
  }

  String get _initial {
    final source = email?.trim();
    if (source == null || source.isEmpty) {
      return '?';
    }
    return source.characters.first.toUpperCase();
  }

  Future<void> _showAccountSheet(BuildContext context) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) {
        Widget buildSheet() => SafeArea(
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                if (email != null)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(24, 4, 24, 12),
                    child: Text(
                      email!,
                      style: Theme.of(sheetContext).textTheme.titleMedium,
                    ),
                  ),
                _NotificationPreference(controller: pushController),
                ListTile(
                  key: const ValueKey<String>('open-model-settings'),
                  leading: const Icon(Icons.tune_rounded),
                  title: const Text('Settings'),
                  onTap: () {
                    Navigator.of(sheetContext).pop();
                    unawaited(onOpenSettings());
                  },
                ),
                ListTile(
                  leading: const Icon(Icons.dns_outlined),
                  title: const Text('Change server'),
                  onTap: () {
                    Navigator.of(sheetContext).pop();
                    onChangeServer();
                  },
                ),
                ListTile(
                  leading: const Icon(Icons.logout_rounded),
                  title: const Text('Sign out'),
                  onTap: () {
                    Navigator.of(sheetContext).pop();
                    onSignOut();
                  },
                ),
                const SizedBox(height: 12),
              ],
            ),
          ),
        );
        final controller = pushController;
        if (controller == null) {
          return buildSheet();
        }
        return AnimatedBuilder(
          animation: controller,
          builder: (_, _) => buildSheet(),
        );
      },
    );
  }
}

final class _NotificationPreference extends StatelessWidget {
  const _NotificationPreference({required this.controller});

  final PushController? controller;

  @override
  Widget build(BuildContext context) {
    final push = controller;
    final colors = Theme.of(context).colorScheme;
    final available = push?.isAvailable ?? false;
    final denied = push?.isDenied ?? false;
    final error = push?.error;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        SwitchListTile(
          key: const ValueKey<String>('push-notifications-toggle'),
          secondary: const Icon(Icons.notifications_outlined),
          title: const Text('Notifications'),
          subtitle: Text(
            error ??
                (denied
                    ? 'Allow notifications in your device settings.'
                    : available
                    ? 'Get alerts for new messages and tasks that need attention.'
                    : 'Not available for this server.'),
            style: error == null ? null : TextStyle(color: colors.error),
          ),
          value: push?.isEnabled ?? false,
          onChanged: !available || (push?.isBusy ?? false)
              ? null
              : (enabled) {
                  unawaited(enabled ? push!.enable() : push!.disable());
                },
        ),
        if (denied)
          ListTile(
            key: const ValueKey<String>('open-notification-settings'),
            leading: const Icon(Icons.settings_outlined),
            title: const Text('Open notification settings'),
            onTap: () => unawaited(push!.openSettings()),
          ),
      ],
    );
  }
}
