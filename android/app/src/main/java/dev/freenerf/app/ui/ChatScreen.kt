package dev.freenerf.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.NorthEast
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.freenerf.app.AppContainer
import dev.freenerf.app.PendingPermission
import dev.freenerf.app.ui.components.MarkdownText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The Chat tab: the live conversation for the current session. The user's
 * instruction is shown as a "Prompt" block, the agent answers as plain
 * document text, and tool calls are tonal cards. There are no user avatars and
 * no "You" bubbles; history lives in the Sessions tab.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    container: AppContainer,
    viewModel: ChatViewModel,
    onOpenSessions: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    var input by rememberSaveable { mutableStateOf("") }
    var attachment by remember { mutableStateOf<String?>(null) }
    var menuOpen by remember { mutableStateOf(false) }
    var renameOpen by remember { mutableStateOf(false) }
    var renameText by remember { mutableStateOf("") }
    var confirmClear by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    val attachmentPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri != null) {
            scope.launch {
                withContext(Dispatchers.IO) { container.mirror.importDocument(uri) }
                    .onSuccess { attachment = it }
            }
        }
    }

    // Only follow the tail while the user is already at the bottom. The check
    // happens inside the effect instead of a derivedState read during
    // composition, so scrolling does not recompose the whole screen on every
    // frame. Scrolling is instant during a stream and animated between turns.
    val lastTextLength = (state.items.lastOrNull() as? ChatItem.Assistant)?.text?.length ?: 0
    LaunchedEffect(state.items.size, lastTextLength, state.busy) {
        if (state.items.isEmpty()) return@LaunchedEffect
        val info = listState.layoutInfo
        val last = info.visibleItemsInfo.lastOrNull()
        val atBottom = last == null || last.index >= info.totalItemsCount - 2
        if (!atBottom) return@LaunchedEffect
        if (state.busy) {
            listState.scrollToItem(state.items.lastIndex)
        } else {
            listState.animateScrollToItem(state.items.lastIndex)
        }
    }

    Scaffold(
        modifier = modifier,
        containerColor = MaterialTheme.colorScheme.surface,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = state.title.ifBlank {
                                if (state.items.isEmpty()) "New session" else "Session"
                            },
                            style = MaterialTheme.typography.titleMedium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            text = if (state.busy && state.status.isNotBlank()) {
                                state.status.replaceFirstChar { it.uppercase() }
                            } else {
                                container.workspace.name.ifBlank { "workspace" }
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = if (state.busy) {
                                MaterialTheme.colorScheme.primary
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            },
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = onOpenSessions) {
                        Icon(Icons.Filled.History, contentDescription = "Session history")
                    }
                    Box {
                        IconButton(onClick = { menuOpen = true }) {
                            Icon(Icons.Filled.MoreVert, contentDescription = "Session menu")
                        }
                        DropdownMenu(
                            expanded = menuOpen,
                            onDismissRequest = { menuOpen = false },
                            shape = MaterialTheme.shapes.medium,
                        ) {
                            DropdownMenuItem(
                                text = { Text("Rename session") },
                                enabled = state.sessionId != null,
                                leadingIcon = { Icon(Icons.Filled.Edit, null) },
                                onClick = {
                                    renameText = state.title
                                    renameOpen = true
                                    menuOpen = false
                                },
                            )
                            DropdownMenuItem(
                                text = { Text("Clear session") },
                                enabled = state.sessionId != null && !state.busy,
                                leadingIcon = { Icon(Icons.Filled.Delete, null) },
                                onClick = {
                                    confirmClear = true
                                    menuOpen = false
                                },
                            )
                        }
                    }
                },
            )
        },
        bottomBar = {
            Composer(
                value = input,
                onValueChange = { input = it },
                busy = state.busy,
                attachment = attachment,
                onAttach = { attachmentPicker.launch(arrayOf("*/*")) },
                onClearAttachment = { attachment = null },
                onSend = {
                    val text = buildString {
                        append(input.trim())
                        attachment?.let {
                            if (isNotEmpty()) append("\n\n")
                            append("[Attached file: $it]")
                        }
                    }
                    if (text.isNotBlank()) {
                        viewModel.send(text)
                        input = ""
                        attachment = null
                    }
                },
                onStop = viewModel::stop,
                usage = state.usage,
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            AnimatedVisibility(visible = state.error != null) {
                state.error?.let { message ->
                    Surface(
                        color = MaterialTheme.colorScheme.errorContainer,
                        shape = MaterialTheme.shapes.large,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(start = 16.dp, top = 8.dp, end = 16.dp),
                    ) {
                        Row(
                            Modifier.padding(start = 16.dp, top = 10.dp, end = 6.dp, bottom = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Text(
                                text = message,
                                color = MaterialTheme.colorScheme.onErrorContainer,
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.weight(1f),
                            )
                            IconButton(onClick = viewModel::dismissError) {
                                Icon(
                                    Icons.Filled.Close,
                                    contentDescription = "Dismiss error",
                                    tint = MaterialTheme.colorScheme.onErrorContainer,
                                )
                            }
                        }
                    }
                }
            }

            if (state.loading) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                return@Column
            }

            if (state.items.isEmpty()) {
                Column(
                    Modifier.fillMaxSize().widthIn(max = 880.dp)
                        .align(Alignment.CenterHorizontally),
                ) {
                    SessionStart(
                        workspace = container.workspace.name.ifBlank { "your workspace" },
                        onSuggestion = { viewModel.send(it) },
                    )
                }
                return@Column
            }

            // Reading width is capped on tablets, like the web layout, so
            // long answers stay a comfortable measure instead of full-bleed.
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().widthIn(max = 880.dp)
                    .align(Alignment.CenterHorizontally),
                contentPadding = PaddingValues(
                    start = 16.dp,
                    end = 16.dp,
                    top = 12.dp,
                    bottom = 20.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                items(
                    items = state.items,
                    key = { it.id },
                    contentType = { item -> item::class },
                ) { item ->
                    when (item) {
                        is ChatItem.User -> PromptBlock(item.text)
                        is ChatItem.Assistant -> AssistantMessage(item)
                        is ChatItem.Notice -> Text(
                            text = item.text,
                            style = MaterialTheme.typography.bodySmall,
                            color = if (item.isError) {
                                MaterialTheme.colorScheme.error
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            },
                            textAlign = TextAlign.Center,
                            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                        )
                    }
                }
            }
        }
    }

    if (renameOpen) {
        AlertDialog(
            onDismissRequest = { renameOpen = false },
            shape = MaterialTheme.shapes.extraLarge,
            title = { Text("Rename session") },
            text = {
                OutlinedTextField(
                    value = renameText,
                    onValueChange = { renameText = it },
                    singleLine = true,
                    label = { Text("Title") },
                    shape = MaterialTheme.shapes.medium,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        if (renameText.isNotBlank()) viewModel.rename(renameText.trim())
                        renameOpen = false
                    },
                ) { Text("Save") }
            },
            dismissButton = {
                TextButton(onClick = { renameOpen = false }) { Text("Cancel") }
            },
        )
    }

    if (confirmClear) {
        AlertDialog(
            onDismissRequest = { confirmClear = false },
            shape = MaterialTheme.shapes.extraLarge,
            title = { Text("Clear session?") },
            text = { Text("Messages in this session will be deleted. This cannot be undone.") },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.clearConversation()
                        confirmClear = false
                    },
                ) { Text("Clear") }
            },
            dismissButton = {
                TextButton(onClick = { confirmClear = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun SessionStart(workspace: String, onSuggestion: (String) -> Unit) {
    val suggestions = listOf(
        "Summarize this workspace",
        "Find and fix a failing test",
        "Write a README for this project",
        "Explain the project structure",
    )
    Column(
        Modifier.fillMaxSize().padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            Modifier
                .size(84.dp)
                .background(
                    MaterialTheme.colorScheme.primaryContainer,
                    RoundedCornerShape(28.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Filled.Terminal,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onPrimaryContainer,
                modifier = Modifier.size(36.dp),
            )
        }
        Spacer(Modifier.height(20.dp))
        Text("New session", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Describe the task. The agent reads, edits and runs tools inside $workspace.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 6.dp, bottom = 22.dp),
        )
        Column(
            Modifier.fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            for (suggestion in suggestions) {
                Surface(
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    shape = MaterialTheme.shapes.large,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(MaterialTheme.shapes.large)
                        .clickable { onSuggestion(suggestion) },
                ) {
                    Row(
                        Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Text(
                            suggestion,
                            style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.weight(1f),
                        )
                        Icon(
                            Icons.Filled.NorthEast,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }
            }
        }
    }
}

/** The instruction for this turn. A labeled block, not a messaging bubble. */
@Composable
private fun PromptBlock(text: String) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
        shape = MaterialTheme.shapes.large,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                "PROMPT",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.8.sp,
                color = MaterialTheme.colorScheme.primary,
            )
            SelectionContainer {
                Text(
                    text = text,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
        }
    }
}

@Composable
private fun AssistantMessage(item: ChatItem.Assistant) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (item.text.isNotBlank()) {
            MarkdownText(item.text, streaming = item.streaming)
        }
        if (item.tools.isNotEmpty()) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                for (tool in item.tools) ToolCardView(tool)
            }
        }
        if (item.streaming) {
            LinearProgressIndicator(
                Modifier
                    .fillMaxWidth(0.4f)
                    .height(3.dp)
                    .clip(CircleShape),
            )
        }
    }
}

