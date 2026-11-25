package dev.freenerf.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeTopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberTopAppBarState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import dev.freenerf.app.AppContainer
import java.text.SimpleDateFormat
import java.util.Locale
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject

@Immutable
data class SessionsUiState(
    val sessions: List<JSONObject> = emptyList(),
    val loading: Boolean = true,
    val query: String = "",
    val searching: Boolean = false,
    val includeClosed: Boolean = true,
    val error: String? = null,
)

class SessionsViewModel(private val container: AppContainer) : ViewModel() {
    private val _state = MutableStateFlow(SessionsUiState())
    val state: StateFlow<SessionsUiState> = _state.asStateFlow()

    init {
        // Preload once so the list is already warm the first time the section
        // opens. The screen refreshes again on entry; there is deliberately no
        // event subscription here, because a session-list query per streamed
        // message would compete with the agent run for the runtime thread.
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            // Keep whatever list is already on screen: a refresh must not flash
            // a spinner over sessions the user can already read.
            _state.update { it.copy(loading = it.sessions.isEmpty()) }
            val list = if (_state.value.searching) {
                container.searchSessions(_state.value.query)
            } else {
                container.sessions(includeClosed = _state.value.includeClosed)
            }
            _state.update { it.copy(sessions = list, loading = false) }
        }
    }

    fun setQuery(query: String) {
        _state.update { it.copy(query = query) }
        viewModelScope.launch {
            val list = if (query.isBlank()) {
                container.sessions(includeClosed = _state.value.includeClosed)
            } else {
                container.searchSessions(query)
            }
            _state.update { it.copy(sessions = list, loading = false) }
        }
    }

    fun toggleSearching(searching: Boolean) {
        _state.update { it.copy(searching = searching, query = "") }
        refresh()
    }

    fun toggleClosed() {
        _state.update { it.copy(includeClosed = !it.includeClosed) }
        refresh()
    }

    fun rename(id: String, title: String) {
        viewModelScope.launch {
            container.renameSession(id, title)
            refresh()
        }
    }

    fun delete(id: String) {
        viewModelScope.launch {
            container.deleteSession(id)
            refresh()
        }
    }

    fun setClosed(id: String, closed: Boolean) {
        viewModelScope.launch {
            container.closeSession(id, closed)
            refresh()
        }
    }

    class Factory(private val container: AppContainer) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            SessionsViewModel(container) as T
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionsScreen(
    container: AppContainer,
    viewModel: SessionsViewModel,
    onOpenSession: (String) -> Unit,
    onNewSession: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var menuFor by remember { mutableStateOf<String?>(null) }
    var renameFor by remember { mutableStateOf<JSONObject?>(null) }
    var deleteFor by remember { mutableStateOf<JSONObject?>(null) }
    var renameText by remember { mutableStateOf("") }

    // Coming back from a session must show the title/state it ended with.
    LaunchedEffect(Unit) { viewModel.refresh() }

    val scrollBehavior = TopAppBarDefaults.exitUntilCollapsedScrollBehavior(
        rememberTopAppBarState(),
    )

    Scaffold(
        modifier = modifier.nestedScroll(scrollBehavior.nestedScrollConnection),
        containerColor = MaterialTheme.colorScheme.surface,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            LargeTopAppBar(
                title = {
                    Column {
                        Text("Sessions", style = MaterialTheme.typography.headlineMedium)
                        Text(
                            when {
                                state.sessions.isEmpty() -> "Your workspace"
                                state.sessions.size == 1 -> "1 session"
                                else -> "${state.sessions.size} sessions"
                            },
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { viewModel.toggleSearching(!state.searching) }) {
                        Icon(
                            if (state.searching) {
                                Icons.AutoMirrored.Filled.ArrowBack
                            } else {
                                Icons.Filled.Search
                            },
                            contentDescription = if (state.searching) "Close search" else "Search sessions",
                        )
                    }
                },
                scrollBehavior = scrollBehavior,
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = onNewSession,
                icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                text = { Text("New session") },
                containerColor = MaterialTheme.colorScheme.primaryContainer,
                contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        },
    ) { padding ->
        BoxWithConstraints(Modifier.fillMaxSize().padding(padding)) {
            // Two columns from tablet width, mirroring the web session grid.
            val wide = maxWidth >= 900.dp
            Column(Modifier.fillMaxSize()) {
                if (state.searching) {
                    OutlinedTextField(
                        value = state.query,
                        onValueChange = viewModel::setQuery,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 4.dp),
                        placeholder = { Text("Search titles and messages") },
                        singleLine = true,
                        shape = MaterialTheme.shapes.extraLarge,
                        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
                        colors = OutlinedTextFieldDefaults.colors(
                            unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant,
                            focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerLow,
                            unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerLow,
                        ),
                    )
                }

                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    FilterChip(
                        selected = state.includeClosed,
                        onClick = { if (!state.includeClosed) viewModel.toggleClosed() },
                        label = { Text("All") },
                        leadingIcon = {
                            Icon(
                                Icons.Filled.FilterList,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                            )
                        },
                    )
                    FilterChip(
                        selected = !state.includeClosed,
                        onClick = { if (state.includeClosed) viewModel.toggleClosed() },
                        label = { Text("Active only") },
                    )
                }

                when {
                    state.loading -> Box(
                        Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator() }

                    state.sessions.isEmpty() -> EmptySessions(
                        searching = state.searching,
                        searchQuery = state.query,
                        onNewSession = onNewSession,
                    )

                    wide -> LazyVerticalGrid(
                        columns = GridCells.Adaptive(340.dp),
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(
                            start = 16.dp,
                            end = 16.dp,
                            top = 4.dp,
                            bottom = 96.dp,
                        ),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        items(
                            count = state.sessions.size,
                            key = { index -> state.sessions[index].optString("id") },
                        ) { index ->
                            val session = state.sessions[index]
                            SessionCard(
                                session = session,
                                menuOpen = menuFor == session.optString("id"),
                                onOpen = { onOpenSession(session.optString("id")) },
                                onMenu = { menuFor = session.optString("id") },
                                onDismissMenu = { menuFor = null },
                                onRename = {
                                    renameText = session.optString("title")
                                    renameFor = session
                                    menuFor = null
                                },
                                onToggleClosed = {
                                    viewModel.setClosed(
                                        session.optString("id"),
                                        !closedSession(session),
                                    )
                                    menuFor = null
                                },
                                onDelete = {
                                    deleteFor = session
                                    menuFor = null
                                },
                            )
                        }
                    }

                    else -> LazyColumn(
                        Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(
                            start = 16.dp,
                            end = 16.dp,
                            top = 4.dp,
                            bottom = 96.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        items(state.sessions, key = { it.optString("id") }) { session ->
                            SessionCard(
                                session = session,
                                menuOpen = menuFor == session.optString("id"),
                                onOpen = { onOpenSession(session.optString("id")) },
                                onMenu = { menuFor = session.optString("id") },
                                onDismissMenu = { menuFor = null },
                                onRename = {
                                    renameText = session.optString("title")
                                    renameFor = session
                                    menuFor = null
                                },
                                onToggleClosed = {
                                    viewModel.setClosed(
                                        session.optString("id"),
                                        !closedSession(session),
                                    )
                                    menuFor = null
                                },
                                onDelete = {
                                    deleteFor = session
                                    menuFor = null
                                },
                            )
                        }
                    }
                }
            }
        }
    }

    renameFor?.let { session ->
        AlertDialog(
            onDismissRequest = { renameFor = null },
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
                        if (renameText.isNotBlank()) {
                            viewModel.rename(session.optString("id"), renameText.trim())
                        }
                        renameFor = null
                    },
                ) { Text("Save") }
            },
            dismissButton = {
                TextButton(onClick = { renameFor = null }) { Text("Cancel") }
            },
        )
    }

    deleteFor?.let { session ->
        AlertDialog(
            onDismissRequest = { deleteFor = null },
            shape = MaterialTheme.shapes.extraLarge,
            title = { Text("Delete session?") },
            text = {
                Text(
                    "\"${session.optString("title").ifBlank { "Untitled session" }}\" and its messages " +
                        "will be permanently deleted.",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.delete(session.optString("id"))
                        deleteFor = null
                    },
                ) { Text("Delete") }
            },
            dismissButton = {
                TextButton(onClick = { deleteFor = null }) { Text("Cancel") }
            },
        )
    }
}

