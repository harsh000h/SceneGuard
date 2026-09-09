// Two modules on purpose: `core` is pure Kotlin so it unit-tests on a bare JVM
// (no emulator, no Robolectric), and `app` is a thin Android shell around it.
// If a contributor needs to change skip behaviour, it goes in core, where the
// golden tests against the Python/JS cores can actually run.
pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "SceneGuard"
include(":core", ":app")
