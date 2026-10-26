import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:voxos/src/api/voxos_api_client.dart';
import 'package:voxos/src/chat/chat_api.dart';
import 'package:voxos/src/config/server_config.dart';
import 'package:voxos/src/location/location_service.dart';

import '../support/config_fixtures.dart';
import '../support/fakes.dart';

void main() {
  test(
    'chat requests are authenticated, typed, and use expected routes',
    () async {
      final auth = FakeAuthSession();
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        if (request.method == 'GET' && request.url.path == '/api/chats') {
          return http.Response(
            jsonEncode(<String, Object?>{
              'chats': <Object?>[_summaryJson()],
            }),
            200,
          );
        }
        if (request.method == 'POST' && request.url.path == '/api/chats') {
          return http.Response(jsonEncode(_summaryJson()), 201);
        }
        if (request.method == 'GET' &&
            request.url.path == '/api/chats/chat-1') {
          return http.Response(
            jsonEncode(<String, Object?>{
              'chat': _summaryJson(),
              'messages': <Object?>[
                <String, Object?>{
                  'id': 'message-1',
                  'role': 'user',
                  'text': 'Hello',
                  'source': 'text',
                  'created_at': '2026-07-20T10:01:00+00:00',
                },
              ],
              'runtime_events': <Object?>[],
              'tool_events': <Object?>[],
              'usage_events': <Object?>[],
            }),
            200,
          );
        }
        if (request.method == 'DELETE' &&
            request.url.path == '/api/chats/chat-1') {
          return http.Response('', 204);
        }
        return http.Response('{}', 404);
      });
      final api = VoxosApiClient(
        auth: auth,
        connection: _connection(),
        client: client,
      );
      addTearDown(() async {
        api.dispose();
        await auth.dispose();
      });

      final chats = await api.loadChats();
      final created = await api.createChat();
      final detail = await api.loadChat('chat-1');
      await api.deleteChat('chat-1');

      expect(chats.chats.single.id, 'chat-1');
      expect(chats.imageInputEnabled, isTrue);
      expect(created.title, 'Support');
      expect(detail.messages.single.text, 'Hello');
      expect(requests.map((request) => request.method), <String>[
        'GET',
        'POST',
        'GET',
        'DELETE',
      ]);
      expect(
        requests.every(
          (request) =>
              request.headers['authorization'] == 'Bearer firebase-id-token',
        ),
        isTrue,
      );
      expect(jsonDecode(requests[1].body), <String, Object?>{});
    },
  );

  test('streams SSE events and surfaces terminal payloads', () async {
    final auth = FakeAuthSession();
    final body = [
      'event: message_delta',
      'data: ${jsonEncode(<String, Object?>{
        'id': 'event-1',
        'kind': 'message_delta',
        'session_id': 'chat-1',
        'run_id': 'run-1',
        'payload': <String, Object?>{'delta': 'Hel'},
        'created_at': '2026-07-20T10:02:00+00:00',
      })}',
      '',
      'event: tool_call_completed',
      'data: ${jsonEncode(<String, Object?>{
        'id': 'event-image',
        'kind': 'tool_call_completed',
        'session_id': 'chat-1',
        'run_id': 'run-1',
        'payload': <String, Object?>{
          'name': 'generate_image',
          'attachments': <Object?>[
            <String, Object?>{'byte_size': 2048, 'height': 1024, 'id': 'generated-1', 'mime_type': 'image/jpeg', 'position': 0, 'width': 1024},
          ],
        },
        'created_at': '2026-07-20T10:02:30+00:00',
      })}',
      '',
      'event: run_failed',
      'data: ${jsonEncode(<String, Object?>{
        'id': 'event-2',
        'kind': 'run_failed',
        'session_id': 'chat-1',
        'run_id': 'run-1',
        'payload': <String, Object?>{'error_message': 'Configure a text provider.'},
        'created_at': '2026-07-20T10:03:00+00:00',
      })}',
      '',
    ].join('\n');
    final client = MockClient((request) async {
      expect(request.url.path, '/api/chats/chat-1/messages/stream');
      expect(request.headers['accept'], 'text/event-stream');
      expect(jsonDecode(request.body), <String, Object?>{
        'attachment_ids': <Object?>[],
        'location': <String, Object?>{
          'latitude': 42.4304,
          'longitude': 19.2594,
          'horizontal_accuracy_m': 25.0,
          'captured_at': '2026-08-30T12:00:00.000Z',
        },
        'text': 'Hello',
      });
      return http.Response(
        body,
        200,
        headers: <String, String>{'content-type': 'text/event-stream'},
      );
    });
    final api = VoxosApiClient(
      auth: auth,
      connection: _connection(),
      client: client,
    );
    addTearDown(() async {
      api.dispose();
      await auth.dispose();
    });

    final events = await api
        .streamChatMessage(
          chatId: 'chat-1',
          currentLocation: AgentLocationSnapshot(
            capturedAt: DateTime.utc(2026, 8, 30, 12),
            horizontalAccuracyM: 25,
            latitude: 42.4304,
            longitude: 19.2594,
          ),
          text: 'Hello',
        )
        .toList();

    expect(events.map((event) => event.kind), <String>[
      'message_delta',
      'tool_call_completed',
      'run_failed',
    ]);
    expect(events.first.delta, 'Hel');
    expect(events[1].toolName, 'generate_image');
    expect(events[1].attachments.single.id, 'generated-1');
    expect(events.last.errorMessage, 'Configure a text provider.');
  });

  test('SSE parser is independent of transport chunk boundaries', () async {
    final encoded = utf8.encode(
      'data: {"kind":"message_delta","payload":{"delta":"A"}}\n\n'
      'data: {"kind":"message_delta","payload":{"delta":"B"}}\n\n',
    );
    final chunks = encoded.map((byte) => <int>[byte]).toList(growable: false);

    final events = await parseChatEventStream(
      Stream<List<int>>.fromIterable(chunks),
    ).toList();

    expect(events.map((event) => event.delta), <String?>['A', 'B']);
  });

  test('uses backend detail and sign-in error messages', () async {
    final auth = FakeAuthSession();
    var response = http.Response(
      jsonEncode(<String, Object?>{'detail': 'Chat was not found'}),
      404,
    );
    final client = MockClient((request) async => response);
    final api = VoxosApiClient(
      auth: auth,
      connection: _connection(),
      client: client,
    );
    addTearDown(() async {
      api.dispose();
      await auth.dispose();
    });

    await expectLater(
      api.loadChat('missing'),
      throwsA(
        isA<ApiException>().having(
          (error) => error.message,
          'message',
          'Chat was not found',
        ),
      ),
    );

    response = http.Response('', 401);
    await expectLater(
      api.loadChats(),
      throwsA(
        isA<ApiException>().having(
          (error) => error.message,
          'message',
          contains('sign-in is no longer valid'),
        ),
      ),
    );
  });

  test(
    'loads and updates typed model defaults without resetting preferences',
    () async {
      final auth = FakeAuthSession();
      final requests = <http.Request>[];
      final client = MockClient((request) async {
        requests.add(request);
        if (request.method == 'GET' &&
            request.url.path == '/api/text/provider-settings') {
          return http.Response(jsonEncode(_textProviderSettingsJson()), 200);
        }
        if (request.method == 'GET' &&
            request.url.path == '/api/voice/provider-settings') {
          return http.Response(jsonEncode(_voiceProviderSettingsJson()), 200);
        }
        if (request.method == 'PUT') {
          return http.Response('{}', 200);
        }
        return http.Response('{}', 404);
      });
      final api = VoxosApiClient(
        auth: auth,
        connection: _connection(),
        client: client,
      );
      addTearDown(() async {
        api.dispose();
        await auth.dispose();
      });

      final settings = await api.loadModelSettings();
      final textChoice = settings
          .textModelChoicesFor('google_gemini')
          .singleWhere((choice) => choice.model == 'gemini-2.5-flash');
      final textProvider = settings.textProvider(textChoice.provider);
      final imageChoice = settings.imageModelChoices.singleWhere(
        (choice) => choice.model == 'imagen-4.0-generate-001',
      );
      final voiceChoice = settings
          .voiceModelChoicesFor('google_gemini_live')
          .singleWhere(
            (choice) => choice.model == 'gemini-3.1-flash-live-preview',
          );
      final voiceProvider = settings.voiceProvider(voiceChoice.provider);

      await api.updateTextModel(
        current: settings.textPreferences,
        provider: textProvider,
        selection: textChoice,
      );
      await api.updateImageModel(imageChoice);
      await api.updateVoiceModel(
        current: settings.voicePreferences,
        provider: voiceProvider,
        selection: voiceChoice,
      );

      final updates = <String, Map<String, Object?>>{
        for (final request in requests.where(
          (request) => request.method == 'PUT',
        ))
          request.url.path: jsonDecode(request.body) as Map<String, Object?>,
      };
      expect(updates['/api/text/settings'], <String, Object?>{
        'provider': 'google_gemini',
        'model': 'gemini-2.5-flash',
        'preferred_language': 'English',
        'agent_instructions': 'Keep text concise.',
        'temperature': null,
        'allowed_subagent_models': <Object?>[
          'gemini-2.5-flash',
          'gemini-2.5-pro',
        ],
        'subagent_model': 'gemini-2.5-flash',
      });
      expect(updates['/api/text/settings/image-generation'], <String, Object?>{
        'provider': 'google_gemini',
        'model': 'imagen-4.0-generate-001',
      });
      expect(updates['/api/voice/settings'], <String, Object?>{
        'provider': 'google_gemini_live',
        'model': 'gemini-3.1-flash-live-preview',
        'voice': 'Puck',
        'preferred_language': 'British English',
        'agent_instructions': 'Keep voice answers brief.',
        'initial_reply_instructions': 'Say hello.',
      });
    },
  );
}

