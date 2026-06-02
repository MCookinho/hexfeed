import os
import signal
import shutil
import time
import sys
from pathlib import Path
from stem.control import Controller
from stem.process import launch_tor_with_config


class TorOnionService:

    def __init__(self, socks_port: int = 19050, control_port: int = 19051):
        self.socks_port = socks_port
        self.control_port = control_port
        self.tor_process = None
        self.controller = None
        self.data_dir: Path | None = None
        self._services: dict[str, str] = {}

    def _service_key_path(self, label: str) -> Path:
        return self.data_dir / f"hs_{label}_ed25519_secret_key"

    def _load_key(self, label: str) -> str | None:
        path = self._service_key_path(label)
        if path.exists():
            return path.read_text().strip()
        return None

    def _save_key(self, label: str, key: str):
        path = self._service_key_path(label)
        path.write_text(key)
        path.chmod(0o600)

    def _cleanup_stale_lock(self):
        lock = self.data_dir / "lock"
        if lock.exists():
            try:
                lock.unlink()
            except Exception:
                pass

    def start(self) -> bool:
        tor_bin = shutil.which("tor")
        if not tor_bin:
            print("⚠️  Tor binary not found", file=sys.stderr)
            return False

        self.data_dir = Path.home() / ".config" / "hexfeed" / "tor"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_lock()

        try:
            self.tor_process = launch_tor_with_config(
                tor_cmd=tor_bin,
                config={
                    "SOCKSPort": str(self.socks_port),
                    "ControlPort": str(self.control_port),
                    "DataDirectory": str(self.data_dir),
                    "CookieAuthentication": "1",
                    "Log": ["warn stdout"],
                },
                take_ownership=False,
                timeout=None,
            )
        except Exception as e:
            print(f"⚠️  Tor launch failed: {e}", file=sys.stderr)
            self._cleanup()
            return False

        try:
            self.controller = Controller.from_port(port=self.control_port)
            self.controller.authenticate()
            self.controller.set_conf("__DisablePredictedCircuits", "1")
        except Exception as e:
            print(f"⚠️  Tor controller failed: {e}", file=sys.stderr)
            self._cleanup()
            return False

        return True

    def create_service(self, label: str, virtual_port: int, target_port: int) -> str | None:
        if not self.controller:
            print(f"⚠️  Tor not started yet", file=sys.stderr)
            return None
        try:
            saved_key = self._load_key(label)
            if saved_key:
                service = self.controller.create_ephemeral_hidden_service(
                    {virtual_port: target_port},
                    key_type="ED25519-V3",
                    key_content=saved_key,
                    await_publication=True,
                )
            else:
                service = self.controller.create_ephemeral_hidden_service(
                    {virtual_port: target_port},
                    await_publication=True,
                )
                self._save_key(label, service.private_key)

            addr = f"{service.service_id}.onion"
            self._services[label] = addr
            return addr
        except Exception as e:
            print(f"⚠️  Onion service '{label}' failed: {e}", file=sys.stderr)
            return None

    def get_address(self, label: str) -> str | None:
        return self._services.get(label)

    def stop(self):
        self._cleanup()

    def _cleanup(self):
        if self.controller:
            try:
                self.controller.close()
            except Exception:
                pass
            self.controller = None
        if self.tor_process:
            try:
                self.tor_process.terminate()
                self.tor_process.wait(timeout=10)
            except Exception:
                try:
                    self.tor_process.kill()
                except Exception:
                    pass
            self.tor_process = None
