import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/chat/chat_models.dart';

void main() {
  test('parses typed chat detail and stream fields', () {
    final detail = ChatDetail.fromJson(<String, Object?>{
      'chat': _summaryJson(),
      'messages': <Object?>[
        <String, Object?>{
          'id': 'message-1',
          'role': 'assistant',
          'text': 'Hello',
          'source': 'text',
          'created_at': '2026-07-20T10:01:00+00:00',
          'place_cards': <Object?>[
            <String, Object?>{
              'provider': 'google_maps',
              'place_id': 'ChIJ123',
              'title': 'Konoba Stari Grad',
              'place_url':
                  'https://www.google.com/maps/place/?q=place_id:ChIJ123',
              'attribution_url':
                  'https://www.google.com/maps/place/?q=place_id:ChIJ123',
              'directions_url':
                  'https://www.google.com/maps/dir/'
                  '?api=1&destination=ChIJ123',
              'latitude': 42.4304,
              'longitude': 19.2594,
              'position': 0,
            },
          ],
        },
      ],
    });
    final event = ChatStreamEvent.fromJson(<String, Object?>{
      'id': 'event-1',
      'kind': 'message_delta',
      'session_id': 'chat-1',
      'run_id': 'run-1',
      'payload': <String, Object?>{
        'delta': 'Hi',
        'activity': <String, Object?>{
          'label': 'Searching the web',
          'detail': 'Exa',
          'icon': 'web',
          'logo': <String, Object?>{
            'source': 'domain',
            'domain': 'exa.ai',
            'label': 'Exa',
          },
        },
      },
      'created_at': '2026-07-20T10:02:00+00:00',
    });

    expect(detail.chat.id, 'chat-1');
    expect(detail.chat.displayTitle, 'Support');
    expect(detail.messages.single.text, 'Hello');
    expect(detail.messages.single.placeCards.single.placeId, 'ChIJ123');
    expect(
      detail.messages.single.placeCards.single.placeUri,
      Uri.parse('https://www.google.com/maps/place/?q=place_id:ChIJ123'),
    );
    expect(detail.messages.single.placeCards.single.title, 'Konoba Stari Grad');
    expect(event.delta, 'Hi');
    expect(event.activity?.label, 'Searching the web');
    expect(event.activity?.detail, 'Exa');
    expect(event.activity?.icon, ChatActivityIcon.web);
    expect(event.activity?.logo?.domain, 'exa.ai');
    expect(event.createdAt, DateTime.parse('2026-07-20T10:02:00+00:00'));
  });

  test('rejects malformed required fields', () {
    final json = _summaryJson()..['updated_at'] = 'not-a-date';

    expect(() => ChatSummary.fromJson(json), throwsFormatException);
    expect(
      () => ChatStreamEvent.fromJson(<String, Object?>{
        'kind': 'message_delta',
        'payload': 'not-an-object',
      }),
      throwsFormatException,
    );
  });

  test('rejects place cards with untrusted URLs', () {
    final message = <String, Object?>{
      'id': 'message-1',
      'role': 'assistant',
      'text': 'Hello',
      'source': 'text',
      'created_at': '2026-07-20T10:01:00+00:00',
      'place_cards': <Object?>[
        <String, Object?>{
          'provider': 'google_maps',
          'place_id': 'unsafe',
          'title': 'Unsafe place',
          'place_url': 'https://google.com.attacker.example/place',
          'attribution_url': 'https://www.google.com/maps/place/unsafe',
          'position': 0,
        },
      ],
    };

    expect(() => ChatMessage.fromJson(message), throwsFormatException);
  });

  test('rejects Google-hosted URLs that are not safe Maps destinations', () {
    for (final url in <String>[
      'https://accounts.google.com/signin',
      'https://www.google.com/url?q=https://attacker.example',
      'https://www.google.com/maps/place/example?key=secret-value',
      'https://www.google.com/maps/place/example#token=secret-value',
      'https://www.google.com:444/maps/place/example',
      'https://maps.app.goo.gl/',
    ]) {
      final message = <String, Object?>{
        'id': 'message-1',
        'role': 'assistant',
        'text': 'Hello',
        'source': 'text',
        'created_at': '2026-07-20T10:01:00+00:00',
        'place_cards': <Object?>[
          <String, Object?>{
            'provider': 'google_maps',
            'place_id': 'unsafe',
            'title': 'Unsafe place',
            'place_url': url,
            'attribution_url': 'https://www.google.com/maps/place/example',
            'position': 0,
          },
        ],
      };

      expect(
        () => ChatMessage.fromJson(message),
        throwsFormatException,
        reason: url,
      );
    }
  });

  test('parses a durable message id from completion events', () {
    const event = ChatStreamEvent(
      kind: 'message_completed',
      payload: <String, Object?>{'message_id': ' assistant-1 '},
    );

    expect(event.messageId, 'assistant-1');
  });
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
