package dev.freenerf.app

import org.json.JSONArray
import org.json.JSONObject

/**
 * The single JNI entry point into the Rust agent runtime.
 *
 * Every call returns a JSON envelope: `{"ok":true,"data":...}` on success or
 * `{"ok":false,"error":"..."}` on failure. Agent events arrive on arbitrary
 * runtime threads through [NativeBridgeCallbacks].
 */
object NativeCore {
    init {
        System.loadLibrary("freenerf")
    }

    external fun nativeCall(method: String, payload: String): String
    external fun nativeSetBridge(bridge: NativeBridgeCallbacks): Boolean
    external fun nativeClearBridge()
}

interface NativeBridgeCallbacks {
    /** Called from Rust worker threads with one serialized agent event. */
    fun onEvent(json: String)

    /**
     * Called synchronously from a Rust worker thread when a tool needs
     * approval. The implementation blocks until the user answers and returns
     * false on timeout or error (fail closed).
     */
    fun onPermissionRequest(json: String): Boolean

    /**
     * Called synchronously from a Rust worker thread to run an `ssh_*` tool.
     * Returns `{"ok":true,"output":"..."}` or `{"ok":false,"error":"..."}`.
     */
    fun onRemoteTool(json: String): String
}

class NativeException(message: String) : Exception(message)

object Native {
    private fun envelope(method: String, payload: JSONObject): JSONObject {
        val raw = try {
            NativeCore.nativeCall(method, payload.toString())
        } catch (error: Throwable) {
            throw NativeException("Native runtime unavailable: ${error.message}")
        }
        val envelope = runCatching { JSONObject(raw) }
            .getOrElse { throw NativeException("Malformed native response") }
        if (!envelope.optBoolean("ok", false)) {
            throw NativeException(envelope.optString("error", "Native call failed"))
        }
        return envelope
    }

    /** Performs a native call whose data is a JSON object. */
    fun call(method: String, payload: JSONObject = JSONObject()): JSONObject {
        val data = envelope(method, payload).opt("data")
        return data as? JSONObject ?: JSONObject()
    }

    /** Performs a native call whose data is a JSON array. */
    fun callArray(method: String, payload: JSONObject = JSONObject()): JSONArray {
        val data = envelope(method, payload).opt("data")
        return data as? JSONArray ?: JSONArray()
    }

    fun callOrNull(method: String, payload: JSONObject = JSONObject()): JSONObject? =
        runCatching { call(method, payload) }.getOrNull()

    fun callArrayOrEmpty(method: String, payload: JSONObject = JSONObject()): JSONArray =
        runCatching { callArray(method, payload) }.getOrDefault(JSONArray())
}
