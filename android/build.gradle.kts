// One Kotlin version for the whole build, declared here once. `:core` applies
// kotlin("jvm") and `:app` applies kotlin("android"); if only the android one is
// versioned here, `:core` silently falls back to whatever compiler AGP bundles
// (1.9.x for AGP 8.5.2) and the two modules then disagree about language level.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.jvm") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
}
