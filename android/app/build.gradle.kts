import com.android.build.api.dsl.ApplicationExtension
import java.io.File
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

val rustAbis: String = (project.findProperty("rustAbis") as String?) ?: "arm64-v8a"
val cargoProfile: String = (project.findProperty("cargoProfile") as String?) ?: "release"

/**
 * Builds the Rust agent runtime for Android with the NDK and copies the shared
 * libraries into jniLibs. This is a real build step: the APK will not assemble
 * without the libraries it produces.
 *
 * Use -PskipRust=true only when the .so files were built by
 * scripts/build-rust-android.sh beforehand.
 */
/**
 * The script is a bash script, and on Windows `bash` on PATH is usually the WSL
 * shim in System32, which cannot run a `C:\...` path. Prefer Git Bash when it is
 * installed; on Linux/macOS the plain `bash` lookup is correct.
 */
val bashExecutable: String = run {
    val candidates = listOf(
        System.getenv("GIT_BASH"),
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
        "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
    )
    candidates.filterNotNull().firstOrNull { File(it).isFile } ?: "bash"
}

val cargoBuild = tasks.register<Exec>("cargoBuild") {
    group = "build"
    description = "Cross-compiles the FreeNerf Rust runtime for Android"

    val script = rootProject.file("../scripts/build-rust-android.sh")
    inputs.file(script)
    inputs.files(
        fileTree(rootProject.file("../native/crates")) { include("**/*.rs", "**/Cargo.toml") },
        rootProject.file("../native/Cargo.toml"),
        rootProject.file("../native/Cargo.lock"),
    )
    outputs.dir(layout.projectDirectory.dir("src/main/jniLibs"))

    workingDir = rootProject.projectDir
    environment("ANDROID_HOME", System.getenv("ANDROID_HOME") ?: "")
    commandLine(bashExecutable, script.absolutePath, cargoProfile, *rustAbis.split(",").toTypedArray())

    onlyIf { (project.findProperty("skipRust") as String?)?.toBoolean() != true }
}

android {
    namespace = "dev.freenerf.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.freenerf.app"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk {
            abiFilters += rustAbis.split(",").map { it.trim() }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += setOf(
            "/META-INF/{AL2.0,LGPL2.1}",
            "/META-INF/DEPENDENCIES",
        )
        jniLibs.useLegacyPackaging = false
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

tasks.named("preBuild") {
    dependsOn(cargoBuild)
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.02.00")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.3")
    implementation("androidx.documentfile:documentfile:1.1.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    // SSH client for the Remote settings tab and the agent's ssh_* tools.
    implementation("com.github.mwiede:jsch:2.28.8")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    debugImplementation("androidx.compose.ui:ui-tooling")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")

    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
