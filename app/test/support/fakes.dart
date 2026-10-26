import 'dart:async';
import 'dart:typed_data';

import 'package:voxos/src/auth/auth_session.dart';
import 'package:voxos/src/chat/chat_api.dart';
import 'package:voxos/src/chat/chat_models.dart';
import 'package:voxos/src/location/location_service.dart';
import 'package:voxos/src/media/gallery_service.dart';
import 'package:voxos/src/voice/voice_transport.dart';
import 'package:voxos/src/voice/voice_wake_lock.dart';

final class FakeAuthSession implements AuthSession {
  FakeAuthSession({
    this.googleSignInError,
    this.pendingToken,
    this.token = 'firebase-id-token',
    AuthUser? user,
  }) : _user =
           user ??
           const AuthUser(id: 'firebase-user', email: 'josh@example.com');

  final Completer<String>? pendingToken;
  final Object? googleSignInError;
  final StreamController<AuthUser?> _users =
      StreamController<AuthUser?>.broadcast();
  final String token;
  AuthUser? _user;
  int googleSignInCount = 0;

  @override
  AuthUser? get currentUser => _user;

  @override
  Stream<AuthUser?> authStateChanges() => _users.stream;

  @override
  Future<void> createAccount({
    required String email,
    required String password,
  }) async {
    _user = AuthUser(id: 'created-user', email: email);
    _users.add(_user);
  }

  @override
  Future<String> idToken() async => pendingToken?.future ?? token;

  @override
  Future<void> resetPassword(String email) async {}

  @override
  Future<void> signIn({required String email, required String password}) async {
    _user = AuthUser(id: 'signed-in-user', email: email);
    _users.add(_user);
  }

  @override
  Future<void> signInWithGoogle() async {
    googleSignInCount++;
    final error = googleSignInError;
    if (error != null) {
      throw error;
    }
    _user = const AuthUser(
      id: 'google-user',
      displayName: 'Google User',
      email: 'google@example.com',
    );
    _users.add(_user);
  }

  @override
  Future<void> signOut() async {
    _user = null;
    _users.add(null);
  }

  Future<void> dispose() => _users.close();
}

final class FakeMicrophoneAccess implements MicrophoneAccess {
  FakeMicrophoneAccess({this.granted = true, this.pendingRequest});

  bool granted;
  int requestCount = 0;
  final Completer<bool>? pendingRequest;

  @override
  Future<bool> request() async {
    requestCount++;
    final pending = pendingRequest;
    if (pending != null) {
      return pending.future;
    }
    return granted;
  }
}

final class FakeVoiceWakeLock implements VoiceWakeLock {
  bool enabled = false;

  @override
  Future<void> setEnabled(bool enabled) async {
    this.enabled = enabled;
  }
}

final class FakeGalleryService implements GalleryService {
  FakeGalleryService({this.granted = true});

  final bool granted;
  int saveCount = 0;
  String? savedName;
  Uint8List? savedBytes;

  @override
  Future<bool> saveImage(Uint8List bytes, {required String name}) async {
    if (!granted) {
      return false;
    }
    saveCount += 1;
    savedBytes = bytes;
    savedName = name;
    return true;
  }
}

final class FakeVoiceTransport implements VoiceTransport {
  FakeVoiceTransport({this.publishFailure});

  final StreamController<VoiceTransportEvent> _events =
      StreamController<VoiceTransportEvent>.broadcast();

  /// Thrown when the transport tries to publish the initial microphone.
  final Object? publishFailure;

  int connectCount = 0;
  int disposeCount = 0;
  bool microphoneEnabled = false;
  bool startWithMicrophone = false;
  AgentLocationSnapshot? currentLocation;
  String? participantName;
  String? roomName;
  bool remoteSpeaking = false;

  @override
  Stream<VoiceTransportEvent> get events => _events.stream;

  @override
  bool get isRemoteSpeaking => remoteSpeaking;

  @override
  Future<void> connect({
    AgentLocationSnapshot? currentLocation,
    required String participantName,
    required String roomName,
    required bool startWithMicrophone,
  }) async {
    connectCount++;
    this.currentLocation = currentLocation;
    this.participantName = participantName;
    this.roomName = roomName;
    this.startWithMicrophone = startWithMicrophone;
    if (startWithMicrophone && publishFailure != null) {
      throw publishFailure!;
    }
    microphoneEnabled = startWithMicrophone;
  }

  @override
  Future<void> disconnect() => dispose();

  @override
  Future<void> dispose() async {
    disposeCount++;
  }

  void emitDisconnected() {
    _events.add(const VoiceTransportEvent.disconnected());
  }

  void emitSpeaking(bool speaking) {
    remoteSpeaking = speaking;
    _events.add(const VoiceTransportEvent.stateChanged());
  }

  @override
  Future<void> setMicrophoneEnabled(bool enabled) async {
    microphoneEnabled = enabled;
  }

  Future<void> closeEvents() => _events.close();
}

