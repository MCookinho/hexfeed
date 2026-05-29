from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button
from textual.containers import Center, Vertical
from textual.binding import Binding


class OutdatedScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.quit", "Sair"),
    ]

    CSS = """
    OutdatedScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #outdated-box {
        width: 60; height: auto; padding: 2 3;
        border: solid #ef4444; background: #1a0a0a;
    }
    #outdated-title {
        text-style: bold; color: #ef4444;
        text-align: center; width: 100%; margin-bottom: 1;
    }
    #outdated-msg {
        color: #e2e8f0; text-align: center; width: 100%; margin-bottom: 1;
    }
    #outdated-detail {
        color: #94a3b8; text-align: center; width: 100%; margin-bottom: 2;
    }
    #quit-btn {
        width: 100%; background: #ef4444; color: #e2e8f0;
    }
    #hint {
        color: #475569; text-align: center; width: 100%; margin-top: 1;
    }
    """

    def __init__(self, required_version: str):
        super().__init__()
        self.required_version = required_version

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="outdated-box"):
                yield Static("CLIENTE DESATUALIZADO", id="outdated-title")
                yield Static(
                    f"Seu cliente está na versão {__import__('client.version', fromlist=['__version__']).__version__}, "
                    f"mas o servidor exige a versão {self.required_version} ou superior.",
                    id="outdated-msg",
                )
                yield Static(
                    "Baixe a versão mais recente do cliente para continuar.",
                    id="outdated-detail",
                )
                yield Button("Sair", id="quit-btn")
                yield Static("Pressione Esc para sair", id="hint")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "quit-btn":
            self.app.exit()
