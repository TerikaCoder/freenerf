"""Modal screens for the Freenerf interface."""

from __future__ import annotations

import os
import re
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    RichLog,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    TextArea,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from .widgets import ACCENT, BLUE, ERROR, MUTED, SUCCESS, WARNING
from ..agents.permissions import describe
from ..config import (
    AVAILABLE_THEMES,
    DEFAULT_CUSTOM_THEME,
    DEFAULT_MODELS,
    KEY_FIELDS,
    MODEL_FIELDS,
    PROVIDERS,
)
from ..llm.providers import PROVIDER_INFO
from ..workspace import Workspace


PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "gemini": "Gemini",
    "freellmapi": "FreeLLMAPI",
    "openai-compatible": "OpenAI-compatible",
}


class ConfirmScreen(ModalScreen[bool]):
    """Approval dialog for potentially destructive operations."""

    BINDINGS = [
        Binding("escape", "deny", "Deny", show=False),
        Binding("y", "approve", "Approve", show=False),
        Binding("n", "deny", "Deny", show=False),
    ]

    CSS = """
    ConfirmScreen {
        align: center middle;
    }

    #confirm-box {
        width: 90%;
        max-width: 72;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #confirm-title {
        color: $warning;
        text-style: bold;
        margin-bottom: 1;
    }

    #confirm-detail {
        max-height: 8;
        overflow-y: auto;
        margin-bottom: 1;
    }

    #confirm-note {
        max-height: 3;
        overflow-y: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    #confirm-buttons {
        height: 3;
        align-horizontal: right;
    }

    #confirm-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, title, detail, note="", approve_label="Approve"):
        super().__init__()
        self.title_text = str(title)
        self.detail_text = str(detail)
        self.note_text = str(note)
        self.approve_label = str(approve_label)

    def compose(self) -> ComposeResult:
        with Container(id="confirm-box"):
            yield Label(self.title_text, id="confirm-title")
            yield Static(Text(self.detail_text), id="confirm-detail")
            yield Static(
                Text(
                    self.note_text
                    or "Review the request before approving. Approval applies "
                    "only to this single operation.",
                ),
                id="confirm-note",
            )
            with Horizontal(id="confirm-buttons"):
                yield Button(
                    "Deny", variant="error", id="deny"
                )
                yield Button(
                    self.approve_label, variant="success", id="approve"
                )

    def on_mount(self):
        self.query_one("#deny", Button).focus()

    @on(Button.Pressed)
    def button_pressed(self, event):
        self.dismiss(event.button.id == "approve")

    def action_approve(self):
        self.dismiss(True)

    def action_deny(self):
        self.dismiss(False)


class ModelExplorerScreen(ModalScreen[dict | None]):
    """Searchable model catalog with capability indicators."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("f5", "refresh", "Refresh", show=False),
    ]

    CSS = """
    ModelExplorerScreen {
        align: center middle;
    }

    #model-box {
        width: 96%;
        max-width: 110;
        height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #model-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #provider-tabs {
        height: 3;
        margin-bottom: 1;
    }

    #provider-tabs Button {
        width: 1fr;
        margin-right: 1;
    }

    #model-search {
        margin-bottom: 1;
    }

    #model-summary {
        height: 1;
        color: $text-muted;
        margin-bottom: 1;
    }

    #model-options {
        height: 1fr;
        border: round $primary;
        margin-bottom: 1;
    }

    #model-details {
        height: auto;
        min-height: 5;
        max-height: 9;
        border: round $primary;
        padding: 0 1;
        margin-bottom: 1;
    }

    #model-buttons {
        height: 3;
        align-horizontal: right;
    }

    #model-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, models_by_provider, active_provider="openrouter", errors=None):
        super().__init__()
        self.models_by_provider = models_by_provider or {}
        self.provider = (
            active_provider if active_provider in PROVIDERS else "openrouter"
        )
        self.errors = list(errors or [])
        self.models: list[dict] = []
        self.filtered: list[dict] = []

    def compose(self) -> ComposeResult:
        with Container(id="model-box"):
            yield Label("MODEL EXPLORER", id="model-title")

            with Horizontal(id="provider-tabs"):
                for provider in PROVIDERS:
                    yield Button(
                        PROVIDER_LABELS.get(provider, provider),
                        id=f"provider-{provider}",
                        variant=(
                            "primary" if provider == self.provider else "default"
                        ),
                    )

            yield Input(
                placeholder="Search by model name or id...",
                id="model-search",
            )
            yield Static("", id="model-summary")
            yield OptionList(id="model-options")
            yield Static(
                "Highlight a model to see its capabilities.",
                id="model-details",
            )

            with Horizontal(id="model-buttons"):
                yield Button("Refresh", id="model-refresh")
                yield Button("Close", id="model-close")

    def on_mount(self):
        self.refresh_provider()

    # ------------------------------------------------------------------
    def refresh_provider(self):
        self.models = list(self.models_by_provider.get(self.provider) or [])

        for provider in PROVIDERS:
            try:
                button = self.query_one(f"#provider-{provider}", Button)
            except Exception:
                continue
            button.variant = (
                "primary" if provider == self.provider else "default"
            )

        self.refresh_models(self.query_one("#model-search", Input).value)

    def refresh_models(self, query=""):
        query = str(query or "").strip().lower()

        self.filtered = [
            model for model in self.models
            if not query
            or query in (
                str(model.get("id", "")) + " " + str(model.get("name", ""))
            ).lower()
        ]

        options = self.query_one("#model-options", OptionList)
        options.clear_options()

        for model in self.filtered[:200]:
            context = model.get("context_length") or 0
            tools = model.get("supports_tools")
            tool_label = (
                "tools ✓" if tools is True
                else "tools ✗" if tools is False
                else "tools ?"
            )

            label = Text()
            label.append(str(model.get("name") or model.get("id")), style="bold")
            label.append(f"\n  {model.get('id')}", style=f"dim {MUTED}")
            label.append(
                f"\n  {int(context):,} ctx · "
                f"{model.get('pricing_label', 'Unknown')} · {tool_label}",
                style=f"dim {MUTED}",
            )

            options.add_option(Option(label, id=str(model.get("id"))))

        summary = Text()
        summary.append(f"{len(self.filtered)} models", style=f"bold {ACCENT}")
        summary.append(f"   {PROVIDER_LABELS.get(self.provider, self.provider)}", style=MUTED)
        if self.errors:
            summary.append("   " + " | ".join(self.errors)[:120], style=WARNING)

        self.query_one("#model-summary", Static).update(summary)

        if self.filtered:
            options.highlighted = 0
            self._show_details(self.filtered[0])
        else:
            self.query_one("#model-details", Static).update(
                Text("No models available for this provider.", style=MUTED)
            )

    def _show_details(self, model):
        text = Text()
        text.append(str(model.get("name") or model.get("id")) + "\n", style="bold")
        text.append(f"{model.get('id')}\n", style=MUTED)
        text.append(f"Provider: {model.get('provider', self.provider)}\n", style="")
        text.append(
            f"Context: {int(model.get('context_length') or 0):,} tokens\n",
            style="",
        )
        text.append(f"Pricing: {model.get('pricing_label', 'Unknown')}\n", style="")

        tools = model.get("supports_tools")
        tool_text = (
            "yes" if tools is True else "no" if tools is False else "unknown"
        )
        text.append(f"Tool calling: {tool_text}\n", style="")

        description = str(model.get("description") or "").strip()
        if description:
            text.append(description[:240], style=MUTED)

        self.query_one("#model-details", Static).update(text)

    # ------------------------------------------------------------------
    @on(Button.Pressed, "#provider-tabs Button")
    def provider_pressed(self, event):
        provider = (event.button.id or "").removeprefix("provider-")
        if provider in PROVIDERS:
            self.provider = provider
            self.refresh_provider()

    @on(Input.Changed, "#model-search")
    def search_changed(self, event):
        self.refresh_models(event.value)

    @on(OptionList.OptionHighlighted, "#model-options")
    def highlighted(self, event):
        model = next(
            (item for item in self.filtered if item.get("id") == event.option.id),
            None,
        )
        if model:
            self._show_details(model)

    @on(OptionList.OptionSelected, "#model-options")
    def selected(self, event):
        model = next(
            (item for item in self.filtered if item.get("id") == event.option.id),
            None,
        )
        if model:
            self.dismiss({"provider": self.provider, "model": model})

    @on(Button.Pressed, "#model-refresh")
    def refresh_pressed(self):
        self.dismiss({"refresh": True})

    @on(Button.Pressed, "#model-close")
    def close_pressed(self):
        self.dismiss(None)

    def action_close(self):
        self.dismiss(None)

    def action_refresh(self):
        self.dismiss({"refresh": True})


class SettingsScreen(ModalScreen[dict | None]):
    """Grouped settings center, including workspace switching."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    CSS = """
    SettingsScreen {
        align: center middle;
    }

    #settings-box {
        width: 98%;
        max-width: 108;
        height: 96%;
        padding: 1 1;
        border: round $accent;
        background: $surface;
    }

    #settings-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #settings-tabs {
        height: 1fr;
    }

    TabPane VerticalScroll {
        height: 1fr;
        padding: 0 1;
    }

    .field-label {
        margin-top: 1;
        color: $text;
        text-style: bold;
    }

    .field-input, .field-select {
        margin-bottom: 1;
    }

    .field-note {
        color: $text-muted;
        margin-bottom: 1;
    }

    #settings-buttons {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }

    #settings-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, config, workspace_root=""):
        super().__init__()
        self.config = config
        self.workspace_root = str(workspace_root)

    def on_mount(self):
        self._sync_custom_font_input()

    def _sync_custom_font_input(self):
        custom = self.query_one("#settings-font-size", Select).value == "custom"
        self.query_one("#settings-custom-font-size", Input).disabled = not custom

    @on(Select.Changed, "#settings-font-size")
    def font_size_changed(self, event):
        self.query_one("#settings-custom-font-size", Input).disabled = (
            event.value != "custom"
        )

    @on(Select.Changed, "#settings-provider")
    def provider_changed(self, event):
        provider = str(event.value or "")
        model_field = MODEL_FIELDS.get(provider)
        if not model_field:
            return
        model = str(
            getattr(self.config, model_field, "")
            or DEFAULT_MODELS.get(provider, "")
        )
        try:
            self.query_one("#settings-current-model", Static).update(
                Text(f"{provider} · {model}", style=f"bold {BLUE}")
            )
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        config = self.config

        with Container(id="settings-box"):
            yield Label("SETTINGS CENTER", id="settings-title")

            with TabbedContent(initial="general", id="settings-tabs"):
                with TabPane("General", id="general"):
                    with VerticalScroll():
                        yield Label("MAX AGENT STEPS", classes="field-label")
                        yield Input(
                            value=str(config.max_agent_steps),
                            id="settings-max-steps",
                            classes="field-input",
                        )
                        yield Label("PROVIDER RETRIES", classes="field-label")
                        yield Input(
                            value=str(config.max_retries),
                            id="settings-max-retries",
                            classes="field-input",
                        )
                        yield Label("CONTEXT BUDGET (CHARACTERS)", classes="field-label")
                        yield Input(
                            value=str(config.max_context_chars),
                            id="settings-context",
                            classes="field-input",
                        )
                        yield Label("STREAM RESPONSES", classes="field-label")
                        yield Select(
                            [("Enabled", "true"), ("Disabled", "false")],
                            value="true" if config.streaming else "false",
                            allow_blank=False,
                            id="settings-streaming",
                            classes="field-select",
                        )
                        yield Label("TYPEWRITER EFFECT", classes="field-label")
                        yield Select(
                            [("Enabled", "true"), ("Disabled", "false")],
                            value="true" if config.typewriter_enabled else "false",
                            allow_blank=False,
                            id="settings-typewriter",
                            classes="field-select",
                        )
                        yield Label("TYPEWRITER SPEED (SECONDS)", classes="field-label")
                        yield Input(
                            value=str(config.typewriter_speed),
                            id="settings-speed",
                            classes="field-input",
                        )

                with TabPane("Custom Prompt", id="custom-prompt"):
                    with VerticalScroll():
                        yield Label(
                            "ADDITIONAL AI INSTRUCTIONS",
                            classes="field-label",
                        )
                        yield Static(
                            "These instructions are appended to the default "
                            "FreeNerf brain. They do not replace its built-in "
                            "identity, rules, or capabilities.",
                            classes="field-note",
                        )
                        yield TextArea(
                            config.custom_prompt,
                            id="settings-custom-prompt",
                            classes="field-input",
                        )
                        yield Static(
                            "Example: Prefer concise explanations, use Python "
                            "for data analysis, or explain commands before "
                            "running them.",
                            classes="field-note",
                        )

                with TabPane("Appearance", id="appearance"):
                    with VerticalScroll():
                        yield Label("THEME", classes="field-label")
                        yield Select(
                            [("FreeNerf · Signature", "freenerf"),
                             ("FreeNerf · Custom", "freenerf-custom")]
                            + [(name, name) for name in AVAILABLE_THEMES
                               if name not in ("freenerf", "freenerf-custom")],
                            value=config.theme,
                            allow_blank=False,
                            id="settings-theme",
                            classes="field-select",
                        )
                        yield Label("UI DENSITY", classes="field-label")
                        yield Select(
                            [
                                ("Compact", "compact"),
                                ("Normal", "normal"),
                                ("Large", "large"),
                            ],
                            value=config.ui_size,
                            allow_blank=False,
                            id="settings-ui-size",
                            classes="field-select",
                        )
                        yield Label("TERMINAL FONT SIZE", classes="field-label")
                        yield Select(
                            [
                                ("Small — 10", "small"),
                                ("Normal — 12", "normal"),
                                ("Large — 16", "large"),
                                ("Custom", "custom"),
                            ],
                            value=config.font_size,
                            allow_blank=False,
                            id="settings-font-size",
                            classes="field-select",
                        )
                        yield Label("CUSTOM FONT SIZE (6–32)", classes="field-label")
                        yield Input(
                            value=str(config.font_size_custom),
                            id="settings-custom-font-size",
                            classes="field-input",
                        )
                        yield Static(
                            "Font size changes apply to XFCE terminals. Termux "
                            "uses pinch-to-zoom, which the app cannot control.",
                            classes="field-note",
                        )

                with TabPane("Theme Studio", id="theme-studio"):
                    with VerticalScroll():
                        yield Static(
                            "CUSTOM PALETTE  ·  Enter #RRGGBB values. Changes are saved with Apply.",
                            classes="field-note",
                        )
                        for palette_key, palette_label in (
                            ("primary", "Primary / brand"),
                            ("secondary", "Secondary"),
                            ("accent", "Accent"),
                            ("foreground", "Main text"),
                            ("background", "Background"),
                            ("surface", "Surface"),
                            ("panel", "Panel"),
                            ("success", "Success"),
                            ("warning", "Warning"),
                            ("error", "Error"),
                        ):
                            yield Label(palette_label.upper(), classes="field-label")
                            yield Input(
                                value=str(config.custom_theme.get(palette_key, DEFAULT_CUSTOM_THEME[palette_key])),
                                placeholder=DEFAULT_CUSTOM_THEME[palette_key],
                                id=f"settings-color-{palette_key}",
                                classes="field-input",
                            )
                        yield Label("COLOR MODE", classes="field-label")
                        yield Select(
                            [("Dark", "true"), ("Light", "false")],
                            value="true" if config.custom_theme.get("dark", True) else "false",
                            allow_blank=False,
                            id="settings-color-mode",
                            classes="field-select",
                        )
                        yield Static(
                            "Choose FreeNerf · Custom in Appearance to activate this palette. "
                            "The signature theme remains available as a reset point.",
                            classes="field-note",
                        )

                with TabPane("Provider", id="provider"):
                    with VerticalScroll():
                        yield Label("ACTIVE PROVIDER", classes="field-label")
                        yield Select(
                            [(PROVIDER_LABELS.get(p, p), p) for p in PROVIDERS],
                            value=config.provider,
                            allow_blank=False,
                            id="settings-provider",
                            classes="field-select",
                        )
                        yield Label("OPENROUTER BASE URL", classes="field-label")
                        yield Input(
                            value=config.openrouter_base_url,
                            id="settings-openrouter-url",
                            classes="field-input",
                        )
                        yield Label("GEMINI BASE URL", classes="field-label")
                        yield Input(
                            value=config.gemini_base_url,
                            id="settings-gemini-url",
                            classes="field-input",
                        )
                        yield Label("FREELLMAPI BASE URL", classes="field-label")
                        yield Input(
                            value=config.freellmapi_base_url,
                            id="settings-freellmapi-url",
                            classes="field-input",
                        )
                        yield Label("OPENAI-COMPATIBLE BASE URL", classes="field-label")
                        yield Input(
                            value=config.openai_base_url,
                            id="settings-openai-url",
                            classes="field-input",
                        )
                        yield Label("TEMPERATURE (0–2)", classes="field-label")
                        yield Input(
                            value=str(config.temperature),
                            id="settings-temperature",
                            classes="field-input",
                        )
                        yield Label("MAX OUTPUT TOKENS", classes="field-label")
                        yield Input(
                            value=str(config.max_tokens),
                            id="settings-max-tokens",
                            classes="field-input",
                        )

                with TabPane("Model", id="model"):
                    with VerticalScroll():
                        yield Label("ACTIVE MODEL", classes="field-label")
                        yield Static(
                            Text(
                                f"{config.provider} · {config.active_model()}",
                                style=f"bold {BLUE}",
                            ),
                            id="settings-current-model",
                        )
                        yield Static(
                            "Use the Model Explorer to search and select a "
                            "model with capability details.",
                            classes="field-note",
                        )
                        yield Button("Browse Models", id="settings-models")
                        yield Label("IMAGE MODEL", classes="field-label")
                        yield Input(
                            value=config.image_model,
                            id="settings-image-model",
                            classes="field-input",
                        )
                        yield Static(
                            "Used for natural-language image generation through "
                            "the FreeLLMAPI images endpoint.",
                            classes="field-note",
                        )

                with TabPane("API Keys", id="api-keys"):
                    with VerticalScroll():
                        for provider, field_name in KEY_FIELDS.items():
                            yield Label(
                                PROVIDER_LABELS.get(provider, provider).upper()
                                + " API KEY",
                                classes="field-label",
                            )
                            yield Input(
                                value=getattr(config, field_name),
                                password=True,
                                placeholder="Not configured",
                                id=f"settings-key-{field_name}",
                                classes="field-input",
                            )
                        yield Static(
                            "Keys are stored in the global config file with "
                            "0600 permissions and never written to a workspace.",
                            classes="field-note",
                        )

                with TabPane("Permissions", id="permissions"):
                    with VerticalScroll():
                        yield Label("PERMISSION MODE", classes="field-label")
                        yield Select(
                            [
                                ("DENIED — block every tool", "denied"),
                                ("NORMAL — ask before writes/commands", "normal"),
                                ("FULL — auto-approve everything", "full"),
                            ],
                            value=config.permission_mode,
                            allow_blank=False,
                            id="settings-permission",
                            classes="field-select",
                        )
                        yield Static(
                            "The hard safety block always rejects destructive "
                            "commands such as recursive deletion of / or ~, disk "
                            "formatting, shutdown and piping remote scripts into "
                            "a shell, even in FULL mode.",
                            classes="field-note",
                        )
                        yield Static(
                            "Approval happens at the tool-execution layer, so "
                            "no UI path can bypass it.",
                            classes="field-note",
                        )

                with TabPane("Workspace", id="workspace"):
                    with VerticalScroll():
                        yield Label("CURRENT WORKSPACE", classes="field-label")
                        yield Static(
                            Text(self.workspace_root, style=f"bold {BLUE}"),
                            id="settings-workspace-path",
                        )
                        yield Static(
                            "Changing workspace switches sessions, tasks and "
                            "generated images to that workspace's own data "
                            "directory. Nothing is migrated or re-run.",
                            classes="field-note",
                        )
                        yield Button(
                            "Change Workspace", id="settings-workspace"
                        )
                        yield Label("DATA DIRECTORY", classes="field-label")
                        yield Static(
                            Text(
                                str(Path(self.workspace_root) / ".freenerf")
                                if self.workspace_root else "(unknown)",
                                style=MUTED,
                            )
                        )

            with Horizontal(id="settings-buttons"):
                yield Button("Cancel", id="settings-cancel")
                yield Button("Apply", variant="primary", id="settings-apply")

    # ------------------------------------------------------------------
    def _integer(self, widget_id, label, minimum, maximum):
        raw = self.query_one(f"#{widget_id}", Input).value.strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self.app.notify(f"{label} must be an integer.", severity="error")
            return None
        if not minimum <= value <= maximum:
            self.app.notify(
                f"{label} must be between {minimum} and {maximum}.",
                severity="error",
            )
            return None
        return value

    def _collect_settings(self, *, open_models=False, open_workspace=False):
        max_steps = self._integer("settings-max-steps", "Max agent steps", 1, 100)
        retries = self._integer("settings-max-retries", "Provider retries", 0, 5)
        context = self._integer(
            "settings-context", "Context budget", 4000, 2_000_000
        )
        max_tokens = self._integer(
            "settings-max-tokens", "Max output tokens", 1, 32768
        )
        font_size = self.query_one("#settings-font-size", Select).value
        custom_font = self.config.font_size_custom
        if font_size == "custom":
            custom_font = self._integer(
                "settings-custom-font-size", "Custom font size", 6, 32
            )

        if None in (max_steps, retries, context, max_tokens, custom_font):
            return None

        try:
            temperature = float(
                self.query_one("#settings-temperature", Input).value.strip()
            )
        except (TypeError, ValueError):
            self.app.notify("Temperature must be a number.", severity="error")
            return None

        if not 0.0 <= temperature <= 2.0:
            self.app.notify("Temperature must be between 0 and 2.", severity="error")
            return None

        try:
            speed = float(
                self.query_one("#settings-speed", Input).value.strip()
            )
        except (TypeError, ValueError):
            self.app.notify("Typewriter speed must be a number.", severity="error")
            return None

        if not 0.001 <= speed <= 0.2:
            self.app.notify(
                "Typewriter speed must be between 0.001 and 0.2 seconds.",
                severity="error",
            )
            return None

        key_values = {
            field_name: self.query_one(
                f"#settings-key-{field_name}", Input
            ).value.strip()
            for field_name in KEY_FIELDS.values()
        }

        custom_theme = {}
        for palette_key, fallback in DEFAULT_CUSTOM_THEME.items():
            if palette_key == "dark":
                custom_theme[palette_key] = (
                    self.query_one("#settings-color-mode", Select).value == "true"
                )
                continue
            value = self.query_one(f"#settings-color-{palette_key}", Input).value.strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                self.app.notify(
                    f"{palette_key.title()} color must use #RRGGBB format.",
                    severity="error",
                )
                return None
            custom_theme[palette_key] = value.upper()

        return {
            "open_models": bool(open_models),
            "custom_prompt": self.query_one(
                "#settings-custom-prompt", TextArea
            ).text,
            "open_workspace": bool(open_workspace),
            **key_values,
            "theme": self.query_one("#settings-theme", Select).value,
            "custom_theme": custom_theme,
            "ui_size": self.query_one("#settings-ui-size", Select).value,
            "font_size": font_size,
            "font_size_custom": custom_font,
            "provider": self.query_one("#settings-provider", Select).value,
            "permission_mode": self.query_one(
                "#settings-permission", Select
            ).value,
            "streaming": (
                self.query_one("#settings-streaming", Select).value == "true"
            ),
            "typewriter_enabled": (
                self.query_one("#settings-typewriter", Select).value == "true"
            ),
            "typewriter_speed": speed,
            "max_agent_steps": max_steps,
            "max_retries": retries,
            "max_context_chars": context,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "image_model": self.query_one(
                "#settings-image-model", Input
            ).value.strip(),
            "openrouter_base_url": self.query_one(
                "#settings-openrouter-url", Input
            ).value.strip(),
            "gemini_base_url": self.query_one(
                "#settings-gemini-url", Input
            ).value.strip(),
            "freellmapi_base_url": self.query_one(
                "#settings-freellmapi-url", Input
            ).value.strip(),
            "openai_base_url": self.query_one(
                "#settings-openai-url", Input
            ).value.strip(),
        }

    @on(Button.Pressed, "#settings-models")
    def models_pressed(self):
        result = self._collect_settings(open_models=True)
        if result is not None:
            self.dismiss(result)

    @on(Button.Pressed, "#settings-workspace")
    def workspace_pressed(self):
        result = self._collect_settings(open_workspace=True)
        if result is not None:
            self.dismiss(result)

    @on(Button.Pressed, "#settings-cancel")
    def cancel_pressed(self):
        self.dismiss(None)

    def action_close(self):
        self.dismiss(None)

    @on(Button.Pressed, "#settings-apply")
    def apply_pressed(self):
        result = self._collect_settings()
        if result is not None:
            self.dismiss(result)


