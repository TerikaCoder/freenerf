package dev.freenerf.app.data

import android.content.Context
import org.json.JSONObject

class SettingsRepository(context: Context, private val secrets: SecretStore) {
    private val prefs = context.applicationContext
        .getSharedPreferences("freenerf_settings", Context.MODE_PRIVATE)

    fun load(): AppSettings {
        val defaults = AppSettings()
        val models = decodeMap(prefs.getString(KEY_MODELS, null), defaults.models)
        // Base URLs deliberately start empty; the UI shows the provider default
        // as a placeholder instead of writing it into every field. Values that
        // only repeat a built-in default are dropped, so upgrades from older
        // builds also start blank.
        val urls = decodeMap(prefs.getString(KEY_URLS, null), emptyMap())
            .filterValues { value ->
                value.isNotBlank() && AppSettings.DEFAULT_URLS.values.none { it == value }
            }
        val keys = mutableMapOf<String, String>()
        for (provider in AppSettings.PROVIDERS) {
            val value = secrets.get(secretName(provider))
            if (value.isNotBlank()) keys[provider] = value
        }
        return AppSettings(
            provider = prefs.getString(KEY_PROVIDER, defaults.provider)
                ?.takeIf { it in AppSettings.PROVIDERS } ?: defaults.provider,
            models = models,
            baseUrls = urls,
            temperature = prefs.getFloat(KEY_TEMPERATURE, defaults.temperature),
            maxTokens = prefs.getInt(KEY_MAX_TOKENS, defaults.maxTokens),
            maxSteps = prefs.getInt(KEY_MAX_STEPS, defaults.maxSteps),
            streaming = prefs.getBoolean(KEY_STREAMING, defaults.streaming),
            permissionMode = prefs.getString(KEY_PERMISSION, defaults.permissionMode)
                ?.takeIf { it in AppSettings.PERMISSION_MODES } ?: defaults.permissionMode,
            customPrompt = prefs.getString(KEY_PROMPT, defaults.customPrompt) ?: "",
            imageModel = prefs.getString(KEY_IMAGE_MODEL, defaults.imageModel)
                ?: defaults.imageModel,
            workspacePath = prefs.getString(KEY_WORKSPACE, "") ?: "",
            themeMode = prefs.getString(KEY_THEME_MODE, defaults.themeMode)
                ?.takeIf { it in AppSettings.THEME_MODES } ?: defaults.themeMode,
            themeAccent = prefs.getString(KEY_THEME_ACCENT, defaults.themeAccent)
                ?.takeIf { it in AppSettings.THEME_ACCENTS } ?: defaults.themeAccent,
            customAccent = prefs.getString(KEY_CUSTOM_ACCENT, "") ?: "",
            dynamicColor = prefs.getBoolean(KEY_DYNAMIC_COLOR, defaults.dynamicColor),
            amoledBlack = prefs.getBoolean(KEY_AMOLED, defaults.amoledBlack),
            themeContrast = prefs.getString(KEY_THEME_CONTRAST, defaults.themeContrast)
                ?.takeIf { it in AppSettings.CONTRAST_LEVELS } ?: defaults.themeContrast,
            sshEnabled = prefs.getBoolean(KEY_SSH_ENABLED, defaults.sshEnabled),
            sshHost = prefs.getString(KEY_SSH_HOST, "") ?: "",
            sshPort = prefs.getInt(KEY_SSH_PORT, defaults.sshPort),
            sshUser = prefs.getString(KEY_SSH_USER, "") ?: "",
            sshRemoteDir = prefs.getString(KEY_SSH_DIR, "") ?: "",
            sshPassword = secrets.get(SECRET_SSH_PASSWORD),
            sshKey = secrets.get(SECRET_SSH_KEY),
            sshPassphrase = secrets.get(SECRET_SSH_PASSPHRASE),
            apiKeys = keys,
        )
    }

