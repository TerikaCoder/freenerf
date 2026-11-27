package dev.freenerf.app.ui

import androidx.compose.runtime.Immutable
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import dev.freenerf.app.AppContainer
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.UUID

enum class ToolStatus { Running, Completed, Failed }

@Immutable
data class ToolCard(
    val callId: String,
    val name: String,
    val summary: String,
    val args: String,
    val policy: String,
    val status: ToolStatus = ToolStatus.Running,
    val output: String = "",
    val elapsed: Double = 0.0,
)

@Immutable
sealed interface ChatItem {
    val id: String

    data class User(override val id: String, val text: String) : ChatItem

    data class Assistant(
        override val id: String,
        val text: String,
        val streaming: Boolean,
        val tools: List<ToolCard> = emptyList(),
        val notice: String? = null,
        val isError: Boolean = false,
    ) : ChatItem

    data class Notice(override val id: String, val text: String, val isError: Boolean = false) : ChatItem
}

@Immutable
data class Usage(val input: Long = 0, val output: Long = 0, val total: Long = 0) {
    val isEmpty: Boolean get() = input == 0L && output == 0L && total == 0L
}

@Immutable
data class TaskStepUi(val title: String, val status: String, val detail: String)

@Immutable
data class TaskProgressUi(
    val title: String,
    val status: String,
    val summary: String,
    val progress: Int,
    val doneSteps: Int,
    val totalSteps: Int,
    val steps: List<TaskStepUi>,
)

@Immutable
data class ChatUiState(
    val sessionId: String? = null,
    val title: String = "",
    val items: List<ChatItem> = emptyList(),
    val busy: Boolean = false,
    val loading: Boolean = false,
    val status: String = "idle",
    val error: String? = null,
    val usage: Usage = Usage(),
    val task: TaskProgressUi? = null,
)

