package dev.freenerf.app

import android.content.Context
import dev.freenerf.app.data.AppSettings
import dev.freenerf.app.data.SecretStore
import dev.freenerf.app.data.SettingsJson
import dev.freenerf.app.data.SettingsRepository
import dev.freenerf.app.net.SshClient
import dev.freenerf.app.net.SshConfig
import dev.freenerf.app.storage.StorageAccess
import dev.freenerf.app.workspace.WorkspaceMirror
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

data class AgentRun(
    val ok: Boolean,
    val sessionId: String?,
    val outcome: JSONObject?,
    val error: String?,
)

/**
 * Application-scoped access to the native runtime, settings and workspace.
 * Native events flow through [events]; permission prompts through [permissions].
 */
class AppContainer(context: Context) : NativeBridgeCallbacks {
    val appContext: Context = context.applicationContext

    val secrets = SecretStore(appContext)
    val settingsRepository = SettingsRepository(appContext, secrets)
    val settings = MutableStateFlow(settingsRepository.load())

    /**
     * Settings writes run off the main thread. The in-memory flow is updated
     * immediately, so a toggle repaints on the next frame while disk IO and
     * Keystore encryption happen behind it.
     */
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val settingsMutex = Mutex()

    /**
     * The folder the agent may edit. Defaults to app-private storage and can be
     * pointed at any directory the user picks in Settings → Workspace.
     */
    @Volatile
    var workspace: File = resolveWorkspace(settings.value.workspacePath)
        private set

    @Volatile
    var mirror: WorkspaceMirror = WorkspaceMirror(appContext, workspace)
        private set

    val permissions = PermissionBroker()

