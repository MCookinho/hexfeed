#!/usr/bin/env python3
"""
hexfeed - iniciador do cliente TUI
Uso: python run_client.py

Inicia a interface gráfica de terminal do hexfeed.
Requer o servidor rodando em http://127.0.0.1:8000
"""

import sys
import os

# Adiciona a raiz do projeto ao path para os imports funcionarem
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client.app import HexfeedApp


def main():
    app = HexfeedApp()
    app.run()


if __name__ == "__main__":
    main()