ServerConnection _connection() {
  return ServerConnection(
    baseUri: Uri.parse('https://voxos.example.com'),
    config: ServerConfig.fromJson(clientConfigJson()),
  );
}

Map<String, Object?> _summaryJson() {
  return <String, Object?>{
    'id': 'chat-1',
    'agent_id': 'agent-1',
    'title': 'Support',
    'provider': 'openai',
    'model': 'gpt-5.6-sol',
    'status': 'started',
    'started_at': '2026-07-20T10:00:00+00:00',
    'updated_at': '2026-07-20T10:01:00+00:00',
  };
}

Map<String, Object?> _textProviderSettingsJson() {
  return <String, Object?>{
    'active_provider': 'openai',
    'image_input_enabled': true,
    'active_settings': <String, Object?>{
      'configured': true,
      'provider': 'openai',
      'model': 'gpt-5',
      'preferred_language': 'English',
      'agent_instructions': 'Keep text concise.',
      'temperature': 0.4,
      'allowed_subagent_models': <Object?>['gpt-5', 'gpt-5-mini'],
      'subagent_model': 'gpt-5-mini',
      'image_generation_provider': 'openai',
      'image_generation_model': 'gpt-image-1',
    },
    'providers': <Object?>[
      _providerJson(
        allowedSubagentModels: <String>['gpt-5', 'gpt-5-mini'],
        defaultImageModel: 'gpt-image-1',
        defaultModel: 'gpt-5',
        defaultSubagentModel: 'gpt-5-mini',
        provider: 'openai',
        label: 'OpenAI',
        models: <Map<String, Object?>>[
          _modelJson('gpt-5', 'GPT-5'),
          _modelJson('gpt-5-mini', 'GPT-5 mini'),
        ],
        imageModels: <Map<String, Object?>>[
          _modelJson('gpt-image-1', 'GPT Image 1'),
        ],
      ),
      _providerJson(
        allowedSubagentModels: <String>['gemini-2.5-flash', 'gemini-2.5-pro'],
        defaultImageModel: 'imagen-4.0-generate-001',
        defaultModel: 'gemini-2.5-flash',
        defaultSubagentModel: 'gemini-2.5-flash',
        provider: 'google_gemini',
        label: 'Google',
        models: <Map<String, Object?>>[
          _modelJson('gemini-2.5-flash', 'Gemini 2.5 Flash'),
          _modelJson('gemini-2.5-pro', 'Gemini 2.5 Pro'),
        ],
        imageModels: <Map<String, Object?>>[
          _modelJson('imagen-4.0-generate-001', 'Imagen 4'),
        ],
      ),
    ],
  };
}