    val events = MutableSharedFlow<JSONObject>(
        extraBufferCapacity = 2048,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    private val _activeSession = MutableStateFlow<String?>(null)
    val activeSession: StateFlow<String?> = _activeSession

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy

    /** Non-null when the native runtime could not be loaded on this device. */
    val nativeError: String?

    init {
        mirror.ensure()
        nativeError = runCatching {
            check(NativeCore.nativeSetBridge(this)) { "Native bridge registration failed" }
            Native.call("app.info")
        }.exceptionOrNull()?.message
    }

    /**
     * Publishes [updated] to the UI immediately and persists it in the
     * background. The previous implementation re-read every secret through the
     * Keystore on the calling (main) thread after each tap, which is what made
     * a Theme Studio switch feel like it took seconds.
     */
    fun saveSettings(updated: AppSettings) {
        settings.value = updated
        ioScope.launch {
            settingsMutex.withLock { settingsRepository.save(settings.value) }
        }
    }

    fun setApiKey(provider: String, key: String) {
        val current = settings.value
        val keys = current.apiKeys.toMutableMap()
        if (key.isBlank()) keys.remove(provider) else keys[provider] = key.trim()
        settings.value = current.copy(apiKeys = keys)
        ioScope.launch {
            settingsMutex.withLock { settingsRepository.saveApiKey(provider, key) }
        }
    }

    fun saveSshSecrets(password: String, key: String, passphrase: String) {
        val current = settings.value
        settings.value = current.copy(
            sshPassword = password.trim(),
            sshKey = key.trim(),
            sshPassphrase = passphrase,
        )
        ioScope.launch {
            settingsMutex.withLock { settingsRepository.saveSshSecrets(password, key, passphrase) }
        }
    }

    /** Applies a settings change; derived state reads the same flow. */
    fun applySettings(updated: AppSettings) {
        saveSettings(updated)
    }

    // ------------------------------------------------------------------
    // Native bridge callbacks (invoked on Rust threads)
    // ------------------------------------------------------------------
    override fun onEvent(json: String) {
        val event = runCatching { JSONObject(json) }.getOrNull() ?: return
        events.tryEmit(event)
    }

    override fun onPermissionRequest(json: String): Boolean = permissions.request(json)

    /** Executes the agent's `ssh_*` tools on the configured remote host. */
    override fun onRemoteTool(json: String): String {
        val request = runCatching { JSONObject(json) }.getOrElse { JSONObject() }
        val settingsNow = settings.value
        if (!settingsNow.sshEnabled || !settingsNow.sshConfigured) {
            return JSONObject()
                .put("ok", false)
                .put(
                    "error",
                    "No SSH host is configured. Enable it and add a host in Settings \u2192 SSH.",
                )
                .toString()
        }
        return SshClient.execute(sshConfig(), request).toString()
    }

    // ------------------------------------------------------------------
    // Workspace
    // ------------------------------------------------------------------
    private fun resolveWorkspace(path: String): File {
        val candidate = path.trim().takeIf { it.isNotEmpty() }?.let(::File)
            ?: return File(appContext.filesDir, "workspace")
        return if (candidate.exists() && !candidate.isDirectory) {
            File(appContext.filesDir, "workspace")
        } else {
            candidate
        }
    }

    /** Switches the active workspace and persists the choice. */
    fun setWorkspace(path: String): Result<File> = runCatching {
        val target = if (path.trim().isEmpty()) {
            File(appContext.filesDir, "workspace")
        } else {
            File(path.trim())
        }
        require(!target.exists() || target.isDirectory) { "$path is not a folder" }
        require(target.exists() || target.mkdirs()) { "Cannot create $path" }
        require(target.canWrite()) { "$path is not writable" }
        workspace = target
        mirror = WorkspaceMirror(appContext, target)
        mirror.ensure()
        saveSettings(settings.value.copy(workspacePath = target.absolutePath))
        target
    }

    fun hasStorageAccess(): Boolean = StorageAccess.hasAccess(appContext)

    fun resolvePathForWorkspace(path: String): File = File(path.trim())

    // ------------------------------------------------------------------
    // SSH
    // ------------------------------------------------------------------
    fun sshConfig(): SshConfig {
        val current = settings.value
        return SshConfig(
            host = current.sshHost,
            port = current.sshPort,
            user = current.sshUser,
            password = current.sshPassword,
            privateKey = current.sshKey,
            passphrase = current.sshPassphrase,
            remoteDir = current.sshRemoteDir,
        )
    }

    suspend fun testSsh(): String = withContext(Dispatchers.IO) {
        runCatching { SshClient.test(sshConfig()) }
            .getOrElse { "SSH connection failed: ${it.message}" }
    }

    // ------------------------------------------------------------------
    // Workspace / session / task helpers
    // ------------------------------------------------------------------
    private fun rootPayload(): JSONObject = JSONObject().put("root", workspace.absolutePath)

    suspend fun workspaceInfo(): JSONObject? = withContext(Dispatchers.IO) {
        Native.callOrNull("workspace.info", rootPayload())
    }

    suspend fun appInfo(): JSONObject? = withContext(Dispatchers.IO) {
        Native.callOrNull("app.info")
    }

    suspend fun sessions(includeClosed: Boolean = false, limit: Int = 40): List<JSONObject> =
        withContext(Dispatchers.IO) {
            Native.callArrayOrEmpty(
                "session.list",
                rootPayload().put("includeClosed", includeClosed).put("limit", limit),
            ).objects()
        }

    suspend fun searchSessions(query: String): List<JSONObject> = withContext(Dispatchers.IO) {
        Native.callArrayOrEmpty(
            "session.search",
            rootPayload().put("query", query).put("limit", 30),
        ).objects()
    }

    suspend fun createSession(title: String = "New session"): JSONObject? =
        withContext(Dispatchers.IO) {
            Native.callOrNull(
                "session.create",
                rootPayload().put("title", title).put("workspace", workspace.absolutePath),
            )
        }

    suspend fun renameSession(id: String, title: String): Boolean = withContext(Dispatchers.IO) {
        Native.callOrNull("session.rename", rootPayload().put("sessionId", id).put("title", title))
            ?.optBoolean("changed") ?: false
    }

    suspend fun deleteSession(id: String): Boolean = withContext(Dispatchers.IO) {
        Native.callOrNull("session.delete", rootPayload().put("sessionId", id))
            ?.optBoolean("changed") ?: false
    }

    suspend fun closeSession(id: String, closed: Boolean): Boolean = withContext(Dispatchers.IO) {
        val method = if (closed) "session.close" else "session.reopen"
        Native.callOrNull(method, rootPayload().put("sessionId", id))
            ?.optBoolean("changed") ?: false
    }

    suspend fun sessionMessages(id: String): List<JSONObject> = withContext(Dispatchers.IO) {
        Native.callArrayOrEmpty("session.messages", rootPayload().put("sessionId", id)).objects()
    }

    suspend fun sessionActivity(id: String): List<JSONObject> = withContext(Dispatchers.IO) {
        Native.callArrayOrEmpty(
            "session.activity",
            rootPayload().put("sessionId", id).put("limit", 200),
        ).objects()
    }

    suspend fun clearSession(id: String): Boolean = withContext(Dispatchers.IO) {
        Native.callOrNull("session.clear", rootPayload().put("sessionId", id)) != null
    }

    suspend fun exportSession(id: String): JSONObject? = withContext(Dispatchers.IO) {
        Native.callOrNull("session.export", rootPayload().put("sessionId", id))
    }

    suspend fun tasks(limit: Int = 50, sessionId: String? = null): List<JSONObject> =
        withContext(Dispatchers.IO) {
            val payload = rootPayload().put("limit", limit)
            if (!sessionId.isNullOrBlank()) payload.put("sessionId", sessionId)
            Native.callArrayOrEmpty("task.list", payload).objects()
        }

    suspend fun deleteTask(id: String): Boolean = withContext(Dispatchers.IO) {
        Native.callOrNull("task.delete", rootPayload().put("taskId", id))
            ?.optBoolean("changed") ?: false
    }

    suspend fun fetchModels(
        provider: String,
        query: String = "",
        force: Boolean = false,
    ): List<JSONObject> = withContext(Dispatchers.IO) {
        val current = settings.value
        val payload = JSONObject()
            .put("provider", provider)
            .put("apiKey", current.keyFor(provider))
            .put("baseUrl", current.effectiveBaseUrl(provider))
            .put("query", query)
            .put("toolsOnly", false)
            .put("forceRefresh", force)
        Native.callArrayOrEmpty("models.list", payload).objects()
    }

    // ------------------------------------------------------------------
    // Agent runs
    // ------------------------------------------------------------------
    suspend fun runAgent(sessionId: String?, prompt: String): AgentRun =
        withContext(Dispatchers.IO) {
            val current = settings.value
            _busy.value = true
            _activeSession.value = sessionId ?: ""
            try {
                val payload = rootPayload()
                    .put("sessionId", sessionId ?: "")
                    .put("prompt", prompt)
                    .put("config", SettingsJson.buildConfig(current))
                    .put("permissionMode", current.permissionMode)
                    .put("loadHistory", true)
                if (current.provider == "freellmapi" || current.keyFor("openai").isNotBlank()) {
                    payload.put("imageApiKey", current.keyFor("openai"))
                }
                val data = Native.call("agent.run", payload)
                AgentRun(
                    ok = true,
                    sessionId = data.optString("session_id").takeIf { it.isNotBlank() },
                    outcome = data.optJSONObject("outcome"),
                    error = null,
                )
            } catch (error: Throwable) {
                AgentRun(false, sessionId, null, error.message ?: "Agent run failed")
            } finally {
                _busy.value = false
                _activeSession.value = null
            }
        }

    suspend fun cancelAgent() = withContext(Dispatchers.IO) {
        runCatching { Native.call("agent.cancel") }
    }

    suspend fun importLegacyConfig(json: String, current: AppSettings): Result<AppSettings> =
        withContext(Dispatchers.IO) {
            runCatching {
                val migrated = Native.call("config.migrate", JSONObject().put("json", json))
                val (parsed, keys) = SettingsJson.parseConfig(migrated, current)
                keys.forEach { (provider, value) ->
                    if (value.isNotBlank()) settingsRepository.saveApiKey(provider, value)
                }
                val saved = parsed.copy(apiKeys = settingsRepository.load().apiKeys)
                saveSettings(saved)
                saved
            }
        }

    suspend fun importLegacyDatabases(legacyDir: String): JSONObject? = withContext(Dispatchers.IO) {
        Native.callOrNull(
            "workspace.legacy_import",
            rootPayload().put("legacyDir", legacyDir),
        )
    }

    /**
     * Copies user-picked legacy database files into a private staging directory
     * and asks the Rust side to import them. SAF URIs are not filesystem paths,
     * so the copy is required before the native import can run.
     */
    suspend fun importLegacyDatabasesFromUris(
        uris: List<android.net.Uri>,
    ): JSONObject? = withContext(Dispatchers.IO) {
        val staging = File(appContext.cacheDir, "legacy_import").apply {
            deleteRecursively()
            mkdirs()
        }
        for (uri in uris) {
            val original = displayName(uri) ?: ""
            val name = when {
                original.contains("task", ignoreCase = true) -> "tasks.db"
                original.contains("session", ignoreCase = true) -> "sessions.db"
                original.endsWith(".db") -> original
                else -> "sessions.db"
            }
            appContext.contentResolver.openInputStream(uri)?.use { input ->
                File(staging, name).outputStream().use { output -> input.copyTo(output) }
            }
        }
        Native.callOrNull(
            "workspace.legacy_import",
            rootPayload().put("legacyDir", staging.absolutePath),
        )
    }

    private fun displayName(uri: android.net.Uri): String? = runCatching {
        appContext.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
        }
    }.getOrNull()

    private fun org.json.JSONArray.objects(): List<JSONObject> =
        (0 until length()).mapNotNull { optJSONObject(it) }
}
