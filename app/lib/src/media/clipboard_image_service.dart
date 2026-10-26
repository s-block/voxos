import 'dart:io';

import 'package:flutter/services.dart';

const int _maxClipboardImageBytes = 25 * 1024 * 1024;

final class ClipboardImage {
  const ClipboardImage({required this.bytes, required this.mimeType});

  final Uint8List bytes;
  final String mimeType;

  String get filenameExtension => switch (mimeType) {
    'image/png' => 'png',
    'image/heic' => 'heic',
    'image/heif' => 'heif',
    _ => 'jpg',
  };
}

abstract interface class ClipboardImageService {
  Future<ClipboardImage?> readImage();
}

abstract interface class ClipboardImageStore {
  Future<void> delete(String path);

  Future<String> save(ClipboardImage image, {required String id});
}

final class TemporaryClipboardImageStore implements ClipboardImageStore {
  const TemporaryClipboardImageStore();

  @override
  Future<void> delete(String path) => File(path).delete();

  @override
  Future<String> save(ClipboardImage image, {required String id}) async {
    final file = File(
      '${Directory.systemTemp.path}/voxos-pasted-$id.${image.filenameExtension}',
    );
    await file.writeAsBytes(image.bytes, flush: true);
    return file.path;
  }
}

final class PlatformClipboardImageService implements ClipboardImageService {
  const PlatformClipboardImageService();

  static const MethodChannel _channel = MethodChannel(
    'voxos/clipboard_images',
  );

  @override
  Future<ClipboardImage?> readImage() async {
    final Map<String, Object?>? payload;
    try {
      payload = await _channel.invokeMapMethod<String, Object?>('readImage');
    } on PlatformException catch (error) {
      if (error.code == 'clipboard_image_too_large') {
        throw const ClipboardImageException(
          'The clipboard image is larger than 25 MiB.',
        );
      }
      rethrow;
    }
    if (payload == null) {
      return null;
    }
    final bytes = payload['bytes'];
    final mimeType = payload['mimeType'];
    if (bytes is! Uint8List ||
        bytes.isEmpty ||
        mimeType is! String ||
        !_supportedMimeTypes.contains(mimeType)) {
      throw const ClipboardImageException(
        'The clipboard did not contain a supported image.',
      );
    }
    if (bytes.length > _maxClipboardImageBytes) {
      throw const ClipboardImageException(
        'The clipboard image is larger than 25 MiB.',
      );
    }
    return ClipboardImage(bytes: bytes, mimeType: mimeType);
  }

  static const Set<String> _supportedMimeTypes = <String>{
    'image/heic',
    'image/heif',
    'image/jpeg',
    'image/png',
  };
}

final class ClipboardImageException implements Exception {
  const ClipboardImageException(this.message);

  final String message;

  @override
  String toString() => message;
}