class ChatViewModel(private val container: AppContainer) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var sessionId: String? = null

    // Streaming tokens are buffered and flushed at most once per interval so a
    // fast provider cannot trigger a recomposition (and a Markdown reparse) per
    // token. All buffer access happens on the main dispatcher.
    private val tokenBuffer = StringBuilder()
    private var tokenMessageId: String = ""
    private var flushJob: Job? = null

    init {
        viewModelScope.launch {
            container.busy.collect { busy ->
                _state.update { it.copy(busy = busy) }
            }
        }
        viewModelScope.launch {
            container.events.collect { event ->
                val active = container.activeSession.value
                val current = sessionId
                val foreign = active != null && active.isNotEmpty() && current != null &&
                    active != current
                if (!foreign) handle(event)
            }
        }
    }

    fun startNew() {
        sessionId = null
        clearBuffer()
        _state.value = ChatUiState(loading = false)
    }

    fun openSession(id: String) {
        sessionId = id
        clearBuffer()
        _state.value = ChatUiState(sessionId = id, loading = true)
        viewModelScope.launch {
            val export = container.exportSession(id)
            val title = export?.optJSONObject("session")?.optString("title") ?: ""
            val task = container.tasks(limit = 1, sessionId = id).firstOrNull()
                ?.let(::taskProgress)
            val items = mutableListOf<ChatItem>()
            val messages = export?.optJSONArray("messages")
            if (messages != null) {
                for (index in 0 until messages.length()) {
                    val message = messages.optJSONObject(index) ?: continue
                    val role = message.optString("role")
                    val content = message.optString("content")
                    if (content.isBlank()) continue
                    when (role) {
                        "user" -> items += ChatItem.User("history-u-$index", content)
                        "assistant" -> items += ChatItem.Assistant(
                            id = "history-a-$index",
                            text = content,
                            streaming = false,
                        )
                    }
                }
            }
            if (items.isEmpty()) {
                items += ChatItem.Notice(
                    "notice-empty",
                    "This session has no messages yet. Ask something to get started.",
                )
            }
            if (sessionId == id) {
                _state.update {
                    it.copy(items = items, title = title, task = task, loading = false)
                }
            }
        }
    }

    fun send(text: String) {
        val prompt = text.trim()
        if (prompt.isEmpty() || _state.value.busy) return
        clearBuffer()
        _state.update {
            it.copy(
                items = it.items + ChatItem.User("user-${UUID.randomUUID()}", prompt),
                error = null,
                status = "thinking",
                task = null,
            )
        }
        viewModelScope.launch {
            val result = container.runAgent(sessionId, prompt)
            if (result.sessionId != null && sessionId == null) {
                sessionId = result.sessionId
                _state.update { it.copy(sessionId = result.sessionId) }
            }
            if (!result.ok) {
                _state.update { it.copy(error = result.error, status = "failed") }
            } else {
                val status = result.outcome?.optString("status").orEmpty()
                // The run is over: drop any half-streamed placeholder so only
                // the final answer remains, and clear the transient status.
                finishStreaming()
                _state.update { it.copy(status = status.ifBlank { "completed" }) }
                if (status == "awaiting_approval") {
                    appendNotice(
                        "A tool needed approval that was not granted. Change the permission mode " +
                            "in Settings or retry.",
                    )
                }
            }
        }
    }

    fun stop() {
        viewModelScope.launch { container.cancelAgent() }
    }

    fun dismissError() = _state.update { it.copy(error = null) }

    fun refreshSessionMeta() {
        val id = sessionId ?: return
        viewModelScope.launch {
            val export = container.exportSession(id)
            val title = export?.optJSONObject("session")?.optString("title") ?: return@launch
            _state.update { it.copy(title = title) }
        }
    }

    fun rename(title: String) {
        val id = sessionId ?: return
        viewModelScope.launch {
            if (container.renameSession(id, title)) _state.update { it.copy(title = title) }
        }
    }

    fun clearConversation() {
        val id = sessionId ?: return
        viewModelScope.launch {
            container.clearSession(id)
            clearBuffer()
            _state.update { it.copy(items = emptyList(), usage = Usage()) }
        }
    }

    // ------------------------------------------------------------------
    // Streaming helpers
    // ------------------------------------------------------------------
    private fun clearBuffer() {
        flushJob?.cancel()
        flushJob = null
        tokenBuffer.clear()
        tokenMessageId = ""
    }

    private fun queueToken(messageId: String, token: String) {
        if (token.isEmpty()) return
        if (tokenMessageId != messageId) {
            flushTokens()
            tokenMessageId = messageId
        }
        tokenBuffer.append(token)
        if (flushJob?.isActive == true) return
        flushJob = viewModelScope.launch {
            delay(TOKEN_FLUSH_MS)
            flushTokens()
        }
    }

    private fun flushTokens() {
        if (tokenBuffer.isEmpty()) return
        val chunk = tokenBuffer.toString()
        tokenBuffer.clear()
        val messageId = tokenMessageId
        if (messageId.isEmpty()) return
        _state.update { state ->
            var found = false
            val items = state.items.map { item ->
                if (item is ChatItem.Assistant && item.id == messageId) {
                    found = true
                    item.copy(text = item.text + chunk, streaming = true)
                } else {
                    item
                }
            }
            if (found) {
                state.copy(items = items, status = "responding")
            } else {
                state.copy(
                    items = items + ChatItem.Assistant(messageId, chunk, streaming = true),
                    status = "responding",
                )
            }
        }
    }

    /** Clears every streaming indicator once the answer is final. */
    private fun finishStreaming() {
        flushJob?.cancel()
        flushJob = null
        flushTokens()
        tokenMessageId = ""
        _state.update { state ->
            state.copy(
                items = state.items.map { item ->
                    if (item is ChatItem.Assistant && item.streaming) {
                        item.copy(streaming = false)
                    } else {
                        item
                    }
                },
                status = "",
            )
        }
    }

    private fun handle(event: JSONObject) {
        when (event.optString("type")) {
            "assistant_message_started" -> {
                val messageId = event.optString("message_id")
                _state.update { state ->
                    if (state.items.any { it is ChatItem.Assistant && it.id == messageId }) {
                        state
                    } else {
                        state.copy(
                            items = state.items + ChatItem.Assistant(messageId, "", streaming = true),
                            status = "responding",
                        )
                    }
                }
            }

            "assistant_token" -> {
                queueToken(event.optString("message_id"), event.optString("text"))
            }

            "assistant_message_completed" -> {
                val messageId = event.optString("message_id")
                val content = event.optString("content")
                // The final text replaces whatever was streamed, so nothing is
                // duplicated and no partial tail is left behind.
                flushJob?.cancel()
                flushJob = null
                flushTokens()
                if (tokenMessageId == messageId) tokenMessageId = ""
                _state.update { state ->
                    var found = false
                    val items = state.items.map { item ->
                        if (item is ChatItem.Assistant && item.id == messageId) {
                            found = true
                            item.copy(text = content, streaming = false)
                        } else {
                            item
                        }
                    }
                    // An empty turn (a tool-only step or a cancelled reply) must
                    // not leave an empty bubble behind: drop the placeholder so
                    // the conversation only holds real text.
                    val cleaned = if (content.isBlank()) {
                        items.filterNot {
                            it is ChatItem.Assistant && it.id == messageId && it.tools.isEmpty()
                        }
                    } else {
                        items
                    }
                    when {
                        content.isNotBlank() && found -> state.copy(items = cleaned)
                        content.isNotBlank() -> state.copy(
                            items = cleaned + ChatItem.Assistant(
                                messageId,
                                content,
                                streaming = false,
                            ),
                        )
                        else -> if (cleaned === items) state else state.copy(items = cleaned)
                    }                }
            }

            "tool_started" -> {
                val card = ToolCard(
                    callId = event.optString("call_id"),
                    name = event.optString("name"),
                    summary = event.optString("summary"),
                    args = event.opt("args")?.toString().orEmpty(),
                    policy = event.optString("policy"),
                )
                _state.update { state -> state.copy(items = attachTool(state.items, card)) }
            }

            "tool_completed" -> updateTool(event, failed = false)
            "tool_failed" -> updateTool(event, failed = true)

            "tool_progress" -> {
                val callId = event.optString("call_id")
                val message = event.optString("message")
                _state.update { state ->
                    state.copy(
                        items = state.items.map { item ->
                            if (item is ChatItem.Assistant) {
                                item.copy(
                                    tools = item.tools.map { card ->
                                        if (card.callId == callId && card.output != message) {
                                            card.copy(output = message)
                                        } else {
                                            card
                                        }
                                    },
                                )
                            } else {
                                item
                            }
                        },
                    )
                }
            }

            "usage_updated" -> _state.update {
                it.copy(
                    usage = Usage(
                        input = event.optLong("input_tokens"),
                        output = event.optLong("output_tokens"),
                        total = event.optLong("total_tokens"),
                    ),
                )
            }

            "task_started", "task_updated" -> {
                event.optJSONObject("task")?.let { task ->
                    _state.update { it.copy(task = taskProgress(task)) }
                }
            }

            "task_completed" -> _state.update { state ->
                state.copy(
                    task = state.task?.copy(
                        status = event.optString("status", state.task.status),
                        summary = event.optString("summary", state.task.summary),
                    ),
                )
            }

            "task_failed" -> _state.update { state ->
                state.copy(
                    task = state.task?.copy(status = "failed", summary = event.optString("error")),
                )
            }

            "agent_status_changed" -> {
                val busy = event.optBoolean("busy", _state.value.busy)
                if (!busy) {
                    finishStreaming()
                    _state.update { it.copy(status = "") }
                } else {
                    _state.update { it.copy(status = event.optString("status")) }
                }
            }

            "error" -> {
                val message = event.optString("message", "Unknown error")
                _state.update { it.copy(error = message, status = "") }
                appendNotice(message, isError = true)
            }
        }
    }

    private fun updateTool(event: JSONObject, failed: Boolean) {
        val callId = event.optString("call_id")
        _state.update { state ->
            state.copy(
                items = state.items.map { item ->
                    if (item is ChatItem.Assistant) {
                        item.copy(
                            tools = item.tools.map { card ->
                                if (card.callId == callId) {
                                    card.copy(
                                        status = if (failed) ToolStatus.Failed else ToolStatus.Completed,
                                        output = if (failed) {
                                            event.optString("error")
                                        } else {
                                            event.optString("output")
                                        },
                                        elapsed = event.optDouble("elapsed", 0.0),
                                    )
                                } else {
                                    card
                                }
                            },
                        )
                    } else {
                        item
                    }
                },
            )
        }
    }

    private fun attachTool(items: List<ChatItem>, card: ToolCard): List<ChatItem> {
        val index = items.indexOfLast { it is ChatItem.Assistant }
        if (index == -1) {
            return items + ChatItem.Assistant(
                id = "tools-${card.callId}",
                text = "",
                streaming = false,
                tools = listOf(card),
            )
        }
        return items.mapIndexed { position, item ->
            if (position == index && item is ChatItem.Assistant) {
                item.copy(tools = item.tools + card)
            } else {
                item
            }
        }
    }

    private fun appendNotice(text: String, isError: Boolean = false) {
        _state.update {
            it.copy(items = it.items + ChatItem.Notice("notice-${UUID.randomUUID()}", text, isError))
        }
    }

    private fun taskProgress(task: JSONObject): TaskProgressUi {
        val stepsJson = task.optJSONArray("steps")
        val steps = buildList {
            if (stepsJson != null) {
                for (index in 0 until stepsJson.length()) {
                    val step = stepsJson.optJSONObject(index) ?: continue
                    add(
                        TaskStepUi(
                            title = step.optString("title", "Step ${index + 1}"),
                            status = step.optString("status", "pending"),
                            detail = step.optString("result").ifBlank { step.optString("error") },
                        ),
                    )
                }
            }
        }
        return TaskProgressUi(
            title = task.optString("title", "Current task"),
            status = task.optString("status", "running"),
            summary = task.optString("summary"),
            progress = task.optInt("progress").coerceIn(0, 100),
            doneSteps = task.optInt("done_steps"),
            totalSteps = task.optInt("step_count", steps.size),
            steps = steps,
        )
    }

    private companion object {
        const val TOKEN_FLUSH_MS = 70L
    }

    class Factory(private val container: AppContainer) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ChatViewModel(container) as T
    }
}
