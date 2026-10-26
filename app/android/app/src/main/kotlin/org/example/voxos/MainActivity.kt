package org.example.voxos

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.util.Log
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.ByteArrayOutputStream
import java.io.InputStream
import kotlin.concurrent.thread

class MainActivity : FlutterActivity() {
    private companion object {
        const val CLIPBOARD_CHANNEL = "voxos/clipboard_images"
        const val MAX_CLIPBOARD_IMAGE_BYTES = 25 * 1024 * 1024
        const val TAG = "VoxosClipboard"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                "agent_updates",
                "Agent updates",
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = "Updates when scheduled and cloud tasks finish"
            }
            getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CLIPBOARD_CHANNEL,
        ).setMethodCallHandler { call, result ->
            if (call.method != "readImage") {
                result.notImplemented()
                return@setMethodCallHandler
            }
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clip = clipboard.primaryClip
            if (clip == null) {
                result.success(null)
                return@setMethodCallHandler
            }
            thread(name = "voxos-clipboard-image") {
                try {
                    val payload = readClipboardImage(clip)
                    runOnUiThread { result.success(payload) }
                } catch (_: ClipboardImageTooLargeException) {
                    runOnUiThread {
                        result.error(
                            "clipboard_image_too_large",
                            "The clipboard image is larger than 25 MiB.",
                            null,
                        )
                    }
                } catch (error: Exception) {
                    Log.e(TAG, "Could not read a clipboard image", error)
                    runOnUiThread {
                        result.error(
                            "clipboard_image_unreadable",
                            "The clipboard image could not be read.",
                            null,
                        )
                    }
                }
            }
        }
    }

    private fun readClipboardImage(clip: ClipData): Map<String, Any>? {
        for (index in 0 until clip.itemCount) {
            val uri = clip.getItemAt(index).uri ?: continue
            val mimeType = contentResolver.getType(uri)
                ?: clip.description.filterMimeTypes("image/*")?.firstOrNull()
                ?: continue
            val normalizedMimeType = when (mimeType.lowercase()) {
                "image/png" -> "image/png"
                "image/jpeg", "image/jpg" -> "image/jpeg"
                "image/heic" -> "image/heic"
                "image/heif" -> "image/heif"
                else -> continue
            }
            val bytes = contentResolver.openInputStream(uri)?.use(::readBounded)
                ?: continue
            return mapOf(
                "bytes" to bytes,
                "mimeType" to normalizedMimeType,
            )
        }
        return null
    }

    private fun readBounded(stream: InputStream): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(64 * 1024)
        var total = 0
        while (true) {
            val count = stream.read(buffer)
            if (count == -1) {
                return output.toByteArray()
            }
            total += count
            if (total > MAX_CLIPBOARD_IMAGE_BYTES) {
                throw ClipboardImageTooLargeException()
            }
            output.write(buffer, 0, count)
        }
    }

    private class ClipboardImageTooLargeException : Exception()
}