@Composable
private fun ToolCardView(tool: ToolCard) {
    var expanded by remember { mutableStateOf(false) }
    Surface(
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        shape = MaterialTheme.shapes.large,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Box(
                    Modifier
                        .size(26.dp)
                        .background(
                            MaterialTheme.colorScheme.surfaceContainerHighest,
                            RoundedCornerShape(9.dp),
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    when (tool.status) {
                        ToolStatus.Running -> CircularProgressIndicator(
                            modifier = Modifier.size(14.dp),
                            strokeWidth = 2.dp,
                        )
                        ToolStatus.Completed -> Icon(
                            Icons.Filled.CheckCircle,
                            contentDescription = "Completed",
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(16.dp),
                        )
                        ToolStatus.Failed -> Icon(
                            Icons.Filled.Error,
                            contentDescription = "Failed",
                            tint = MaterialTheme.colorScheme.error,
                            modifier = Modifier.size(16.dp),
                        )
                    }
                }
                Text(
                    text = tool.name,
                    style = MaterialTheme.typography.labelLarge,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (tool.elapsed > 0) {
                    Text(
                        text = "${"%.1f".format(tool.elapsed)}s",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(onClick = { expanded = !expanded }, modifier = Modifier.size(30.dp)) {
                    Icon(
                        if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                        contentDescription = if (expanded) "Collapse" else "Expand",
                    )
                }
            }
            if (tool.summary.isNotBlank()) {
                Text(
                    text = tool.summary,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = if (expanded) Int.MAX_VALUE else 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
            AnimatedVisibility(visible = expanded) {
                Column(Modifier.padding(top = 8.dp)) {
                    if (tool.args.isNotBlank()) {
                        SelectionContainer {
                            Text(
                                text = tool.args,
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .background(
                                        MaterialTheme.colorScheme.surface,
                                        RoundedCornerShape(12.dp),
                                    )
                                    .horizontalScroll(rememberScrollState())
                                    .padding(10.dp),
                            )
                        }
                        Spacer(Modifier.height(6.dp))
                    }
                    if (tool.output.isNotBlank()) {
                        SelectionContainer {
                            Text(
                                text = tool.output.take(6000),
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .background(
                                        MaterialTheme.colorScheme.surface,
                                        RoundedCornerShape(12.dp),
                                    )
                                    .horizontalScroll(rememberScrollState())
                                    .padding(10.dp),
                            )
                        }
                    }
                    if (tool.policy.isNotBlank()) {
                        Text(
                            text = "policy: ${tool.policy}",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 6.dp),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun Composer(
    value: String,
    onValueChange: (String) -> Unit,
    busy: Boolean,
    attachment: String?,
    onAttach: () -> Unit,
    onClearAttachment: () -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    usage: Usage,
) {
    Surface(color = MaterialTheme.colorScheme.surface) {
        Column(
            Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 10.dp),
        ) {
            if (!usage.isEmpty) {
                Text(
                    "Tokens in ${usage.input} · out ${usage.output} · total ${usage.total}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 8.dp, bottom = 6.dp),
                )
            }
            if (attachment != null) {
                AssistChip(
                    onClick = onClearAttachment,
                    label = {
                        Text(
                            attachment.substringAfterLast('/'),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    },
                    trailingIcon = {
                        Icon(
                            Icons.Filled.Clear,
                            contentDescription = "Remove attachment",
                            modifier = Modifier.size(16.dp),
                        )
                    },
                    modifier = Modifier.padding(bottom = 6.dp),
                )
            }
            Surface(
                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                shape = MaterialTheme.shapes.extraLarge,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    Modifier.padding(4.dp),
                    verticalAlignment = Alignment.Bottom,
                ) {
                    IconButton(onClick = onAttach, enabled = !busy) {
                        Icon(Icons.Filled.AttachFile, contentDescription = "Attach file")
                    }
                    OutlinedTextField(
                        value = value,
                        onValueChange = onValueChange,
                        modifier = Modifier.weight(1f),
                        placeholder = { Text("Describe a task…") },
                        maxLines = 6,
                        shape = RoundedCornerShape(22.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = Color.Transparent,
                            unfocusedBorderColor = Color.Transparent,
                            disabledBorderColor = Color.Transparent,
                            focusedContainerColor = Color.Transparent,
                            unfocusedContainerColor = Color.Transparent,
                            disabledContainerColor = Color.Transparent,
                        ),
                    )
                    Spacer(Modifier.size(4.dp))
                    if (busy) {
                        FilledTonalIconButton(
                            onClick = onStop,
                            modifier = Modifier.padding(end = 4.dp),
                        ) {
                            Icon(Icons.Filled.Stop, contentDescription = "Stop")
                        }
                    } else {
                        FilledIconButton(
                            onClick = onSend,
                            enabled = value.isNotBlank() || attachment != null,
                            shape = CircleShape,
                            modifier = Modifier.padding(end = 4.dp),
                        ) {
                            Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ApprovalDialog(pending: PendingPermission, onAnswer: (Boolean) -> Unit) {
    AlertDialog(
        onDismissRequest = { onAnswer(false) },
        shape = MaterialTheme.shapes.extraLarge,
        icon = { Icon(Icons.Filled.Error, contentDescription = null) },
        title = { Text("Allow ${pending.name}?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (pending.reason.isNotBlank()) {
                    Text(pending.reason, style = MaterialTheme.typography.bodyMedium)
                }
                if (pending.detail.isNotBlank()) {
                    Surface(
                        color = MaterialTheme.colorScheme.surfaceContainerHigh,
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        SelectionContainer {
                            Text(
                                text = pending.detail,
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.padding(10.dp),
                            )
                        }
                    }
                }
                if (pending.policy.isNotBlank()) {
                    Text(
                        "policy: ${pending.policy}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onAnswer(true) }) { Text("Allow") }
        },
        dismissButton = {
            TextButton(onClick = { onAnswer(false) }) { Text("Deny") }
        },
    )
}