    fun save(settings: AppSettings) {
        prefs.edit()
            .putString(KEY_PROVIDER, settings.provider)
            .putString(KEY_MODELS, encodeMap(settings.models))
            .putString(KEY_URLS, encodeMap(settings.baseUrls))
            .putFloat(KEY_TEMPERATURE, settings.temperature)
            .putInt(KEY_MAX_TOKENS, settings.maxTokens)
            .putInt(KEY_MAX_STEPS, settings.maxSteps)
            .putBoolean(KEY_STREAMING, settings.streaming)
            .putString(KEY_PERMISSION, settings.permissionMode)
            .putString(KEY_PROMPT, settings.customPrompt)
            .putString(KEY_IMAGE_MODEL, settings.imageModel)
            .putString(KEY_WORKSPACE, settings.workspacePath)
            .putString(KEY_THEME_MODE, settings.themeMode)
            .putString(KEY_THEME_ACCENT, settings.themeAccent)
            .putString(KEY_CUSTOM_ACCENT, settings.customAccent)
            .putBoolean(KEY_DYNAMIC_COLOR, settings.dynamicColor)
            .putBoolean(KEY_AMOLED, settings.amoledBlack)
            .putString(KEY_THEME_CONTRAST, settings.themeContrast)
            .putBoolean(KEY_SSH_ENABLED, settings.sshEnabled)
            .putString(KEY_SSH_HOST, settings.sshHost)
            .putInt(KEY_SSH_PORT, settings.sshPort)
            .putString(KEY_SSH_USER, settings.sshUser)
            .putString(KEY_SSH_DIR, settings.sshRemoteDir)
            .apply()

        // API keys and SSH credentials are written only through their own
        // setters; saving other settings must never overwrite or clear them.
    }

    fun saveApiKey(provider: String, value: String) {
        secrets.put(secretName(provider), value.trim())
    }

    fun saveSshSecrets(password: String, key: String, passphrase: String) {
        secrets.put(SECRET_SSH_PASSWORD, password.trim())
        secrets.put(SECRET_SSH_KEY, key.trim())
        secrets.put(SECRET_SSH_PASSPHRASE, passphrase)
    }

    private fun secretName(provider: String) = "api_key_${AppSettings.keySlot(provider)}"

    private fun encodeMap(map: Map<String, String>): String =
        JSONObject().also { json -> map.forEach { (key, value) -> json.put(key, value) } }.toString()

    private fun decodeMap(raw: String?, fallback: Map<String, String>): Map<String, String> {
        if (raw.isNullOrBlank()) return fallback
        return runCatching {
            val json = JSONObject(raw)
            val result = fallback.toMutableMap()
            for (provider in AppSettings.PROVIDERS) {
                val value = json.optString(provider, "")
                if (value.isNotBlank()) result[provider] = value
            }
            result
        }.getOrDefault(fallback)
    }

    private companion object {
        const val KEY_PROVIDER = "provider"
        const val KEY_MODELS = "models"
        const val KEY_URLS = "base_urls"
        const val KEY_TEMPERATURE = "temperature"
        const val KEY_MAX_TOKENS = "max_tokens"
        const val KEY_MAX_STEPS = "max_steps"
        const val KEY_STREAMING = "streaming"
        const val KEY_PERMISSION = "permission_mode"
        const val KEY_PROMPT = "custom_prompt"
        const val KEY_IMAGE_MODEL = "image_model"
        const val KEY_WORKSPACE = "workspace_path"
        const val KEY_THEME_MODE = "theme_mode"
        const val KEY_THEME_ACCENT = "theme_accent"
        const val KEY_CUSTOM_ACCENT = "theme_custom_accent"
        const val KEY_DYNAMIC_COLOR = "dynamic_color"
        const val KEY_AMOLED = "amoled_black"
        const val KEY_THEME_CONTRAST = "theme_contrast"
        const val KEY_SSH_ENABLED = "ssh_enabled"
        const val KEY_SSH_HOST = "ssh_host"
        const val KEY_SSH_PORT = "ssh_port"
        const val KEY_SSH_USER = "ssh_user"
        const val KEY_SSH_DIR = "ssh_remote_dir"

        const val SECRET_SSH_PASSWORD = "ssh_password"
        const val SECRET_SSH_KEY = "ssh_private_key"
        const val SECRET_SSH_PASSPHRASE = "ssh_passphrase"
    }
}
