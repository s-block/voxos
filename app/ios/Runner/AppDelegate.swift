import Flutter
import UIKit
import UniformTypeIdentifiers
import firebase_messaging

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private static let clipboardChannelName = "voxos/clipboard_images"
  private static let maxClipboardImageBytes = 25 * 1024 * 1024
  private var clipboardChannel: FlutterMethodChannel?

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    FLTFirebaseMessagingPlugin.configureNotificationCenterDelegate()
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let channel = FlutterMethodChannel(
      name: Self.clipboardChannelName,
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    channel.setMethodCallHandler { [weak self] call, result in
      guard call.method == "readImage" else {
        result(FlutterMethodNotImplemented)
        return
      }
      guard let self else {
        result(
          FlutterError(
            code: "clipboard_unavailable",
            message: "The clipboard is unavailable.",
            details: nil
          )
        )
        return
      }
      self.readClipboardImage(result: result)
    }
    clipboardChannel = channel
  }

  private func readClipboardImage(result: @escaping FlutterResult) {
    let pasteboard = UIPasteboard.general
    let candidates: [(String, String)] = [
      (UTType.png.identifier, "image/png"),
      (UTType.jpeg.identifier, "image/jpeg"),
      (UTType.heic.identifier, "image/heic"),
    ]
    var foundOversizedImage = false
    for (pasteboardType, mimeType) in candidates {
      guard let data = pasteboard.data(forPasteboardType: pasteboardType) else {
        continue
      }
      guard data.count <= Self.maxClipboardImageBytes else {
        foundOversizedImage = true
        continue
      }
      completeClipboardRead(data: data, mimeType: mimeType, result: result)
      return
    }
    if foundOversizedImage {
      result(
        FlutterError(
          code: "clipboard_image_too_large",
          message: "The clipboard image is larger than 25 MiB.",
          details: nil
        )
      )
      return
    }
    guard let image = pasteboard.image else {
      result(nil)
      return
    }
    DispatchQueue.global(qos: .userInitiated).async {
      guard let data = image.pngData() else {
        DispatchQueue.main.async {
          result(
            FlutterError(
              code: "clipboard_image_unreadable",
              message: "The clipboard image could not be read.",
              details: nil
            )
          )
        }
        return
      }
      DispatchQueue.main.async {
        self.completeClipboardRead(
          data: data,
          mimeType: "image/png",
          result: result
        )
      }
    }
  }

  private func completeClipboardRead(
    data: Data,
    mimeType: String,
    result: @escaping FlutterResult
  ) {
    guard data.count <= Self.maxClipboardImageBytes else {
      result(
        FlutterError(
          code: "clipboard_image_too_large",
          message: "The clipboard image is larger than 25 MiB.",
          details: nil
        )
      )
      return
    }
    result([
      "bytes": FlutterStandardTypedData(bytes: data),
      "mimeType": mimeType,
    ])
  }
}
