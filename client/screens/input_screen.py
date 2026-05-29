"""
hexfeed - tela de entrada de texto
Exibe prompt e campo de input. Retorna string ou None via dismiss.
"""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Input, Button
from textual.containers import Horizontal, Vertical
from textual.binding import Binding


class InputScreen(Screen):

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(self, prompt: str, button_label: str | None = None):
        super().__init__()
        self._prompt = prompt
        self._button_label = button_label

    CSS = """
    InputScreen { background: #0a0a0f; align: center middle; }
    #input-box { width: 50; padding: 2; border: solid #7c3aed; background: #111118; }
    #input-prompt { color: #e2e8f0; text-style: bold; margin-bottom: 1; }
    #input-field { width: 100%; }
    #input-buttons { height: 3; padding-top: 1; }
    Button { width: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="input-box"):
            yield Static(self._prompt, id="input-prompt")
            yield Input(id="input-field")
            with Horizontal(id="input-buttons"):
                yield Button(self.app.lang["cancel"], id="input-cancel")
                yield Button(self._button_label or self.app.lang["ok"], id="input-ok", variant="primary")

    def on_mount(self):
        # Foca o campo de input automaticamente ao abrir
        self.query_one("#input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted):
        # Enter no campo confirma e retorna valor
        if event.input.id == "input-field":
            self._done(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "input-ok":
            inp = self.query_one("#input-field", Input)
            self._done(inp.value.strip())
        elif event.button.id == "input-cancel":
            self._done(None)

    def _done(self, value: str | None):
        self.dismiss(value)

    def action_cancel(self):
        self.dismiss(None)
