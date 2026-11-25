package dev.freenerf.app.data

import androidx.compose.runtime.Immutable

/**
 * User-visible application settings. API keys and SSH credentials are held only
 * in memory here after being decrypted from [SecretStore]; they are never
 * written to the Rust databases or logs.
 *
 * Immutable on purpose: Compose can skip screens whose settings did not change
 * instead of re-reading and re-laying out the whole tree on every toggle.
 */
@Immutable
data class AppSettings(
    val provider: String = "openrouter",
    val models: Map<String, String> = DEFAULT_MODELS,

    /**
     * Base URLs the user actually typed. Empty by default on purpose: the
     * settings screen starts blank and only overrides a provider when the user
     * enters a URL. [DEFAULT_URLS] holds the placeholder hints and the fallback
     * used when talking to the runtime.
     */
    val baseUrls: Map<String, String> = emptyMap(),
    val temperature: Float = 0.2f,
    val maxTokens: Int = 4096,
    val maxSteps: Int = 25,
    val streaming: Boolean = true,
    val permissionMode: String = "normal",
    val customPrompt: String = "",
    val imageModel: String = "gpt-image-1",

    /** Absolute path of the workspace the agent may edit. Empty = app private. */
    val workspacePath: String = "",

    /** Theme Studio: system/light/dark, a brand accent and a pure-black surface. */
    val themeMode: String = "system",
    val themeAccent: String = "verdant",

    /**
     * Theme Studio custom accent as "#RRGGBB". Empty means "use the preset
     * accent"; the terminal app has the same idea in `/theme`.
     */
    val customAccent: String = "",
    val dynamicColor: Boolean = true,
    val amoledBlack: Boolean = false,

    /** Tone mapping shape: "standard", "medium" or "high" contrast. */
    val themeContrast: String = "standard",

    /** Optional SSH host that replaces the old submit-only Termux bridge. */
    val sshEnabled: Boolean = false,
    val sshHost: String = "",
    val sshPort: Int = 22,
    val sshUser: String = "",
    val sshRemoteDir: String = "",
    val sshPassword: String = "",
    val sshKey: String = "",
    val sshPassphrase: String = "",

    val apiKeys: Map<String, String> = emptyMap(),
) {
    fun modelFor(provider: String = this.provider): String =
        models[provider] ?: DEFAULT_MODELS[provider] ?: ""

    fun keyFor(provider: String = this.provider): String = apiKeys[provider] ?: ""

    fun hasKey(provider: String = this.provider): Boolean = keyFor(provider).isNotBlank()

    /** The URL to use for this provider: the user's, else the built-in default. */
    fun effectiveBaseUrl(provider: String = this.provider): String =
        baseUrls[provider]?.takeIf { it.isNotBlank() }
            ?: DEFAULT_URLS[provider]
            ?: ""

    fun urlHint(provider: String): String = DEFAULT_URLS[provider] ?: ""

    val sshConfigured: Boolean
        get() = sshHost.isNotBlank() && sshUser.isNotBlank() &&
            (sshPassword.isNotBlank() || sshKey.isNotBlank())

    companion object {
        val PROVIDERS = listOf("openrouter", "gemini", "freellmapi", "openai-compatible")

        val PROVIDER_LABELS = mapOf(
            "openrouter" to "OpenRouter",
            "gemini" to "Google Gemini",
            "freellmapi" to "FreeLLMAPI",
            "openai-compatible" to "OpenAI-compatible",
        )

        val PROVIDER_SHORT = mapOf(
            "openrouter" to "OpenRouter",
            "gemini" to "Gemini",
            "freellmapi" to "FreeLLMAPI",
            "openai-compatible" to "OpenAI",
        )

        val DEFAULT_MODELS = mapOf(
            "openrouter" to "openai/gpt-4o-mini",
            "gemini" to "gemini-2.5-flash",
            "freellmapi" to "auto",
            "openai-compatible" to "gpt-4o-mini",
        )

        /** Placeholder hints and runtime fallbacks, never prefilled in the UI. */
        val DEFAULT_URLS = mapOf(
            "openrouter" to "https://openrouter.ai/api/v1",
            "gemini" to "https://generativelanguage.googleapis.com",
            "freellmapi" to "https://freellmapi-3d3g.onrender.com/v1",
            "openai-compatible" to "https://api.openai.com/v1",
        )

        val THEME_MODES = listOf("system", "light", "dark")

        val CONTRAST_LEVELS = listOf("standard", "medium", "high")

        fun contrastLabel(level: String): String = when (level) {
            "medium" -> "Medium"
            "high" -> "High"
            else -> "Standard"
        }

        /** Theme Studio accents, matching the terminal palette names. */
        val THEME_ACCENTS = listOf(
            "verdant", "violet", "ocean", "amber", "rose", "graphite",
        )

        fun accentLabel(accent: String): String = when (accent) {
            "violet" -> "Violet"
            "ocean" -> "Ocean"
            "amber" -> "Amber"
            "rose" -> "Rose"
            "graphite" -> "Graphite"
            else -> "Verdant"
        }

        /** Maps a UI provider id to the config key used by the Rust runtime. */
        fun keySlot(provider: String): String =
            if (provider == "openai-compatible") "openai" else provider

        val PERMISSION_MODES = listOf("denied", "normal", "full")

        fun permissionLabel(mode: String): String = when (mode) {
            "denied" -> "Read only"
            "full" -> "Full access"
            else -> "Ask first"
        }
    }
}
