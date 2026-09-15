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
    // JUnit 4 test classes need the vintage engine to run at all: without this,
    // `useJUnitPlatform` finds zero tests and Gradle reports success. A silently
    // empty suite is worse than a red one, so it is declared explicitly and the
    // CI step fails if the result XML ever contains fewer than 8 tests.
    testRuntimeOnly("org.junit.vintage:junit-vintage-engine:5.10.2")
}
tasks.withType<Test> {
    useJUnitPlatform { includeEngines("junit-vintage") }
    reports.junitXml.required.set(true)
}
