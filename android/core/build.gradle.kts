plugins {
    id("org.jetbrains.kotlin.jvm")
}
// NOT an android library. Plain JVM: `./gradlew :core:test` runs in ~4s on CI
// and on any laptop, which is the only reason the three cores stay in sync.
java {
    toolchain { languageVersion.set(JavaLanguageVersion.of(17)) }
}
dependencies {
    testImplementation("junit:junit:4.13.2")
}
tasks.withType<Test> { useJUnitPlatform { includeEngines("junit-vintage") } }
