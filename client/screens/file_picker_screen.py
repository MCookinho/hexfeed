"""
hexfeed - seletor de arquivos
Navega pelo sistema de arquivos e seleciona um arquivo para upload.
"""

import os
from pathlib import Path
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Tree
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual import work


class FilePickerScreen(Screen):
    """
    Tela para navegar pelos diretórios e selecionar um arquivo.
    Emite resultado via self.dismiss(result) com o caminho absoluto.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    CSS = """
    FilePickerScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #picker-box {
        width: 80; height: 80%;
        border: solid #7c3aed; background: #111118;
    }
    #picker-header {
        height: 3; background: #1a1a2e;
        border-bottom: solid #7c3aed;
        padding: 0 2;
        text-style: bold; color: #7c3aed;
        content-align: center middle; width: 100%;
    }
    #picker-path {
        height: 1; color: #64748b;
        padding: 0 2; background: #0a0a0f;
    }
    Tree { height: 1fr; }
    Button { width: 100%; margin-top: 1; }
    #picker-actions {
        dock: bottom; height: 4;
        padding: 0 2; 
        background: #111118; border-top: solid #1e293b;
    }
    #selected-info { color: #64748b; padding: 0 2; height: 1; text-align: center; }
    """

    def __init__(self, start_path: str = "/"):
        super().__init__()
        self.start_path = os.path.abspath(start_path)
        self.selected_path = None

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(self.app.lang["file_picker_title"], id="picker-header")
            yield Tree(self.app.lang["file_picker_tree_label"], id="file-tree")
            yield Static("", id="selected-info")
            with Horizontal(id="picker-actions"):
                yield Button(self.app.lang["file_picker_upload_btn"], id="picker-upload", variant="primary")
                yield Button(self.app.lang["cancel"], id="picker-cancel")

    def on_mount(self):
        self._populate_tree()

    def _populate_tree(self):
        """Popula a árvore com a estrutura de diretórios."""
        tree = self.query_one("#file-tree", Tree)
        tree.clear()

        root_path = self.start_path
        if not os.path.isdir(root_path):
            root_path = "/home"

        root_label = f"📁 {root_path}"
        root = tree.root
        root.label = root_label
        root.data = root_path
        root.expand()

        self.query_one("#selected-info", Static).update(f"{self.app.lang['path_label']}: {root_path}")

    def on_tree_node_selected(self, event: Tree.NodeSelected):
        """Quando um nó da árvore é selecionado, atualiza o caminho e mostra info."""
        node = event.node
        path = node.data
        if not path or not isinstance(path, str):
            return

        self.selected_path = path
        info = self.query_one("#selected-info", Static)

        if os.path.isdir(path):
            info.update(f"📁 {path}/")
        else:
            size = os.path.getsize(path)
            size_str = f"{size / 1024:.1f} KB" if size < 1024*1024 else f"{size / (1024*1024):.1f} MB"
            info.update(f"📄 {path}  ({size_str})")

    def on_tree_node_expanded(self, event: Tree.NodeExpanded):
        """Quando um diretório é expandido, carrega seu conteúdo."""
        node = event.node
        path = node.data
        if not path or not isinstance(path, str) or not os.path.isdir(path):
            return
        self._load_children(node, path)

    def _load_children(self, node, path: str):
        """Carrega os filhos de um diretório na árvore."""
        # Remove placeholder de loading se existir
        children = list(node.children)
        for child in children:
            if child.data == "__loading__":
                node.remove_child(child)

        try:
            # Ordena: diretórios primeiro, depois arquivos; ambos alfabeticamente
            entries = sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
        except PermissionError:
            node.add(self.app.lang["file_picker_no_permission"], data=None)
            return
        except OSError:
            return

        for entry in entries:
            # Ignora arquivos ocultos
            if entry.startswith("."):
                continue
            full_path = os.path.join(path, entry)
            try:
                if os.path.isdir(full_path):
                    n = node.add(f"📁 {entry}", data=full_path)
                    # Placeholder para permitir expansão (Tree não expande nós sem filhos)
                    n.add("...", data="__placeholder__")
                elif os.path.isfile(full_path):
                    node.add(f"📄 {entry}", data=full_path)
            except PermissionError:
                pass

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "picker-upload":
            if self.selected_path and os.path.isfile(self.selected_path):
                self.dismiss(self.selected_path)
            else:
                self.notify(self.app.lang["file_picker_select_first"], severity="warning")
        elif event.button.id == "picker-cancel":
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)
