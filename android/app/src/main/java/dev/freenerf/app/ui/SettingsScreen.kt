package dev.freenerf.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.VpnKey
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PrimaryScrollableTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.freenerf.app.AppContainer
import dev.freenerf.app.BuildConfig
import dev.freenerf.app.data.AppSettings
import dev.freenerf.app.storage.StorageAccess
import dev.freenerf.app.ui.components.FileBrowserDialog
import dev.freenerf.app.ui.theme.parseHexColor
import dev.freenerf.app.ui.theme.themeAccentColor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

private data class SettingsCategory(
    val label: String,
    val summary: String,
    val icon: ImageVector,
)

/**
 * The settings centre, same nine sections the app always had. On a phone the
 * sections are a chip row above the page; from tablet width they become the
 * sidebar the web Theme Studio uses, with the page beside it.
 */
private val SETTINGS_CATEGORIES = listOf(
    SettingsCategory("General", "Temperature, tokens, system prompt", Icons.Filled.Tune),
    SettingsCategory("Appearance", "Theme Studio, palette, contrast", Icons.Filled.Palette),
    SettingsCategory("Providers", "Endpoint, key and model per provider", Icons.Filled.Cloud),
    SettingsCategory("Models", "Browse every model", Icons.Filled.Search),
    SettingsCategory("API keys", "Encrypted keys in the Keystore", Icons.Filled.VpnKey),
    SettingsCategory("Permissions", "What the agent may do", Icons.Filled.Lock),
    SettingsCategory("Workspace", "Folder, storage and imports", Icons.Filled.FolderOpen),
    SettingsCategory("SSH", "Remote host and credentials", Icons.Filled.Terminal),
    SettingsCategory("About", "Version, tools and privacy", Icons.Filled.Info),
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    container: AppContainer,
    onWorkspaceChanged: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val settings by container.settings.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var tab by rememberSaveable { mutableStateOf(0) }

    fun notify(message: String) {
        scope.launch { snackbar.showSnackbar(message) }
    }

    fun apply(updated: AppSettings, message: String? = null) {
        container.applySettings(updated)
        if (message != null) notify(message)
    }

    BoxWithConstraints(modifier.fillMaxSize()) {
        val wide = maxWidth >= 840.dp
        Scaffold(
            containerColor = MaterialTheme.colorScheme.surface,
            // The navigation bar is owned by the app shell, so no bottom insets here.
            contentWindowInsets = WindowInsets(0, 0, 0, 0),
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text("Settings", style = MaterialTheme.typography.headlineSmall)
                            Text(
                                SETTINGS_CATEGORIES[tab].summary,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    },
                )
            },
            snackbarHost = { SnackbarHost(snackbar) },
        ) { padding ->
            Row(Modifier.fillMaxSize().padding(padding)) {
                if (wide) {
                    SettingsIndex(selected = tab, onSelect = { tab = it })
                    Box(
                        Modifier
                            .width(1.dp)
                            .fillMaxHeight()
                            .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f)),
                    )
                }
                Column(Modifier.weight(1f).fillMaxHeight()) {
                    if (!wide) {
                        // Primary tabs directly under the app bar, the way the web
                        // settings centre arranges them. Scrollable so all nine
                        // categories stay reachable on a phone.
                        PrimaryScrollableTabRow(
                            selectedTabIndex = tab,
                            edgePadding = 12.dp,
                            divider = {},
                        ) {
                            SETTINGS_CATEGORIES.forEachIndexed { index, category ->
                                Tab(
                                    selected = tab == index,
                                    onClick = { tab = index },
                                    text = {
                                        Text(
                                            category.label,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                        )
                                    },
                                )
                            }
                        }
                    }
                    when (tab) {
                        0 -> GeneralTab(settings, ::apply)
                        1 -> AppearanceTab(settings, ::apply)
                        2 -> ProvidersTab(container, settings, ::apply, ::notify)
                        3 -> ModelsTab(container, settings, ::apply, ::notify)
                        4 -> KeysTab(container, settings, ::notify)
                        5 -> PermissionsTab(settings, ::apply)
                        6 -> WorkspaceTab(
                            container = container,
                            settings = settings,
                            notify = ::notify,
                            onWorkspaceChanged = onWorkspaceChanged,
                            apply = ::apply,
                        )
                        7 -> SshTab(container, settings, ::apply, ::notify)
                        else -> AboutTab(container)
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsIndex(selected: Int, onSelect: (Int) -> Unit) {
    LazyColumn(
        modifier = Modifier.width(300.dp).fillMaxHeight(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        items(SETTINGS_CATEGORIES.size) { index ->
            val category = SETTINGS_CATEGORIES[index]
            val active = selected == index
            val shape = MaterialTheme.shapes.large
            Surface(
                modifier = Modifier.fillMaxWidth().clip(shape).clickable { onSelect(index) },
                shape = shape,
                color = if (active) {
                    MaterialTheme.colorScheme.secondaryContainer
                } else {
                    Color.Transparent
                },
            ) {
                Row(
                    Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Icon(
                        category.icon,
                        contentDescription = null,
                        tint = if (active) {
                            MaterialTheme.colorScheme.onSecondaryContainer
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                    Column(Modifier.weight(1f)) {
                        Text(category.label, style = MaterialTheme.typography.titleSmall)
                        Text(
                            category.summary,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
    }
}

// ----------------------------------------------------------------------
// Shared building blocks
// ----------------------------------------------------------------------

@Composable
private fun SettingCard(
    title: String? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainerLow,
    ) {
        Column(
            Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (title != null) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            content()
        }
    }
}

@Composable
private fun SwitchRow(
    title: String,
    description: String? = null,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            if (description != null) {
                Text(
                    description,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            value,
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
private fun ScrollPage(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        content = content,
    )
}

// ----------------------------------------------------------------------
// General
// ----------------------------------------------------------------------

@Composable
private fun GeneralTab(settings: AppSettings, apply: (AppSettings, String?) -> Unit) {
    var temperature by remember(settings.temperature) { mutableFloatStateOf(settings.temperature) }
    var maxTokens by remember(settings.maxTokens) { mutableStateOf(settings.maxTokens.toString()) }
    var maxSteps by remember(settings.maxSteps) { mutableStateOf(settings.maxSteps.toString()) }
    var prompt by remember(settings.customPrompt) { mutableStateOf(settings.customPrompt) }

    ScrollPage {
        SettingCard("Generation") {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Temperature", style = MaterialTheme.typography.bodyLarge)
                Text(
                    "%.2f".format(temperature),
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Slider(
                value = temperature,
                onValueChange = { temperature = it },
                valueRange = 0f..2f,
                steps = 19,
                onValueChangeFinished = {
                    apply(settings.copy(temperature = temperature), null)
                },
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = maxTokens,
                    onValueChange = { maxTokens = it.filter(Char::isDigit) },
                    modifier = Modifier.weight(1f),
                    label = { Text("Max tokens") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                OutlinedTextField(
                    value = maxSteps,
                    onValueChange = { maxSteps = it.filter(Char::isDigit) },
                    modifier = Modifier.weight(1f),
                    label = { Text("Max steps") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
            }
            Button(
                onClick = {
                    apply(
                        settings.copy(
                            maxTokens = maxTokens.toIntOrNull()?.coerceIn(1, 32768)
                                ?: settings.maxTokens,
                            maxSteps = maxSteps.toIntOrNull()?.coerceIn(1, 100)
                                ?: settings.maxSteps,
                            customPrompt = prompt,
                        ),
                        "Generation settings saved",
                    )
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Save") }
        }

        SettingCard("Behaviour") {
            SwitchRow(
                title = "Stream responses",
                description = "Show tokens as they arrive. Gemini replies arrive complete.",
                checked = settings.streaming,
                onCheckedChange = { apply(settings.copy(streaming = it), null) },
            )
            HorizontalDivider()
            OutlinedTextField(
                value = prompt,
                onValueChange = { prompt = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Extra system instructions") },
                minLines = 3,
                maxLines = 8,
            )
        }

        SettingCard("Session") {
            InfoRow("Provider", AppSettings.PROVIDER_LABELS[settings.provider] ?: settings.provider)
            InfoRow("Model", settings.modelFor())
            InfoRow("Permission mode", AppSettings.permissionLabel(settings.permissionMode))
        }
    }
}

// ----------------------------------------------------------------------
// Appearance / Theme Studio
// ----------------------------------------------------------------------

@Composable
private fun AppearanceTab(settings: AppSettings, apply: (AppSettings, String?) -> Unit) {
    ScrollPage {
        SettingCard("Theme Studio") {
            Text(
                "The app follows Material You. Pick a mode, a brand accent, or let " +
                    "Android derive the palette from your wallpaper.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AppSettings.THEME_MODES.forEach { mode ->
                    FilterChip(
                        selected = settings.themeMode == mode,
                        onClick = { apply(settings.copy(themeMode = mode), null) },
                        label = {
                            Text(mode.replaceFirstChar { it.uppercase() })
                        },
                    )
                }
            }
            HorizontalDivider()
            SwitchRow(
                title = "Dynamic colour",
                description = "Use the Android 12+ wallpaper palette.",
                checked = settings.dynamicColor,
                onCheckedChange = { apply(settings.copy(dynamicColor = it), null) },
            )
            SwitchRow(
                title = "Pure black surfaces",
                description = "AMOLED-friendly background. Disables dynamic colour.",
                checked = settings.amoledBlack,
                onCheckedChange = { apply(settings.copy(amoledBlack = it), null) },
            )
            HorizontalDivider()
            Text(
                "Contrast",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AppSettings.CONTRAST_LEVELS.forEach { level ->
                    FilterChip(
                        selected = settings.themeContrast == level,
                        onClick = { apply(settings.copy(themeContrast = level), null) },
                        label = { Text(AppSettings.contrastLabel(level)) },
                    )
                }
            }
            Text(
                "Stretches the generated palette away from the mid tones: higher " +
                    "contrast darkens text and lightens containers.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            HorizontalDivider()
            Text(
                "Brand accent",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                AppSettings.THEME_ACCENTS.forEach { accent ->
                    val selected = settings.themeAccent == accent
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.clickable {
                            apply(
                                settings.copy(
                                    themeAccent = accent,
                                    // A preset replaces a custom hex accent; the
                                    // custom colour wins in the theme lookup.
                                    customAccent = "",
                                    dynamicColor = false,
                                ),
                                null,
                            )
                        },
                    ) {
                        Box(
                            Modifier
                                .size(40.dp)
                                .background(themeAccentColor(accent), CircleShape)
                                .border(
                                    width = if (selected) 3.dp else 1.dp,
                                    color = if (selected) {
                                        MaterialTheme.colorScheme.onSurface
                                    } else {
                                        MaterialTheme.colorScheme.outlineVariant
                                    },
                                    shape = CircleShape,
                                ),
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            AppSettings.accentLabel(accent),
                            style = MaterialTheme.typography.labelSmall,
                            color = if (selected) {
                                MaterialTheme.colorScheme.primary
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            },
                        )
                    }
                }
            }
        }

        SettingCard("Custom accent") {
            Text(
                "Type any hex colour (#RRGGBB) and the whole palette is derived " +
                    "from it, like the CLI Theme Studio. The presets stay available.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            var hex by remember(settings.customAccent) { mutableStateOf(settings.customAccent) }
            val parsed = parseHexColor(hex)
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = hex,
                    onValueChange = { hex = it },
                    modifier = Modifier.weight(1f),
                    label = { Text("Hex colour") },
                    placeholder = { Text("#2E7D4F") },
                    singleLine = true,
                    isError = hex.isNotBlank() && parsed == null,
                    leadingIcon = {
                        Box(
                            Modifier
                                .size(20.dp)
                                .background(
                                    parsed ?: MaterialTheme.colorScheme.outlineVariant,
                                    CircleShape,
                                ),
                        )
                    },
                )
                Button(
                    enabled = parsed != null,
                    onClick = {
                        apply(
                            settings.copy(customAccent = hex.trim(), dynamicColor = false),
                            "Custom accent applied",
                        )
                    },
                ) { Text("Apply") }
            }
            if (hex.isNotBlank() && parsed == null) {
                Text(
                    "Use six hex digits, for example #2E7D4F.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            if (settings.customAccent.isNotBlank()) {
                TextButton(
                    onClick = {
                        hex = ""
                        apply(settings.copy(customAccent = ""), "Preset accent restored")
                    },
                ) { Text("Remove custom accent") }
            }
        }

        SettingCard("Preview") {
            Text(
                "Freenerf",
                style = MaterialTheme.typography.headlineSmall,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                "Assistant messages, tables and code blocks use these roles.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {}) { Text("Primary") }
                OutlinedButton(onClick = {}) { Text("Secondary") }
            }
            // The live roles this seed just produced, so a palette change is
            // visible without leaving the picker.
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf(
                    "primary" to MaterialTheme.colorScheme.primary,
                    "container" to MaterialTheme.colorScheme.primaryContainer,
                    "tertiary" to MaterialTheme.colorScheme.tertiary,
                    "surface" to MaterialTheme.colorScheme.surfaceContainerHighest,
                ).forEach { (label, color) ->
                    Column(Modifier.weight(1f)) {
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .height(44.dp)
                                .clip(MaterialTheme.shapes.medium)
                                .background(color),
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            label,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
    }
}

// ----------------------------------------------------------------------
// Providers
// ----------------------------------------------------------------------

@Composable
private fun ProvidersTab(
    container: AppContainer,
    settings: AppSettings,
    apply: (AppSettings, String?) -> Unit,
    notify: (String) -> Unit,
) {
    val provider = settings.provider
    var apiKey by remember(provider) { mutableStateOf("") }
    var model by remember(provider, settings.models) { mutableStateOf(settings.modelFor(provider)) }
    var baseUrl by remember(provider, settings.baseUrls) {
        mutableStateOf(settings.baseUrls[provider].orEmpty())
    }
    var browser by remember { mutableStateOf(false) }

    ScrollPage {
        SettingCard("Provider") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AppSettings.PROVIDERS.forEach { option ->
                    FilterChip(
                        selected = provider == option,
                        onClick = {
                            apply(settings.copy(provider = option), null)
                            apiKey = ""
                        },
                        label = { Text(AppSettings.PROVIDER_SHORT[option] ?: option) },
                    )
                }
            }
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    if (settings.hasKey(provider)) "API key configured" else "No API key yet",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (settings.hasKey(provider)) {
                        MaterialTheme.colorScheme.primary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
                if (settings.hasKey(provider)) {
                    TextButton(onClick = {
                        container.setApiKey(provider, "")
                        notify("API key removed")
                    }) { Text("Remove") }
                }
            }
            OutlinedTextField(
                value = apiKey,
                onValueChange = { apiKey = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("New API key") },
                placeholder = { Text("Paste a key, then Save key") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
            )
            Button(
                onClick = {
                    if (apiKey.isBlank()) {
                        notify("Enter a key first")
                    } else {
                        container.setApiKey(provider, apiKey.trim())
                        apiKey = ""
                        notify("API key saved")
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Save key") }
        }

        SettingCard("Model") {
            OutlinedTextField(
                value = model,
                onValueChange = { model = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Model id") },
                singleLine = true,
                trailingIcon = {
                    IconButton(onClick = { browser = true }) {
                        Icon(Icons.Filled.Search, contentDescription = "Browse models")
                    }
                },
            )
            TextButton(onClick = { browser = true }) { Text("Browse every model") }
        }

        SettingCard("Base URL") {
            OutlinedTextField(
                value = baseUrl,
                onValueChange = { baseUrl = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Custom base URL (optional)") },
                placeholder = { Text("Empty — uses ${settings.urlHint(provider)}") },
                singleLine = true,
            )
            Text(
                "Leave this empty to use the provider's official endpoint. " +
                    "Only fill it for a proxy or a local server.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(
                onClick = {
                    val nextModel = model.trim().ifBlank {
                        AppSettings.DEFAULT_MODELS[provider] ?: ""
                    }
                    val urls = if (baseUrl.isBlank()) {
                        settings.baseUrls - provider
                    } else {
                        settings.baseUrls + (provider to baseUrl.trim())
                    }
                    apply(
                        settings.copy(
                            models = settings.models + (provider to nextModel),
                            baseUrls = urls,
                        ),
                        "Provider settings saved",
                    )
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Save provider") }
        }
    }

    if (browser) {
        ModelBrowserDialog(
            container = container,
            initialProvider = provider,
            onDismiss = { browser = false },
            onPick = { pickedProvider, pickedModel ->
                apply(
                    settings.copy(
                        provider = pickedProvider,
                        models = settings.models + (pickedProvider to pickedModel),
                    ),
                    "Model: $pickedModel",
                )
                browser = false
                model = pickedModel
            },
        )
    }
}

// ----------------------------------------------------------------------
// Models (one tab per provider, full catalog)
// ----------------------------------------------------------------------

@Composable
private fun ModelsTab(
    container: AppContainer,
    settings: AppSettings,
    apply: (AppSettings, String?) -> Unit,
    notify: (String) -> Unit,
) {
    var index by rememberSaveable {
        mutableStateOf(AppSettings.PROVIDERS.indexOf(settings.provider).coerceAtLeast(0))
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
            models.filter {
                it.optString("id").lowercase().contains(needle) ||
                    it.optString("name").lowercase().contains(needle)
            }
        }
    }

    Column(Modifier.fillMaxSize()) {
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
            Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
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
        Text(
            "Current: ${settings.modelFor(provider)}",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
        Spacer(Modifier.height(6.dp))
        when {
            loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator()
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Loading every ${AppSettings.PROVIDER_LABELS[provider] ?: provider} model…",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            error != null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(
                    Modifier.padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(
                        "Could not load models",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        error ?: "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    TextButton(onClick = { reload++ }) { Text("Retry") }
                }
            }

            filtered.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    "No models matched.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(filtered, key = { it.optString("id") }) { model ->
                    val id = model.optString("id")
                    val name = model.optString("name")
                    val context = model.optLong("context_length", 0)
                    val selected = settings.modelFor(provider) == id
                    androidx.compose.material3.ListItem(
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
                        modifier = Modifier.clickable {
                            apply(
                                settings.copy(
                                    provider = provider,
                                    models = settings.models + (provider to id),
                                ),
                                "Model: $id",
                            )
                            notify("${AppSettings.PROVIDER_SHORT[provider] ?: provider} → $id")
                        },
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}

// ----------------------------------------------------------------------
// API keys
// ----------------------------------------------------------------------

@Composable
private fun KeysTab(
    container: AppContainer,
    settings: AppSettings,
    notify: (String) -> Unit,
) {
    var editing by remember { mutableStateOf<String?>(null) }
    var value by remember { mutableStateOf("") }
    var visible by remember { mutableStateOf(false) }

    ScrollPage {
        SettingCard("API keys") {
            Text(
                "Keys live in the Android Keystore, never in the workspace or the " +
                    "agent databases. The active provider needs a key before it can chat.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            AppSettings.PROVIDERS.forEach { provider ->
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            AppSettings.PROVIDER_LABELS[provider] ?: provider,
                            style = MaterialTheme.typography.bodyLarge,
                        )
                        Text(
                            if (settings.hasKey(provider)) "configured" else "not set",
                            style = MaterialTheme.typography.labelSmall,
                            color = if (settings.hasKey(provider)) {
                                MaterialTheme.colorScheme.primary
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            },
                        )
                    }
                    TextButton(
                        onClick = {
                            value = ""
                            visible = false
                            editing = provider
                        },
                    ) { Text(if (settings.hasKey(provider)) "Replace" else "Set") }
                    if (settings.hasKey(provider)) {
                        TextButton(
                            onClick = {
                                container.setApiKey(provider, "")
                                notify(
                                    "${AppSettings.PROVIDER_SHORT[provider] ?: provider} key removed",
                                )
                            },
                        ) { Text("Clear") }
                    }
                }
                HorizontalDivider()
            }
        }

        SettingCard("In use") {
            InfoRow(
                "Active provider",
                AppSettings.PROVIDER_LABELS[settings.provider] ?: settings.provider,
            )
            InfoRow("Model", settings.modelFor())
            InfoRow(
                "Base URL",
                settings.baseUrls[settings.provider]?.takeIf { it.isNotBlank() }
                    ?: "provider default",
            )
        }
    }

    editing?.let { provider ->
        AlertDialog(
            onDismissRequest = { editing = null },
            title = { Text("${AppSettings.PROVIDER_LABELS[provider] ?: provider} API key") },
            text = {
                OutlinedTextField(
                    value = value,
                    onValueChange = { value = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    label = { Text("API key") },
                    visualTransformation = if (visible) {
                        VisualTransformation.None
                    } else {
                        PasswordVisualTransformation()
                    },
                    trailingIcon = {
                        IconButton(onClick = { visible = !visible }) {
                            Icon(
                                if (visible) {
                                    Icons.Filled.VisibilityOff
                                } else {
                                    Icons.Filled.Visibility
                                },
                                contentDescription = "Toggle visibility",
                            )
                        }
                    },
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val trimmed = value.trim()
                        if (trimmed.isEmpty()) {
                            notify("Enter a key first")
                        } else {
                            container.setApiKey(provider, trimmed)
                            notify(
                                "${AppSettings.PROVIDER_SHORT[provider] ?: provider} key saved",
                            )
                        }
                        editing = null
                    },
                ) { Text("Save") }
            },
            dismissButton = {
                TextButton(onClick = { editing = null }) { Text("Cancel") }
            },
        )
    }
}

// ----------------------------------------------------------------------
// Permissions
// ----------------------------------------------------------------------

@Composable
private fun PermissionsTab(settings: AppSettings, apply: (AppSettings, String?) -> Unit) {
    ScrollPage {
        SettingCard("Permission mode") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AppSettings.PERMISSION_MODES.forEach { mode ->
                    FilterChip(
                        selected = settings.permissionMode == mode,
                        onClick = { apply(settings.copy(permissionMode = mode), null) },
                        label = { Text(AppSettings.permissionLabel(mode)) },
                    )
                }
            }
            Text(
                when (settings.permissionMode) {
                    "denied" -> "Read-only: writes, shell, SSH and network tools are refused."
                    "full" -> "Everything runs without asking. Use only in trusted workspaces."
                    else -> "Reads run freely; writes, shell and SSH pause for approval."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        SettingCard("Hard blocks") {
            Text(
                "In every mode the runtime refuses recursive deletion of /, ~, ., *, " +
                    ".git or .freenerf, disk formatting, shutdown, fork bombs and piping " +
                    "remote content into a shell.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// ----------------------------------------------------------------------
// Workspace
// ----------------------------------------------------------------------

@Composable
private fun WorkspaceTab(
    container: AppContainer,
    settings: AppSettings,
    notify: (String) -> Unit,
    onWorkspaceChanged: () -> Unit,
    apply: (AppSettings, String?) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var showBrowser by remember { mutableStateOf(false) }
    var storageGranted by remember { mutableStateOf(container.hasStorageAccess()) }

    // Granting All files access happens in the system settings app; re-read the
    // real state when the user comes back instead of showing a stale value.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                storageGranted = container.hasStorageAccess()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val importFolder = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri ->
        if (uri != null) {
            scope.launch {
                val result = withContext(Dispatchers.IO) { container.mirror.importTree(uri) }
                notify(
                    result.fold(
                        { "Imported $it file(s) into the workspace" },
                        { "Import failed: ${it.message}" },
                    ),
                )
            }
        }
    }
    val exportFolder = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocumentTree(),
    ) { uri ->
        if (uri != null) {
            scope.launch {
                val result = withContext(Dispatchers.IO) { container.mirror.exportTree(uri) }
                notify(
                    result.fold(
                        { "Exported $it file(s)" },
                        { "Export failed: ${it.message}" },
                    ),
                )
            }
        }
    }
    val importConfig = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri != null) {
            scope.launch {
                val text = withContext(Dispatchers.IO) { container.mirror.readText(uri) }
                    .getOrElse { error ->
                        notify("Could not read config: ${error.message}")
                        return@launch
                    }
                container.importLegacyConfig(text, settings).fold(
                    { notify("Imported legacy configuration and keys") },
                    { notify("Config import failed: ${it.message}") },
                )
            }
        }
    }
    val importDatabases = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris ->
        if (uris.isNotEmpty()) {
            scope.launch {
                val result = container.importLegacyDatabasesFromUris(uris)
                notify(
                    if (result != null) {
                        "Imported legacy databases: ${result.optString("imported")}"
                    } else {
                        "Legacy database import failed"
                    },
                )
            }
        }
    }

    ScrollPage {
        SettingCard("Current workspace") {
            Text(
                container.workspace.absolutePath,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
            )
            Text(
                "The agent may read and write only inside this folder. Pick any folder " +
                    "under /sdcard to work on real device files.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { showBrowser = true }) {
                    Icon(Icons.Filled.FolderOpen, contentDescription = null)
                    Spacer(Modifier.size(6.dp))
                    Text("Browse storage")
                }
                OutlinedButton(
                    onClick = {
                        container.setWorkspace("").fold(
                            {
                                notify("Workspace: ${it.absolutePath}")
                                onWorkspaceChanged()
                            },
                            { notify("Could not use the private folder: ${it.message}") },
                        )
                    },
                ) { Text("App-private") }
            }
        }

        SettingCard("Storage permission") {
            InfoRow(
                "All files access",
                if (storageGranted) "granted" else "not granted",
            )
            Text(
                "Needed to use folders in /sdcard as the workspace. Android 11+ " +
                    "uses All files access; older versions use the storage permission.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(
                onClick = {
                    val intent = StorageAccess.requestIntent(context)
                    if (intent != null) {
                        runCatching { context.startActivity(intent) }
                            .onFailure { notify("Open Settings → Apps → Freenerf to grant access") }
                    } else {
                        notify("Grant storage permission from the device settings")
                    }
                    storageGranted = container.hasStorageAccess()
                },
            ) { Text("Grant file access") }
        }

        SettingCard("Import / export") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { importFolder.launch(null) }) {
                    Text("Import folder")
                }
                OutlinedButton(onClick = { exportFolder.launch(null) }) {
                    Text("Export folder")
                }
            }
            Text(
                "Copies a folder into or out of the current workspace. Handy for " +
                    "app-private workspaces.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        SettingCard("Legacy data") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { importConfig.launch(arrayOf("*/*")) }) {
                    Text("Import config")
                }
                OutlinedButton(onClick = { importDatabases.launch(arrayOf("*/*")) }) {
                    Text("Import databases")
                }
            }
            Text(
                "Imports the terminal app's config.json and session/task databases. " +
                    "Existing data is backed up first.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        SettingCard("Data directory") {
            InfoRow("Workspace", File(container.workspace.absolutePath).name)
            InfoRow("Stored sessions", "inside .freenerf/")
        }
    }

    if (showBrowser) {
        FileBrowserDialog(
            onPickFolder = { folder ->
                showBrowser = false
                container.setWorkspace(folder.absolutePath).fold(
                    {
                        notify("Workspace: ${it.name}")
                        onWorkspaceChanged()
                    },
                    { notify("Cannot use this folder: ${it.message}") },
                )
            },
            onPickFile = { file ->
                showBrowser = false
                val parent = file.parentFile ?: file
                container.setWorkspace(parent.absolutePath).fold(
                    {
                        notify("Workspace: ${parent.name} (${file.name} selected)")
                        onWorkspaceChanged()
                    },
                    { notify("Cannot use ${parent.absolutePath}: ${it.message}") },
                )
            },
            onDismiss = { showBrowser = false },
            startDirectory = StorageAccess.roots(context)
                .firstOrNull { it.isDirectory && it.absolutePath.startsWith("/storage") },
        )
    }
}

// ----------------------------------------------------------------------
// SSH
// ----------------------------------------------------------------------

@Composable
private fun SshTab(
    container: AppContainer,
    settings: AppSettings,
    apply: (AppSettings, String?) -> Unit,
    notify: (String) -> Unit,
) {
    var host by remember(settings.sshHost) { mutableStateOf(settings.sshHost) }
    var port by remember(settings.sshPort) { mutableStateOf(settings.sshPort.toString()) }
    var user by remember(settings.sshUser) { mutableStateOf(settings.sshUser) }
    var remoteDir by remember(settings.sshRemoteDir) { mutableStateOf(settings.sshRemoteDir) }
    var password by remember { mutableStateOf("") }
    var privateKey by remember { mutableStateOf("") }
    var passphrase by remember { mutableStateOf("") }
    var showSecrets by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf<String?>(null) }
    var testing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    fun saveSsh() {
        val updated = settings.copy(
            sshHost = host.trim(),
            sshPort = port.toIntOrNull()?.coerceIn(1, 65535) ?: 22,
            sshUser = user.trim(),
            sshRemoteDir = remoteDir.trim(),
        )
        apply(updated, null)
        container.saveSshSecrets(
            password = password.ifBlank { settings.sshPassword },
            key = privateKey.ifBlank { settings.sshKey },
            passphrase = passphrase.ifBlank { settings.sshPassphrase },
        )
        password = ""
        privateKey = ""
        passphrase = ""
    }

    ScrollPage {
        SettingCard("Remote host") {
            SwitchRow(
                title = "Use SSH",
                description = "Adds ssh_exec, ssh_read_file, ssh_write_file and " +
                    "ssh_list_files tools to the agent.",
                checked = settings.sshEnabled,
                onCheckedChange = {
                    apply(settings.copy(sshEnabled = it), null)
                },
            )
            HorizontalDivider()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = host,
                    onValueChange = { host = it },
                    modifier = Modifier.weight(2f),
                    label = { Text("Host") },
                    placeholder = { Text("192.168.1.10") },
                    singleLine = true,
                )
                OutlinedTextField(
                    value = port,
                    onValueChange = { port = it.filter(Char::isDigit) },
                    modifier = Modifier.weight(1f),
                    label = { Text("Port") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
            }
            OutlinedTextField(
                value = user,
                onValueChange = { user = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("User") },
                placeholder = { Text("u0_a123") },
                singleLine = true,
            )
            OutlinedTextField(
                value = remoteDir,
                onValueChange = { remoteDir = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Remote workspace (optional)") },
                placeholder = { Text("/sdcard/project") },
                singleLine = true,
            )
        }

        SettingCard("Authentication") {
            Text(
                if (settings.sshPassword.isNotBlank() || settings.sshKey.isNotBlank()) {
                    "Credentials are stored encrypted in the Android Keystore."
                } else {
                    "Use a password or paste a private key. Credentials never leave the device."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Password") },
                placeholder = {
                    Text(
                        if (settings.sshPassword.isNotBlank()) "Saved — type to replace"
                        else "SSH password",
                    )
                },
                singleLine = true,
                visualTransformation = if (showSecrets) {
                    VisualTransformation.None
                } else {
                    PasswordVisualTransformation()
                },
                trailingIcon = {
                    IconButton(onClick = { showSecrets = !showSecrets }) {
                        Icon(
                            if (showSecrets) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                            contentDescription = "Toggle visibility",
                        )
                    }
                },
            )
            OutlinedTextField(
                value = privateKey,
                onValueChange = { privateKey = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Private key (PEM, optional)") },
                placeholder = {
                    Text(
                        if (settings.sshKey.isNotBlank()) "Saved — paste to replace"
                        else "-----BEGIN OPENSSH PRIVATE KEY-----",
                    )
                },
                minLines = 3,
                maxLines = 6,
            )
            OutlinedTextField(
                value = passphrase,
                onValueChange = { passphrase = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Key passphrase (optional)") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
            )
        }

        SettingCard("Connection") {
            InfoRow("Target", if (host.isBlank()) "not configured" else "$user@$host:$port")
            InfoRow("Remote dir", remoteDir.ifBlank { "home" })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        saveSsh()
                        notify("SSH settings saved")
                    },
                ) { Text("Save") }
                OutlinedButton(
                    enabled = !testing,
                    onClick = {
                        saveSsh()
                        testing = true
                        status = "Connecting…"
                        scope.launch {
                            status = container.testSsh()
                            testing = false
                        }
                    },
                ) { Text(if (testing) "Testing…" else "Test connection") }
                if (settings.sshPassword.isNotBlank() || settings.sshKey.isNotBlank()) {
                    TextButton(
                        onClick = {
                            container.saveSshSecrets("", "", "")
                            notify("SSH credentials removed")
                        },
                    ) { Text("Clear") }
                }
            }
            status?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    color = if (it.startsWith("Connected")) {
                        MaterialTheme.colorScheme.primary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    maxLines = 8,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }

        SettingCard("Agent tools") {
            Text(
                "With SSH enabled the agent can run any command on the remote host, " +
                    "read and write remote files, and list remote directories. Every " +
                    "ssh_exec call still goes through the permission policy, so it " +
                    "asks for approval unless you chose Full access.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// ----------------------------------------------------------------------
// About
// ----------------------------------------------------------------------

@Composable
private fun AboutTab(container: AppContainer) {
    var info by remember { mutableStateOf<JSONObject?>(null) }

    LaunchedEffect(Unit) { info = container.appInfo() }

    ScrollPage {
        SettingCard("Freenerf") {
            Text(
                "Version ${BuildConfig.VERSION_NAME}",
                style = MaterialTheme.typography.titleMedium,
            )
            Text(
                buildString {
                    append("Rust agent core")
                    info?.optString("version")?.takeIf { it.isNotBlank() }?.let { append(" $it") }
                    info?.optInt("tool_count", 0)?.takeIf { it > 0 }?.let {
                        append(" · $it tools")
                    }
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            HorizontalDivider()
            Text(
                "Providers",
                style = MaterialTheme.typography.labelLarge,
            )
            AppSettings.PROVIDERS.forEach { provider ->
                InfoRow(
                    AppSettings.PROVIDER_LABELS[provider] ?: provider,
                    settingsModel(info, provider),
                )
            }
        }
        SettingCard("Privacy") {
            Text(
                "API keys and SSH credentials are encrypted with a key held by the " +
                    "Android Keystore. They are never written to the session database " +
                    "or logs.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun settingsModel(info: JSONObject?, provider: String): String {
    val providers = info?.optJSONArray("providers") ?: return "—"
    for (index in 0 until providers.length()) {
        val item = providers.optJSONObject(index) ?: continue
        if (item.optString("kind") == provider || item.optString("id") == provider) {
            return item.optString("default_model").ifBlank { "—" }
        }
    }
    return "—"
}
