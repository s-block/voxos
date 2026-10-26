import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
    id("com.google.gms.google-services") apply false
}

if (file("google-services.json").isFile) {
    apply(plugin = "com.google.gms.google-services")
}

val installation = Properties().apply {
    val source = rootProject.file("installation.properties")
    if (source.isFile) source.inputStream().use { load(it) }
}
fun installationSetting(name: String): String? = providers.gradleProperty(name)
    .orElse(providers.environmentVariable(name))
    .orNull ?: installation.getProperty(name)

val installationApplicationId = installationSetting("VOXOS_ANDROID_APPLICATION_ID") ?: "org.example.voxos"
val signingKeys = listOf(
    "VOXOS_ANDROID_KEYSTORE_PATH", "VOXOS_ANDROID_KEYSTORE_PASSWORD",
    "VOXOS_ANDROID_KEY_ALIAS", "VOXOS_ANDROID_KEY_PASSWORD",
)
val signingValues = signingKeys.associateWith(::installationSetting)
val hasSigning = signingValues.values.any { !it.isNullOrBlank() }
if (hasSigning && signingValues.values.any { it.isNullOrBlank() }) {
    throw GradleException("Set all four VOXOS_ANDROID_KEYSTORE/KEY signing settings in private installation.properties.")
}

android {
    namespace = "org.example.voxos"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        applicationId = installationApplicationId
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (hasSigning) {
            create("installation") {
                storeFile = rootProject.file(signingValues.getValue("VOXOS_ANDROID_KEYSTORE_PATH")!!)
                storePassword = signingValues.getValue("VOXOS_ANDROID_KEYSTORE_PASSWORD")
                keyAlias = signingValues.getValue("VOXOS_ANDROID_KEY_ALIAS")
                keyPassword = signingValues.getValue("VOXOS_ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("installation")
        }
    }
}

flutter {
    source = "../.."
}
