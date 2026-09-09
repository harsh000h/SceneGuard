plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "dev.sceneguard.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "dev.sceneguard.app"
        // 26+: AImageReader + modern Media3 APIs. Deliberately not 24 - we would
        // be carrying compat shims for a feature set no older device uses.
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildFeatures { compose = true }

    // `debug` is what CI publishes as an APK for self-hosting. Play needs `release`
    // + your upload key; do not ship the debug build to users as if it were signed.
    buildTypes {
        debug { applicationIdSuffix = ".debug" }
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation(project(":core"))
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.compose.material3:material3:1.3.0")
    implementation("androidx.documentfile:documentfile:1.0.1")
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-ui:1.4.1")
    implementation("androidx.media3:media3-common:1.4.1")
    testImplementation("junit:junit:4.13.2")
}
