package dev.freenerf.app.data

import org.json.JSONObject

/** Serialization between [AppSettings] and the Rust `Config` struct. */
object SettingsJson {
    fun buildConfig(settings: AppSettings, includeKeys: Boolean = true): JSONObject {
        val keys = JSONObject()
        val models = JSONObject()
        val urls = JSONObject()
        for (provider in AppSettings.PROVIDERS) {
            val slot = AppSettings.keySlot(provider)
            keys.put(slot, if (includeKeys) settings.keyFor(provider) else "")
            models.put(slot, settings.modelFor(provider))
            // A blank field keeps the runtime's built-in endpoint; only an
            // explicit override is sent.
            urls.put(
                slot,
                settings.baseUrls[provider]?.takeIf { it.isNotBlank() }
                    ?: AppSettings.DEFAULT_URLS[provider]
                    ?: "",
            )
        }
        return JSONObject()
            .put("provider", settings.provider)
            .put("api_keys", keys)
            .put("models", models)
            .put("base_urls", urls)
            .put("temperature", settings.temperature.toDouble())
            .put("max_tokens", settings.maxTokens)
            .put("request_timeout", 90.0)
            .put("max_retries", 2)
            .put("max_agent_steps", settings.maxSteps)
            .put("max_context_chars", 120_000)
            .put("custom_prompt", settings.customPrompt)
            .put("streaming", settings.streaming)
            .put("permission_mode", settings.permissionMode)
            .put("image_model", settings.imageModel)
    }

    /**
     * Applies a config JSON produced by either the Python CLI (`config.json`) or
     * this app's own persisted settings. API keys are returned separately so
     * the caller can place them in the Keystore instead of preferences.
     */
    fun parseConfig(json: JSONObject, current: AppSettings): Pair<AppSettings, Map<String, String>> {
        val keysJson = json.optJSONObject("api_keys") ?: JSONObject()
        val modelsJson = json.optJSONObject("models") ?: JSONObject()
        val urlsJson = json.optJSONObject("base_urls") ?: JSONObject()

        val keys = mutableMapOf<String, String>()
        for (provider in AppSettings.PROVIDERS) {
            val slot = AppSettings.keySlot(provider)
            val value = keysJson.optString(slot, "")
            if (value.isNotBlank() && value != "********") keys[provider] = value
        }

        val models = current.models.toMutableMap()
        val urls = current.baseUrls.toMutableMap()
        for (provider in AppSettings.PROVIDERS) {
            val slot = AppSettings.keySlot(provider)
            if (modelsJson.has(slot) && modelsJson.optString(slot).isNotBlank()) {
                models[provider] = modelsJson.optString(slot)
            }
            if (urlsJson.has(slot) && urlsJson.optString(slot).isNotBlank()) {
                urls[provider] = urlsJson.optString(slot)
            }
        }

        val provider = json.optString("provider", current.provider).let {
            if (it == "openai") "openai-compatible" else it
        }

        return AppSettings(
            provider = if (provider in AppSettings.PROVIDERS) provider else current.provider,
            models = models,
            baseUrls = urls,
            temperature = json.optDouble("temperature", current.temperature.toDouble())
                .toFloat().coerceIn(0f, 2f),
            maxTokens = json.optInt("max_tokens", current.maxTokens).coerceIn(1, 32768),
            maxSteps = json.optInt("max_agent_steps", current.maxSteps).coerceIn(1, 100),
            streaming = json.optBoolean("streaming", current.streaming),
            permissionMode = json.optString("permission_mode", current.permissionMode)
                .takeIf { it in AppSettings.PERMISSION_MODES } ?: current.permissionMode,
            customPrompt = json.optString("custom_prompt", current.customPrompt),
            imageModel = json.optString("image_model", current.imageModel),
            workspacePath = current.workspacePath,
            themeMode = current.themeMode,
            themeAccent = current.themeAccent,
            customAccent = current.customAccent,
            dynamicColor = current.dynamicColor,
            amoledBlack = current.amoledBlack,
            themeContrast = current.themeContrast,
            sshEnabled = current.sshEnabled,
            sshHost = current.sshHost,
            sshPort = current.sshPort,
            sshUser = current.sshUser,
            sshRemoteDir = current.sshRemoteDir,
            sshPassword = current.sshPassword,
            sshKey = current.sshKey,
            sshPassphrase = current.sshPassphrase,
            apiKeys = current.apiKeys + keys,
        ) to keys
    }
}
