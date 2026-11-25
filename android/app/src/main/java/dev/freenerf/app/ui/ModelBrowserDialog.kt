package dev.freenerf.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.freenerf.app.AppContainer
import dev.freenerf.app.data.AppSettings
import org.json.JSONObject

/**
 * Full catalog browser: one tab per provider, every model the provider
 * exposes (not only tool-capable ones), plus search and refresh.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ModelBrowserDialog(
    container: AppContainer,
    initialProvider: String,
    onDismiss: () -> Unit,
    onPick: (provider: String, model: String) -> Unit,
) {
    var index by remember {
        mutableIntStateOf(AppSettings.PROVIDERS.indexOf(initialProvider).coerceAtLeast(0))
    }
    val provider = AppSettings.PROVIDERS[index]
    var query by remember { mutableStateOf("") }
    var models by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(provider, reload) {
        loading = true
        error = null
        models = emptyList()
        val result = runCatching { container.fetchModels(provider, "", reload > 0) }
        result.onSuccess {
            models = it
            loading = false
        }.onFailure {
            error = it.message ?: "Could not load models"
            loading = false
        }
    }

    val filtered = remember(models, query) {
        if (query.isBlank()) {
            models
        } else {
            val needle = query.lowercase()
            models.filter { model ->
                model.optString("id").lowercase().contains(needle) ||
                    model.optString("name").lowercase().contains(needle)
            }
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Models") },
        text = {
            Column(Modifier.fillMaxWidth().height(480.dp)) {
                TabRow(selectedTabIndex = index) {
                    AppSettings.PROVIDERS.forEachIndexed { position, id ->
                        Tab(
                            selected = index == position,
                            onClick = { index = position },
                            text = {
                                Text(
                                    AppSettings.PROVIDER_SHORT[id] ?: id,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            },
                        )
                    }
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(top = 8.dp),
                ) {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        modifier = Modifier.weight(1f),
                        placeholder = { Text("Filter models") },
                        singleLine = true,
                        leadingIcon = { Icon(Icons.Filled.Search, null) },
                    )
                    IconButton(onClick = { reload++ }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Refresh catalog")
                    }
                }
                Spacer(Modifier.height(4.dp))
                when {
                    loading -> Column(
                        Modifier.fillMaxWidth().padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        CircularProgressIndicator()
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Loading ${AppSettings.PROVIDER_LABELS[provider] ?: provider} models…",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    error != null -> Column(Modifier.padding(16.dp)) {
                        Text(
                            "Could not load models: $error",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "Check the API key and base URL for this provider.",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    filtered.isEmpty() -> Box(
                        Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            "No models matched.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    else -> LazyColumn(Modifier.fillMaxSize()) {
                        item {
                            Text(
                                "${filtered.size} model${if (filtered.size == 1) "" else "s"}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(vertical = 4.dp),
                            )
                        }
                        items(filtered, key = { it.optString("id") }) { model ->
                            val id = model.optString("id")
                            val name = model.optString("name")
                            val context = model.optLong("context_length", 0)
                            val selected = container.settings.value.modelFor(provider) == id
                            ListItem(
                                headlineContent = {
                                    Text(
                                        id,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                        fontFamily = FontFamily.Monospace,
                                        style = MaterialTheme.typography.bodyMedium,
                                    )
                                },
                                supportingContent = {
                                    Text(
                                        buildString {
                                            if (name.isNotBlank() && name != id) append(name)
                                            if (context > 0) {
                                                if (isNotEmpty()) append(" · ")
                                                append("${context / 1000}k ctx")
                                            }
                                            if (model.optBoolean("supports_tools", false)) {
                                                if (isNotEmpty()) append(" · ")
                                                append("tools")
                                            }
                                        },
                                        style = MaterialTheme.typography.bodySmall,
                                    )
                                },
                                trailingContent = if (selected) {
                                    {
                                        Text(
                                            "current",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.primary,
                                        )
                                    }
                                } else {
                                    null
                                },
                                modifier = Modifier.clickable { onPick(provider, id) },
                            )
                            HorizontalDivider()
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}