private fun closedSession(session: JSONObject): Boolean = session.optInt("is_closed", 0) == 1

@Composable
private fun SessionCard(
    session: JSONObject,
    menuOpen: Boolean,
    onOpen: () -> Unit,
    onMenu: () -> Unit,
    onDismissMenu: () -> Unit,
    onRename: () -> Unit,
    onToggleClosed: () -> Unit,
    onDelete: () -> Unit,
) {
    val closed = closedSession(session)
    val title = session.optString("title").ifBlank { "Untitled session" }
    val shape = MaterialTheme.shapes.large
    Surface(
        modifier = Modifier.fillMaxWidth().clip(shape).clickable(onClick = onOpen),
        shape = shape,
        color = MaterialTheme.colorScheme.surfaceContainerLow,
    ) {
        Row(
            Modifier.padding(start = 14.dp, top = 12.dp, end = 4.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier
                    .size(46.dp)
                    .clip(MaterialTheme.shapes.medium)
                    .background(
                        if (closed) {
                            MaterialTheme.colorScheme.surfaceContainerHighest
                        } else {
                            MaterialTheme.colorScheme.secondaryContainer
                        },
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.Filled.Terminal,
                    contentDescription = null,
                    tint = if (closed) {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    } else {
                        MaterialTheme.colorScheme.onSecondaryContainer
                    },
                    modifier = Modifier.size(22.dp),
                )
            }
            Spacer(Modifier.size(14.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    buildString {
                        append(relativeTime(session.optString("updated_at")))
                        if (closed) {
                            append(" · Closed")
                        }
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (closed) {
                        MaterialTheme.colorScheme.tertiary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Box {
                IconButton(onClick = onMenu) {
                    Icon(Icons.Filled.MoreVert, contentDescription = "Session actions")
                }
                DropdownMenu(
                    expanded = menuOpen,
                    onDismissRequest = onDismissMenu,
                    shape = MaterialTheme.shapes.medium,
                ) {
                    DropdownMenuItem(
                        text = { Text("Rename") },
                        leadingIcon = { Icon(Icons.Filled.Edit, null) },
                        onClick = onRename,
                    )
                    DropdownMenuItem(
                        text = { Text(if (closed) "Reopen" else "Close") },
                        leadingIcon = { Icon(Icons.AutoMirrored.Filled.ArrowBack, null) },
                        onClick = onToggleClosed,
                    )
                    DropdownMenuItem(
                        text = { Text("Delete") },
                        leadingIcon = { Icon(Icons.Filled.Delete, null) },
                        onClick = onDelete,
                    )
                }
            }
        }
    }
}

@Composable
private fun EmptySessions(
    searching: Boolean,
    searchQuery: String,
    onNewSession: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().padding(horizontal = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            Modifier
                .size(84.dp)
                .clip(MaterialTheme.shapes.extraLarge)
                .background(MaterialTheme.colorScheme.secondaryContainer),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Filled.Terminal,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSecondaryContainer,
                modifier = Modifier.size(36.dp),
            )
        }
        Spacer(Modifier.size(20.dp))
        Text(
            if (searching && searchQuery.isNotBlank()) "No matching sessions" else "No sessions yet",
            style = MaterialTheme.typography.headlineSmall,
        )
        Spacer(Modifier.size(6.dp))
        Text(
            if (searching && searchQuery.isNotBlank()) {
                "Try a different word, or start a fresh session."
            } else {
                "A session is one agent run: ask, plan, and let it work in your workspace."
            },
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        Spacer(Modifier.size(20.dp))
        ExtendedFloatingActionButton(
            onClick = onNewSession,
            icon = { Icon(Icons.Filled.Add, contentDescription = null) },
            text = { Text("Start a session") },
            containerColor = MaterialTheme.colorScheme.primary,
            contentColor = MaterialTheme.colorScheme.onPrimary,
        )
    }
}

/** "Just now", "12m ago", a short date — never a raw ISO timestamp. */
private fun relativeTime(raw: String): String {
    if (raw.isBlank()) return "No activity yet"
    val parsed = runCatching {
        val normalized = raw.trim().replace('T', ' ').take(19)
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(normalized)
    }.getOrNull() ?: return raw.take(10)
    val delta = (System.currentTimeMillis() - parsed.time).coerceAtLeast(0L)
    return when {
        delta < 60_000L -> "Just now"
        delta < 3_600_000L -> "${delta / 60_000L}m ago"
        delta < 86_400_000L -> "${delta / 3_600_000L}h ago"
        delta < 7 * 86_400_000L -> "${delta / 86_400_000L}d ago"
        else -> SimpleDateFormat("MMM d", Locale.US).format(parsed)
    }
}
