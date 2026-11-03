plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing comes from environment variables (or GitHub secrets). Never commit passwords.
val keystorePath: String? = System.getenv("RELEASE_KEYSTORE_PATH")
val hasReleaseKey = keystorePath != null && file(keystorePath).exists()

android {
    namespace = "ai.subforge.universal"
    compileSdk = 34

    defaultConfig {
        applicationId = "ai.subforge.universal"
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "1.1.0"
        ndk {
            abiFilters.addAll(listOf("arm64-v8a"))
        }
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(keystorePath!!)
                storePassword = System.getenv("RELEASE_STORE_PASSWORD")
                keyAlias = System.getenv("RELEASE_KEY_ALIAS")
                keyPassword = System.getenv("RELEASE_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Without a release key the release APK is signed with the debug key, so it still installs.
            signingConfig = if (hasReleaseKey) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }

    lint {
        // Lint needs extra memory and downloads; it must not block a build on a small machine.
        checkReleaseBuilds = false
        abortOnError = false
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1,LICENSE,LICENSE.txt,NOTICE,NOTICE.txt}"
        }
        jniLibs {
            useLegacyPackaging = false
        }
    }
}

dependencies {
    // The only library the app needs: FFmpeg for audio extraction, subtitle tracks, soft-mux and burn-in.
    // Same versions that already resolved and built successfully in this project.
    implementation("dev.ffmpegkit-maintained:ffmpeg-kit-full:8.1.7")
    implementation("com.arthenica:smart-exception-java:0.2.1")
}
