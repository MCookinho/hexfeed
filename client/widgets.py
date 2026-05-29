"""
hexfeed - widgets compartilhados
Componentes reutilizáveis para a interface TUI.
"""

from textual.widgets import Static
from textual.app import ComposeResult
from textual.containers import Horizontal
from client.version import __version__


class HeaderBar(Static):
    """Cabeçalho com o logo hexfeed (não usado atualmente)."""

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static(self.app.lang["header_brand"], classes="brand"),
            Static(self.app.lang["version_label"].format(__version__), classes="version"),
            classes="header-row",
        )

    def on_mount(self):
        self.styles.height = 3
        self.styles.dock = "top"


class FooterBar(Static):
    """Rodapé com dicas de navegação (não usado atualmente)."""

    def compose(self) -> ComposeResult:
        yield Static(
            self.app.lang["footer_nav_hint"],
            classes="footer-text",
        )

    def on_mount(self):
        self.styles.height = 1
        self.styles.dock = "bottom"