class ActivityScreen(ModalScreen[None]):
    """Mobile-friendly full-screen view of recent tool activity."""

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    CSS = """
    ActivityScreen { align: center middle; }
    #activity-box {
        width: 98%;
        max-width: 110;
        height: 96%;
        padding: 1 1;
        border: round $accent;
        background: $surface;
    }
    #activity-title { height: 1; color: $accent; text-style: bold; }
    #activity-log { height: 1fr; border: none; background: $surface; padding: 0 1; }
    #activity-footer { height: 3; align-horizontal: right; }
    """

    def __init__(self, events=None):
        super().__init__()
        self.events = list(events or [])

    def compose(self) -> ComposeResult:
        with Container(id="activity-box"):
            yield Label("FREENERF  /  TOOL ACTIVITY", id="activity-title")
            yield RichLog(id="activity-log", wrap=True, markup=False, auto_scroll=True)
            with Horizontal(id="activity-footer"):
                yield Button("Close", variant="primary", id="activity-close")

    def on_mount(self):
        log = self.query_one("#activity-log", RichLog)
        # Render a bounded snapshot; the panel's record method owns formatting.
        for event in self.events[-120:]:
            name = str(event.get("name") or "tool")
            phase = str(event.get("phase") or "")
            kind = str(event.get("kind") or "")
            color = {
                "started": BLUE,
                "waiting_approval": WARNING,
                "finished": SUCCESS if kind == "ok" else ERROR,
                "failed": ERROR,
            }.get(phase, MUTED)
            line = Text()
            line.append({
                "started": "▶ ",
                "waiting_approval": "⏳ ",
                "finished": "✓ " if kind == "ok" else "✗ ",
                "failed": "✗ ",
            }.get(phase, "· "), style=color)
            line.append(name, style="bold")
            detail = str(event.get("detail") or event.get("summary") or "")
            if detail:
                line.append("  " + detail[:180], style=MUTED)
            elapsed = event.get("elapsed")
            if isinstance(elapsed, (int, float)):
                line.append(f"  {elapsed:.2f}s", style=MUTED)
            log.write(line)
        if not self.events:
            log.write(Text("No tool activity in this session yet.", style=MUTED))

    @on(Button.Pressed, "#activity-close")
    def close_pressed(self):
        self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


