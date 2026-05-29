"""Entry point for the hexfeed TUI client."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.app import HexfeedApp


def main():
    app = HexfeedApp()
    app.run()