Map<String, Object?> _voiceProviderSettingsJson() {
  return <String, Object?>{
    'active_provider': 'openai_realtime',
    'active_settings': <String, Object?>{
      'configured': true,
      'provider': 'openai_realtime',
      'model': 'gpt-realtime',
      'voice': 'marin',
      'preferred_language': 'British English',
      'agent_instructions': 'Keep voice answers brief.',
      'initial_reply_instructions': 'Say hello.',
    },
    'providers': <Object?>[
      _providerJson(
        defaultModel: 'gpt-realtime',
        defaultVoice: 'marin',
        provider: 'openai_realtime',
        label: 'OpenAI',
        models: <Map<String, Object?>>[
          _modelJson('gpt-realtime', 'GPT Realtime'),
          _modelJson('gpt-realtime-mini', 'GPT Realtime mini'),
        ],
      ),
      _providerJson(
        defaultModel: 'gemini-3.1-flash-live-preview',
        defaultVoice: 'Puck',
        provider: 'google_gemini_live',
        label: 'Google',
        models: <Map<String, Object?>>[
          _modelJson('gemini-3.1-flash-live-preview', 'Gemini 3.1 Flash Live'),
        ],
      ),
    ],
  };
}

Map<String, Object?> _providerJson({
  List<String> allowedSubagentModels = const <String>[],
  String? defaultImageModel,
  required String defaultModel,
  String? defaultSubagentModel,
  String? defaultVoice,
  required String label,
  required List<Map<String, Object?>> models,
  required String provider,
  List<Map<String, Object?>> imageModels = const <Map<String, Object?>>[],
}) {
  return <String, Object?>{
    'provider': provider,
    'label': label,
    'configured': true,
    'runtime_supported': true,
    'default_model': defaultModel,
    'default_image_generation_model': defaultImageModel,
    'default_subagent_model': defaultSubagentModel,
    'default_voice': defaultVoice,
    'allowed_subagent_models': allowedSubagentModels,
    'model_options': models,
    'image_generation_model_options': imageModels,
  };
}

Map<String, Object?> _modelJson(String model, String label) {
  return <String, Object?>{
    'model': model,
    'label': label,
    'description': '$label model',
    'recommended': false,
  };
}