class SessionHistoryScreen(ModalScreen[dict | None]):
    """Search, rename, close, delete and open previous sessions."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    CSS = """
    SessionHistoryScreen {
        align: center middle;
    }

    #history-box {
        width: 92%;
        max-width: 90;
        height: 85%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #history-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #session-search {
        margin-bottom: 1;
    }

    #session-list {
        height: 1fr;
        border: round $primary;
        margin-bottom: 1;
    }

    #rename-row {
        height: 3;
        margin-bottom: 1;
    }

    #rename-row Input {
        width: 1fr;
    }

    #rename-row Button {
        margin-left: 1;
    }

    #history-buttons {
        height: 3;
        align-horizontal: right;
    }

    #history-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, store, current_session_id=""):
        super().__init__()
        self.store = store
        self.current_session_id = current_session_id
        self.sessions: list[dict] = []
        self.selected_id = ""

    def compose(self) -> ComposeResult:
        with Container(id="history-box"):
            yield Label("SESSION HISTORY", id="history-title")
            yield Input(
                placeholder="Search titles and messages...",
                id="session-search",
            )
            yield OptionList(id="session-list")
            with Horizontal(id="rename-row"):
                yield Input(
                    placeholder="New title for the selected session",
                    id="rename-input",
                )
                yield Button("Rename", id="session-rename")
            with Horizontal(id="history-buttons"):
                yield Button("Open", variant="primary", id="session-open")
                yield Button("Close session", id="session-close-action")
                yield Button("Delete", variant="error", id="session-delete")
                yield Button("Cancel", id="session-cancel")

    def on_mount(self):
        self.refresh_list("")

    # ------------------------------------------------------------------
    def refresh_list(self, query):
        self.sessions = self.store.search(query, limit=50)
        options = self.query_one("#session-list", OptionList)
        options.clear_options()

        for session in self.sessions:
            marker = "● " if session["id"] == self.current_session_id else "  "
            title = str(session.get("title") or "New session")[:52]
            closed = bool(session.get("is_closed"))
            updated = str(session.get("updated_at") or "")[:16].replace("T", " ")

            label = Text()
            label.append(
                f"{marker}{title}",
                style="bold" if marker.strip() else "",
            )
            if closed:
                label.append("  [closed]", style=f"dim {MUTED}")
            label.append(
                f"\n   {session['id'][:8]} · {updated}",
                style=f"dim {MUTED}",
            )
            options.add_option(Option(label, id=session["id"]))

        if self.sessions:
            options.highlighted = 0
            self.selected_id = self.sessions[0]["id"]
            self.query_one("#rename-input", Input).value = str(
                self.sessions[0].get("title") or ""
            )
            self._update_session_actions(self.sessions[0])
        else:
            self.selected_id = ""
            self.query_one("#rename-input", Input).value = ""
            self._update_session_actions(None)

    def _update_session_actions(self, session):
        closed = bool(session and session.get("is_closed"))
        self.query_one("#session-open", Button).label = (
            "Reopen" if closed else "Open"
        )
        self.query_one("#session-close-action", Button).label = (
            "Reopen session" if closed else "Close session"
        )

    @on(Input.Changed, "#session-search")
    def search_changed(self, event):
        self.refresh_list(event.value)

    @on(OptionList.OptionHighlighted, "#session-list")
    def highlighted(self, event):
        self.selected_id = event.option.id or ""
        session = next(
            (item for item in self.sessions if item["id"] == self.selected_id),
            None,
        )
        if session:
            self.query_one("#rename-input", Input).value = str(
                session.get("title") or ""
            )
        self._update_session_actions(session)

    @on(OptionList.OptionSelected, "#session-list")
    def opened(self, event):
        self.dismiss({"action": "open", "session_id": event.option.id})

    def _selected(self):
        session = next(
            (item for item in self.sessions if item["id"] == self.selected_id),
            None,
        )
        if session is None:
            self.app.notify("Select a session first.", severity="warning")
        return session

    @on(Button.Pressed, "#session-open")
    def open_pressed(self):
        session = self._selected()
        if session:
            self.dismiss({"action": "open", "session_id": session["id"]})

    @on(Button.Pressed, "#session-rename")
    def rename_pressed(self):
        session = self._selected()
        if not session:
            return
        title = self.query_one("#rename-input", Input).value.strip()
        if not title:
            self.app.notify("Enter a new title.", severity="warning")
            return
        self.store.rename(session["id"], title)
        self.refresh_list(self.query_one("#session-search", Input).value)

    @on(Button.Pressed, "#session-close-action")
    def close_session_pressed(self):
        session = self._selected()
        if not session:
            return
        if session.get("is_closed"):
            self.store.reopen_session(session["id"])
        else:
            self.store.close_session(session["id"])
        self.dismiss({"action": "changed", "session_id": session["id"]})

    @on(Button.Pressed, "#session-delete")
    def delete_pressed(self):
        session = self._selected()
        if not session:
            return

        def confirmed(approved):
            if not approved:
                return
            self.store.delete(session["id"])
            self.refresh_list(self.query_one("#session-search", Input).value)
            self.app.notify("Session deleted.", severity="information")

        self.app.push_screen(
            ConfirmScreen(
                "Delete session?",
                f"{session.get('title') or session['id']}",
                "The session and its messages are permanently removed. A "
                "backup of the database is not created.",
                approve_label="Delete",
            ),
            confirmed,
        )

    @on(Button.Pressed, "#session-cancel")
    def cancel_pressed(self):
        self.dismiss({"action": "changed", "session_id": self.selected_id})

    def action_close(self):
        self.dismiss({"action": "changed", "session_id": self.selected_id})


