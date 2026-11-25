package dev.freenerf.app.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * API keys are encrypted with an AES-256-GCM key held by the Android Keystore
 * and stored as ciphertext in private SharedPreferences. Plaintext keys never
 * touch disk, logs, or the Rust databases.
 */
class SecretStore(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("freenerf_secrets", Context.MODE_PRIVATE)

    fun get(name: String): String {
        val encoded = prefs.getString(name, null) ?: return ""
        return runCatching {
            val parts = encoded.split(":", limit = 2)
            if (parts.size != 2) return ""
            val iv = Base64.decode(parts[0], Base64.NO_WRAP)
            val ciphertext = Base64.decode(parts[1], Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(ciphertext), Charsets.UTF_8)
        }.getOrDefault("")
    }

    fun put(name: String, value: String) {
        if (value.isBlank()) {
            prefs.edit().remove(name).apply()
            return
        }
        runCatching {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.ENCRYPT_MODE, secretKey())
            val ciphertext = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
            val encoded = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
                Base64.encodeToString(ciphertext, Base64.NO_WRAP)
            prefs.edit().putString(name, encoded).apply()
        }
    }

    fun has(name: String): Boolean = prefs.contains(name)

    /**
     * The Keystore key is looked up once per process. Loading it on every
     * get/put cost a `KeyStore.load` plus an entry lookup for each secret, and
     * the settings screen reads seven of them.
     */
    @Volatile
    private var cachedKey: SecretKey? = null

    @Synchronized
    private fun secretKey(): SecretKey {
        cachedKey?.let { return it }
        val keyStore = KeyStore.getInstance(PROVIDER).apply { load(null) }
        val existing = (keyStore.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey
        val key = existing ?: run {
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER)
            generator.init(
                KeyGenParameterSpec.Builder(
                    ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
            generator.generateKey()
        }
        cachedKey = key
        return key
    }

    private companion object {
        const val PROVIDER = "AndroidKeyStore"
        const val ALIAS = "freenerf-api-keys"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