final class FakeChatApi implements ChatApi {
  FakeChatApi({
    List<ChatSummary>? chats,
    Map<String, ChatDetail>? details,
    this.chatToCreate,
    this.deleteError,
    this.loadError,
    this.uploadError,
    this.failUploadAt,
    this.imageInputEnabled = true,
    this.imageEditingEnabled = false,
    this.attachmentBytes,
    List<ChatStreamEvent>? streamEvents,
    this.streamFactory,
  }) : chats = chats ?? <ChatSummary>[],
       details = details ?? <String, ChatDetail>{},
       streamEvents = streamEvents ?? <ChatStreamEvent>[];

  final ChatSummary? chatToCreate;
  final Object? deleteError;
  final Map<String, ChatDetail> details;
  final Object? loadError;
  final Object? uploadError;
  final int? failUploadAt;
  final bool imageInputEnabled;
  final bool imageEditingEnabled;
  final Uint8List? attachmentBytes;
  final Stream<ChatStreamEvent> Function(String chatId, String text)?
  streamFactory;
  final List<ChatStreamEvent> streamEvents;
  final List<ChatSummary> chats;

  int createCount = 0;
  int deleteCount = 0;
  int streamCount = 0;
  int uploadCount = 0;
  bool disposed = false;
  final List<String> sentMessages = <String>[];
  final List<String?> sentClientTurnIds = <String?>[];
  final List<List<String>> sentAttachmentIds = <List<String>>[];
  final List<AgentLocationSnapshot?> sentLocations = <AgentLocationSnapshot?>[];
  final List<String> uploadedImageNames = <String>[];

  @override
  Future<ChatSummary> createChat() async {
    createCount++;
    final chat = chatToCreate;
    if (chat == null) {
      throw StateError('No fake chat is configured.');
    }
    if (!chats.any((item) => item.id == chat.id)) {
      chats.insert(0, chat);
    }
    return chat;
  }

  @override
  Future<void> deleteChat(String chatId) async {
    deleteCount++;
    final error = deleteError;
    if (error != null) {
      throw error;
    }
    chats.removeWhere((chat) => chat.id == chatId);
    details.remove(chatId);
  }

  @override
  void dispose() {
    disposed = true;
  }

  @override
  Future<ChatDetail> loadChat(String chatId) async {
    final error = loadError;
    if (error != null) {
      throw error;
    }
    final detail = details[chatId];
    if (detail == null) {
      throw StateError('No fake detail exists for $chatId.');
    }
    return detail;
  }

  @override
  Future<ChatList> loadChats() async {
    final error = loadError;
    if (error != null) {
      throw error;
    }
    return ChatList(
      chats: List<ChatSummary>.of(chats),
      imageEditingEnabled: imageEditingEnabled,
      imageInputEnabled: imageInputEnabled,
    );
  }

  @override
  Future<ChatAttachmentSummary> uploadChatAttachment({
    required String chatId,
    required String clientDraftId,
    required PendingChatImage image,
  }) async {
    uploadCount++;
    uploadedImageNames.add(image.name);
    final error = uploadError;
    if (failUploadAt == uploadCount && error != null) {
      throw error;
    }
    return ChatAttachmentSummary(
      byteSize: image.byteSize,
      height: 100,
      id: 'attachment-$uploadCount',
      mimeType: image.mimeType,
      position: null,
      width: 100,
    );
  }

  @override
  Future<void> deleteChatAttachment({
    required String chatId,
    required String attachmentId,
  }) async {}

  @override
  Future<Uint8List> loadChatAttachment({
    required String chatId,
    required String attachmentId,
    required bool thumbnail,
  }) async {
    return attachmentBytes ?? Uint8List.fromList(<int>[1, 2, 3]);
  }

  @override
  Stream<ChatStreamEvent> streamChatMessage({
    List<String> attachmentIds = const <String>[],
    required String chatId,
    String? clientTurnId,
    AgentLocationSnapshot? currentLocation,
    required String text,
  }) {
    streamCount++;
    sentMessages.add(text);
    sentClientTurnIds.add(clientTurnId);
    sentLocations.add(currentLocation);
    sentAttachmentIds.add(List<String>.of(attachmentIds));
    return streamFactory?.call(chatId, text) ??
        Stream<ChatStreamEvent>.fromIterable(streamEvents);
  }
}

ChatSummary fakeChatSummary({
  required String id,
  String model = 'gpt-5.6-sol',
  bool supportsImageInput = true,
  String? title,
}) {
  return ChatSummary(
    agentId: 'agent-1',
    id: id,
    model: model,
    provider: 'openai',
    startedAt: DateTime.utc(2026, 7, 20, 10),
    status: 'started',
    supportsImageInput: supportsImageInput,
    title: title,
    updatedAt: DateTime.utc(2026, 7, 20, 10),
  );
}

ChatMessage fakeChatMessage({
  required String id,
  required String role,
  required String text,
}) {
  return ChatMessage(
    createdAt: DateTime.utc(2026, 7, 20, 10),
    id: id,
    role: role,
    source: 'text',
    text: text,
  );
}

ChatDetail fakeChatDetail(
  ChatSummary chat, {
  List<ChatMessage> messages = const <ChatMessage>[],
}) {
  return ChatDetail(chat: chat, messages: messages);
}