class WorkspaceScreen(ModalScreen[dict | None]):
    """Directory browser for choosing the active workspace.

    Browsing never requires typing a path. Files are shown for orientation but
    cannot be selected; only a readable, writable directory can be confirmed.
    """

    BINDINGS = [
        Binding("escape", "close", "Cancel", show=False),
        Binding("backspace", "up", "Up", show=False),
        Binding("ctrl+s", "select_folder", "Select", show=False),
        Binding("f2", "select_folder", "Select", show=False),
    ]

    CSS = """
    WorkspaceScreen {
        align: center middle;
    }

    #workspace-box {
        width: 96%;
        max-width: 100;
        height: 92%;
        padding: 0 1;
        border: round $primary;
        background: $surface;
    }

    #workspace-title {
        height: 1;
        color: $accent;
        text-style: bold;
    }

    #workspace-path {
        height: 1;
        padding: 0 1;
        color: $primary;
        overflow: hidden;
    }

    #workspace-list {
        height: 1fr;
        border: none;
        background: $surface;
    }

    #workspace-hint {
        height: auto;
        max-height: 3;
        color: $text-muted;
    }

    #workspace-nav, #workspace-actions {
        height: 3;
        align-horizontal: left;
    }

    #workspace-nav Button, #workspace-actions Button {
        min-width: 7;
        margin-right: 1;
    }

    #workspace-use {
        min-width: 15;
    }
    """

    SKIP = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        ".pytest_cache", ".mypy_cache", "dist", "build", "target",
        ".freenerf",
    }
    MAX_VISIBLE_ENTRIES = 500

    def __init__(self, workspace: Workspace, recent=None):
        super().__init__()
        self.current: Workspace = workspace
        self.current_path = Path(workspace.root)
        self.recent = list(recent or [])

    def compose(self) -> ComposeResult:
        with Container(id="workspace-box"):
            yield Label("WORKSPACE", id="workspace-title")
            yield Static("", id="workspace-path")
            yield OptionList(id="workspace-list")
            yield Static("", id="workspace-hint")
            with Horizontal(id="workspace-nav"):
                yield Button("Up", id="workspace-up")
                yield Button("Home", id="workspace-home")
                yield Button("Storage", id="workspace-storage")
            with Horizontal(id="workspace-actions"):
                yield Button(
                    "Select folder", variant="primary", id="workspace-use"
                )
                yield Button("Cancel", id="workspace-cancel")

    def on_mount(self):
        self.load(self.current_path)

    # ------------------------------------------------------------------
    def _display_path(self, path: Path) -> str:
        home = str(Path(os.environ.get("HOME") or Path.home()))
        value = str(path)
        if value == home:
            return "~"
        if value.startswith(home + os.sep):
            return "~" + value[len(home):]
        return value

    def load(self, path):
        """Browse ``path`` without requiring workspace write access."""
        raw = os.path.expandvars(os.path.expanduser(str(path or ""))).strip()
        if not raw:
            return

        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate

        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()

        if not candidate.exists():
            self.app.notify(
                f"Folder does not exist: {candidate}", severity="error"
            )
            return
        if not candidate.is_dir():
            self.app.notify(
                f"Not a folder: {candidate.name}. Only folders can be selected.",
                severity="warning",
            )
            return

        entries = []
        truncated = False
        try:
            with os.scandir(candidate) as iterator:
                for entry in iterator:
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        is_dir = False
                    if is_dir and entry.name in self.SKIP:
                        continue
                    entries.append((not is_dir, entry.name.casefold(), entry, is_dir))
                    if len(entries) > self.MAX_VISIBLE_ENTRIES:
                        entries.pop()
                        truncated = True
                        break
        except PermissionError:
            self.app.notify(
                f"Permission denied: {candidate}. Use a folder Termux can read.",
                severity="error",
            )
            return
        except OSError as exc:
            self.app.notify(
                f"Cannot read {candidate}: {exc}", severity="error"
            )
            return

        entries.sort(key=lambda item: (item[0], item[1]))
        directories = [item for item in entries if item[3]]
        files = [item for item in entries if not item[3]]

        self.current_path = candidate
        self.query_one("#workspace-path", Static).update(
            Text(self._display_path(candidate), style=f"bold {BLUE}")
        )

        options = self.query_one("#workspace-list", OptionList)
        options.clear_options()

        for _, _, entry, _ in directories:
            label = Text()
            label.append("📁 ", style=BLUE)
            label.append(entry.name + "/", style="bold")
            options.add_option(Option(label, id=f"dir:{entry.path}"))

        for _, _, entry, _ in files:
            label = Text()
            label.append("📄 ", style=f"dim {MUTED}")
            label.append(entry.name, style=MUTED)
            options.add_option(
                Option(label, id=f"file:{entry.path}", disabled=True)
            )

        if directories:
            options.highlighted = 0

        hint = Text()
        hint.append(
            f"{len(directories)} folders · {len(files)} files",
            style=MUTED,
        )
        if truncated:
            hint.append(
                f"  ·  showing first {self.MAX_VISIBLE_ENTRIES} entries",
                style=f"bold {MUTED}",
            )
        hint.append(
            "   Enter open · Backspace up · Ctrl+S select",
            style=f"dim {MUTED}",
        )
        hint.append("\nFiles are shown but cannot be selected.", style=f"dim {MUTED}")
        if self.recent:
            hint.append(
                "\nRecent: " + "  ·  ".join(self.recent[:2]),
                style=f"dim {MUTED}",
            )
        self.query_one("#workspace-hint", Static).update(hint)

    # ------------------------------------------------------------------
    @on(OptionList.OptionSelected, "#workspace-list")
    def open_directory(self, event):
        value = event.option.id or ""
        if value.startswith("dir:"):
            self.load(value[len("dir:"):])
        elif value.startswith("file:"):
            self.app.notify(
                "Only folders can be used as a workspace.",
                severity="warning",
            )

    def action_up(self):
        parent = self.current_path.parent
        if parent == self.current_path:
            return
        self.load(parent)

    @on(Button.Pressed, "#workspace-up")
    def up_pressed(self):
        self.action_up()

    @on(Button.Pressed, "#workspace-home")
    def home_pressed(self):
        self.load(os.environ.get("HOME") or Path.home())

    @on(Button.Pressed, "#workspace-storage")
    def storage_pressed(self):
        for candidate in ("/storage/emulated/0", "/sdcard"):
            if Path(candidate).is_dir():
                self.load(candidate)
                return
        self.app.notify(
            "Android shared storage was not found. Grant storage access to "
            "Termux, or browse your home folder.",
            severity="warning",
        )

    def action_select_folder(self):
        self.use_pressed()

    @on(Button.Pressed, "#workspace-use")
    def use_pressed(self):
        if not self.current_path.is_dir():
            self.app.notify("Select a folder first.", severity="warning")
            return

        if not os.access(self.current_path, os.W_OK | os.X_OK):
            self.app.notify(
                f"Permission denied: {self.current_path} is not writable. "
                "Choose another folder or grant storage access to Termux.",
                severity="error",
            )
            return

        self.dismiss({"path": str(self.current_path)})

    @on(Button.Pressed, "#workspace-cancel")
    def cancel_pressed(self):
        self.dismiss(None)

    def action_close(self):
        self.dismiss(None)
