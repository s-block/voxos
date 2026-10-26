import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:voxos/src/media/clipboard_image_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('voxos/clipboard_images');

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('reads a supported clipboard image from the platform', () async {
    final bytes = Uint8List.fromList(<int>[1, 2, 3]);
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          expect(call.method, 'readImage');
          return <String, Object?>{'bytes': bytes, 'mimeType': 'image/png'};
        });

    final image = await const PlatformClipboardImageService().readImage();

    expect(image?.bytes, bytes);
    expect(image?.mimeType, 'image/png');
    expect(image?.filenameExtension, 'png');
  });

  test('rejects an unsupported clipboard payload', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (_) async {
          return <String, Object?>{
            'bytes': Uint8List.fromList(<int>[1, 2, 3]),
            'mimeType': 'image/gif',
          };
        });

    expect(
      const PlatformClipboardImageService().readImage(),
      throwsA(isA<ClipboardImageException>()),
    );
  });

  test('reports a clipboard image that exceeds the upload limit', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (_) async {
          throw PlatformException(code: 'clipboard_image_too_large');
        });

    expect(
      const PlatformClipboardImageService().readImage(),
      throwsA(
        isA<ClipboardImageException>().having(
          (error) => error.message,
          'message',
          'The clipboard image is larger than 25 MiB.',
        ),
      ),
    );
  });
}
