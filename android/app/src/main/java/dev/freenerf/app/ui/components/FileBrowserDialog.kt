package dev.freenerf.app.ui.components

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.freenerf.app.storage.StorageAccess
import java.io.File

/**
 * Filesystem browser for /sdcard (and app storage). Folders select a new
 * workspace; picking a file uses its parent folder and returns the file.
 */
@Composable
fun FileBrowserDialog(
    onPickFolder: (File) -> Unit,
    onPickFile: (File) -> Unit,
    onDismiss: () -> Unit,
    startDirectory: File? = null,
) {
    val context = LocalContext.current
    val roots = remember { StorageAccess.roots(context) }
    var directory by remember {
        mutableStateOf(
            startDirectory?.takeIf { it.isDirectory }
                ?: roots.firstOrNull { it.isDirectory }
                ?: context.filesDir,
        )
    }
    var entries by remember { mutableStateOf<List<File>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var rootsMenu by remember { mutableStateOf(false) }
    var granted by remember { mutableStateOf(StorageAccess.hasAccess(context)) }

    val permission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted = it }

    fun refresh(target: File) {
        directory = target
        val children = target.listFiles()
        entries = children?.toList().orEmpty()
            .sortedWith(compareByDescending<File> { it.isDirectory }.thenBy { it.name.lowercase() })
        error = if (children == null) {
            "Cannot read this folder. Grant file access first."
        } else if (!StorageAccess.hasAccess(context) && !target.absolutePath.startsWith(
                context.filesDir.absolutePath,
            )
        ) {
            "Limited access: grant All files access to browse shared storage."
        } else {
            null
        }
    }

    LaunchedEffect(directory) { refresh(directory) }

    // The grant happens outside the app; refresh both the flag and the listing
    // when the user returns so /sdcard appears without reopening the dialog.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                granted = StorageAccess.hasAccess(context)
                refresh(directory)
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Choose workspace", modifier = Modifier.weight(1f))
                Box {
                    IconButton(onClick = { rootsMenu = true }) {
                        Icon(Icons.Filled.Storage, contentDescription = "Storage roots")
                    }
                    DropdownMenu(expanded = rootsMenu, onDismissRequest = { rootsMenu = false }) {
                        roots.forEach { root ->
                            DropdownMenuItem(
                                text = { Text(root.absolutePath, fontFamily = FontFamily.Monospace) },
                                onClick = {
                                    rootsMenu = false
                                    refresh(root)
                                },
                            )
                        }
                    }
                }
            }
        },
        text = {
            Column(Modifier.fillMaxWidth().height(460.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(
                        onClick = { directory.parentFile?.let { refresh(it) } },
                        enabled = directory.parentFile != null,
                    ) {
                        Icon(Icons.Filled.ArrowUpward, contentDescription = "Parent folder")
                    }
                    Text(
                        text = directory.absolutePath,
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                }

                if (!StorageAccess.hasAccess(context)) {
                    Surface(
                        color = MaterialTheme.colorScheme.secondaryContainer,
                        shape = MaterialTheme.shapes.medium,
                        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                    ) {
                        Column(Modifier.padding(12.dp)) {
                            Text(
                                "Storage permission is needed to see folders in /sdcard.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSecondaryContainer,
                            )
                            Spacer(Modifier.height(6.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                TextButton(
                                    onClick = {
                                        val intent = StorageAccess.requestIntent(context)
                                        if (intent != null) {
                                            runCatching { context.startActivity(intent) }
                                        } else {
                                            permission.launch(
                                                Manifest.permission.READ_EXTERNAL_STORAGE,
                                            )
                                        }
                                    },
                                ) { Text("Grant access") }
                                TextButton(
                                    onClick = { refresh(directory) },
                                ) { Text("Recheck") }
                            }
                        }
                    }
                }

                error?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(vertical = 4.dp),
                    )
                }

                if (entries.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            if (granted) "Folder is empty" else "No folders visible",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    LazyColumn(Modifier.fillMaxSize()) {
                        items(entries, key = { it.absolutePath }) { entry ->
                            ListItem(
                                headlineContent = {
                                    Text(
                                        entry.name,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                },
                                supportingContent = {
                                    Text(
                                        if (entry.isDirectory) "Folder" else formatSize(entry.length()),
                                        style = MaterialTheme.typography.labelSmall,
                                    )
                                },
                                leadingContent = {
                                    Icon(
                                        if (entry.isDirectory) {
                                            Icons.Filled.Folder
                                        } else {
                                            Icons.Filled.Description
                                        },
                                        contentDescription = null,
                                        tint = if (entry.isDirectory) {
                                            MaterialTheme.colorScheme.primary
                                        } else {
                                            MaterialTheme.colorScheme.onSurfaceVariant
                                        },
                                    )
                                },
                                modifier = Modifier.clickable {
                                    if (entry.isDirectory) refresh(entry) else onPickFile(entry)
                                },
                            )
                            HorizontalDivider()
                        }
                    }
                }
            }
        },
        confirmButton = {
            Button(onClick = { onPickFolder(directory) }) { Text("Use this folder") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

private fun formatSize(bytes: Long): String = when {
    bytes >= 1_048_576 -> "%.1f MB".format(bytes / 1_048_576.0)
    bytes >= 1024 -> "%.1f KB".format(bytes / 1024.0)
    else -> "$bytes B"
}
