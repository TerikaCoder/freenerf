package dev.freenerf.app

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.util.UUID

data class PendingPermission(
    val id: String,
    val name: String,
    val detail: String,
    val reason: String,
    val policy: String,
    internal val gate: CompletableDeferred<Boolean>,
)

/**
 * Bridges the Rust approval hook (called on a worker thread) to a Compose
 * dialog. The Rust call blocks until [answer] or the timeout, and failures fail
 * closed.
 */
class PermissionBroker {
    private val _current = MutableStateFlow<PendingPermission?>(null)
    val current: StateFlow<PendingPermission?> = _current

    fun request(json: String): Boolean {
        val parsed = runCatching { JSONObject(json) }.getOrElse {
            JSONObject().put("name", "Tool").put("detail", json)
        }
        val gate = CompletableDeferred<Boolean>()
        val pending = PendingPermission(
            id = UUID.randomUUID().toString(),
            name = parsed.optString("name", "Tool"),
            detail = parsed.optString("detail", ""),
            reason = parsed.optString("reason", ""),
            policy = parsed.optString("policy", ""),
            gate = gate,
        )
        _current.value = pending
        val result = runBlocking {
            withTimeoutOrNull(APPROVAL_TIMEOUT_MS) { gate.await() }
        } ?: false
        if (_current.value?.id == pending.id) _current.value = null
        return result
    }

    fun answer(approved: Boolean) {
        val pending = _current.value ?: return
        pending.gate.complete(approved)
        _current.value = null
    }

    private companion object {
        const val APPROVAL_TIMEOUT_MS = 120_000L
    }
}
