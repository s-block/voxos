import 'dart:typed_data';

import 'package:gal/gal.dart';

abstract interface class GalleryService {
  Future<bool> saveImage(Uint8List bytes, {required String name});
}

final class SystemGalleryService implements GalleryService {
  const SystemGalleryService();

  @override
  Future<bool> saveImage(Uint8List bytes, {required String name}) async {
    if (!await Gal.requestAccess()) {
      return false;
    }
    await Gal.putImageBytes(bytes, name: name);
    return true;
  }
}
