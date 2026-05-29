"""
hexfeed - tela de confirmação
Exibe mensagem e botões Confirmar/Cancelar. Retorna bool via dismiss.
"""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button
from textual.containers import Horizontal, Vertical
from textual.binding import Binding


class ConfirmScreen(Screen):

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(self, title: str, message: str = ""):
        super().__init__()
        self._title = title
        self._message = message

    CSS = """
    ConfirmScreen { background: #0a0a0f; align: center middle; }
    #confirm-box { width: 50; padding: 2; border: solid #ef4444; background: #111118; }
    #confirm-title { color: #ef4444; text-style: bold; margin-bottom: 1; }
    #confirm-msg { color: #e2e8f0; margin-bottom: 1; }
    #confirm-buttons { height: 3; padding-top: 1; }
    Button { width: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._title, id="confirm-title")
            if self._message:
                yield Static(self._message, id="confirm-msg")
            with Horizontal(id="confirm-buttons"):
                yield Button(self.app.lang["cancel"], id="confirm-no")
                yield Button(self.app.lang["confirm"], id="confirm-yes", variant="primary")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "confirm-yes":
            self.dismiss(True)
        elif event.button.id == "confirm-no":
            self.dismiss(False)

    def action_cancel(self):
        self.dismiss(False)
