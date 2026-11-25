package dev.freenerf.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.freenerf.app.AppContainer

/**
 * Three top-level sections, the same split the terminal app uses: Chat is the
 * live conversation, Sessions is history and Settings is the settings centre.
 *
 * The shell is adaptive: a navigation rail on tablets and foldables, a bottom
 * bar on phones. Both ViewModels are hoisted here so switching section never
 * recreates a screen or reloads its data — the whole tab is already warm when
 * it becomes visible.
 */
private enum class Section(val key: String, val label: String, val icon: ImageVector) {
    Chat("chat", "Chat", Icons.AutoMirrored.Filled.Chat),
    Sessions("sessions", "Sessions", Icons.Filled.History),
    Settings("settings", "Settings", Icons.Filled.Settings),
}

@Composable
fun RootScreen(container: AppContainer) {
    container.nativeError?.let { error ->
        NativeErrorScreen(error)
        return
    }

    var section by rememberSaveable { mutableStateOf(Section.Chat.key) }
    val chat: ChatViewModel = viewModel(factory = ChatViewModel.Factory(container))
    val sessions: SessionsViewModel = viewModel(factory = SessionsViewModel.Factory(container))
    val pending by container.permissions.current.collectAsStateWithLifecycle()
    val stateHolder = rememberSaveableStateHolder()

    BoxWithConstraints(Modifier.fillMaxSize()) {
        // Material 3 window classes: compact phones get the bottom bar, medium
        // and expanded widths get a rail so content keeps its full height.
        val useRail = maxWidth >= 600.dp

        Scaffold(
            containerColor = MaterialTheme.colorScheme.surface,
            // Each screen owns its app bar and its insets; the shell only
            // reserves room for navigation, so nothing is padded twice.
            contentWindowInsets = WindowInsets(0, 0, 0, 0),
            bottomBar = {
                if (!useRail) {
                    NavigationBar(
                        containerColor = MaterialTheme.colorScheme.surfaceContainer,
                        tonalElevation = 0.dp,
                    ) {
                        Section.entries.forEach { entry ->
                            NavigationBarItem(
                                selected = section == entry.key,
                                onClick = { section = entry.key },
                                icon = { Icon(entry.icon, contentDescription = entry.label) },
                                label = { Text(entry.label) },
                            )
                        }
                    }
                }
            },
        ) { padding ->
            Row(Modifier.fillMaxSize().padding(padding)) {
                if (useRail) {
                    NavigationRail(
                        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
                        header = {
                            Spacer(Modifier.height(12.dp))
                            Box(
                                Modifier
                                    .size(48.dp)
                                    .background(
                                        MaterialTheme.colorScheme.primaryContainer,
                                        MaterialTheme.shapes.large,
                                    ),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text(
                                    "F",
                                    style = MaterialTheme.typography.titleLarge,
                                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                                )
                            }
                            Spacer(Modifier.height(20.dp))
                        },
                    ) {
                        Section.entries.forEach { entry ->
                            NavigationRailItem(
                                selected = section == entry.key,
                                onClick = { section = entry.key },
                                icon = { Icon(entry.icon, contentDescription = entry.label) },
                                label = { Text(entry.label) },
                            )
                        }
                    }
                    Box(
                        Modifier
                            .width(1.dp)
                            .fillMaxHeight()
                            .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f)),
                    )
                }

                Box(Modifier.weight(1f)) {
                    // Keep each section's state (scroll position, settings tab,
                    // search query) across switches without keeping its
                    // composition alive.
                    stateHolder.SaveableStateProvider(section) {
                        when (section) {
                            Section.Sessions.key -> SessionsScreen(
                                container = container,
                                viewModel = sessions,
                                onOpenSession = { id ->
                                    chat.openSession(id)
                                    section = Section.Chat.key
                                },
                                onNewSession = {
                                    chat.startNew()
                                    section = Section.Chat.key
                                },
                            )
                            Section.Settings.key -> SettingsScreen(
                                container = container,
                                onWorkspaceChanged = { section = Section.Chat.key },
                            )
                            else -> ChatScreen(
                                container = container,
                                viewModel = chat,
                                onOpenSessions = { section = Section.Sessions.key },
                            )
                        }
                    }
                }
            }
        }
    }

    pending?.let { approval ->
        ApprovalDialog(approval) { approved -> container.permissions.answer(approved) }
    }
}

@Composable
private fun NativeErrorScreen(message: String) {
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
    ) {
        Icon(
            Icons.Filled.Warning,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.error,
            modifier = Modifier.size(48.dp),
        )
        Text("The agent runtime could not start", style = MaterialTheme.typography.titleMedium)
        Text(
            message,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        Text(
            "This build supports arm64-v8a devices. Reinstall the app if the problem persists.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}
