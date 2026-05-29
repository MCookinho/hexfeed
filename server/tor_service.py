import os
import signal
import shutil
import time
import sys
from pathlib import Path
from stem.control import Controller
from stem.process import launch_tor_with_config


class TorOnionService:

    def __init__(self, target_port: int, socks_port: int = 19050, control_port: int = 19051):
        self.target_port = target_port
        self.socks_port = socks_port
        self.control_port = control_port
        self.tor_process = None
        self.onion_address: str | None = None
        self.data_dir: Path | None = None
        self._ready = False

    def _key_path(self) -> Path:
        return self.data_dir / "hs_ed25519_secret_key"

    def _load_key(self) -> str | None:
        path = self._key_path()
        if path.exists():
            return path.read_text().strip()
        return None

    def _save_key(self, key: str):
        path = self._key_path()
        path.write_text(key)
        path.chmod(0o600)

    def _cleanup_stale_lock(self):
        lock = self.data_dir / "lock"
        if lock.exists():
            try:
                lock.unlink()
            except Exception:
                pass

    def start(self) -> str | None:
        tor_bin = shutil.which("tor")
        if not tor_bin:
            print("⚠️  Tor binary not found", file=sys.stderr)
            return None

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
                take_ownership=True,
                timeout=None,
            )
        except Exception as e:
            print(f"⚠️  Tor launch failed: {e}", file=sys.stderr)
            self._cleanup()
            return None

        try:
            with Controller.from_port(port=self.control_port) as controller:
                controller.authenticate()
                controller.set_conf("__DisablePredictedCircuits", "1")

                saved_key = self._load_key()
                if saved_key:
                    service = controller.create_ephemeral_hidden_service(
                        {80: self.target_port},
                        key_type="ED25519-V3",
                        key_content=saved_key,
                        await_publication=True,
                    )
                else:
                    service = controller.create_ephemeral_hidden_service(
                        {80: self.target_port},
                        await_publication=True,
                    )
                    self._save_key(service.private_key)

                self.onion_address = f"{service.service_id}.onion"
                self._ready = True
        except Exception as e:
            print(f"⚠️  Onion service creation failed: {e}", file=sys.stderr)
            self._cleanup()
            return None

        return self.onion_address

    def is_ready(self) -> bool:
        return self._ready

    def stop(self):
        self._cleanup()

    def _cleanup(self):
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
