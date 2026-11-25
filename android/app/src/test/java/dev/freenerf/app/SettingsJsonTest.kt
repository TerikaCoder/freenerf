package dev.freenerf.app

import dev.freenerf.app.data.AppSettings
import dev.freenerf.app.data.SettingsJson
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsJsonTest {
    @Test
    fun mapsProvidersToConfigKeySlots() {
        val settings = AppSettings(
            provider = "openai-compatible",
            apiKeys = mapOf("openai-compatible" to "sk-test"),
            models = AppSettings.DEFAULT_MODELS + ("openai-compatible" to "my-model"),
        )
        val config = SettingsJson.buildConfig(settings)
        assertEquals("openai-compatible", config.getString("provider"))
        assertEquals("sk-test", config.getJSONObject("api_keys").getString("openai"))
        assertEquals("my-model", config.getJSONObject("models").getString("openai"))
        assertFalse(config.getJSONObject("api_keys").has("openai-compatible"))
    }

    @Test
    fun includesEveryConfigFieldTheRustSideExpects() {
        val config = SettingsJson.buildConfig(AppSettings())
        for (key in listOf(
            "provider", "api_keys", "models", "base_urls", "temperature", "max_tokens",
            "request_timeout", "max_retries", "max_agent_steps", "max_context_chars",
            "custom_prompt", "streaming", "permission_mode", "image_model",
        )) {
            assertTrue("missing $key", config.has(key))
        }
    }

    @Test
    fun parsesLegacyPythonConfigAndNormalizesProvider() {
        val legacy = JSONObject(
            """
            {
              "provider": "openai",
              "api_keys": {"openai": "sk-legacy", "gemini": ""},
              "models": {"openai": "gpt-4o"},
              "base_urls": {"openai": "https://example.test/v1"},
              "temperature": 0.7,
              "max_tokens": 2048,
              "max_agent_steps": 12,
              "permission_mode": "full",
              "streaming": false
            }
            """.trimIndent(),
        )
        val (settings, keys) = SettingsJson.parseConfig(legacy, AppSettings())
        assertEquals("openai-compatible", settings.provider)
        assertEquals("gpt-4o", settings.modelFor("openai-compatible"))
        assertEquals("https://example.test/v1", settings.baseUrls["openai-compatible"])
        assertEquals(0.7f, settings.temperature, 0.001f)
        assertEquals(2048, settings.maxTokens)
        assertEquals(12, settings.maxSteps)
        assertEquals("full", settings.permissionMode)
        assertFalse(settings.streaming)
        assertEquals("sk-legacy", keys["openai-compatible"])
    }

    @Test
    fun ignoresMaskedKeysFromPublicConfig() {
        val public = JSONObject(
            """{"provider":"gemini","api_keys":{"gemini":"********"}}""",
        )
        val (_, keys) = SettingsJson.parseConfig(public, AppSettings())
        assertTrue(keys.isEmpty())
    }
}
