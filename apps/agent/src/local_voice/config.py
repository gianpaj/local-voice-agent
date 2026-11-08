"""Portable, fail-closed configuration. Inference URLs must stay on loopback."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    parakeet_bin: Path | None
    parakeet_model: Path | None
    phonon_model: Path
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_source: str = "qwen3.5:9b"
    ollama_model: str = "local-voice-qwen"
    llm_max_checkpoint_bytes: int = 8_000_000_000
    allowed_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    stt_timeout: float = 15
    max_utterance_seconds: float = 30
    vad_stop_seconds: float = 0.6
    metrics_dir: Path = Path(".local/metrics")
    metrics_max_bytes: int = 10_000_000
    metrics_backups: int = 5
    stt_backend: str = "parakeet"
    needle_bin: Path | None = None
    whistle_model: Path | None = None
    stt_threads: int = 4
    parakeet_mode: str = "cli"
    parakeet_library: Path | None = None
    parakeet_bindings: Path | None = None
    parakeet_startup_timeout: float = 45
    stun_url: str = ""

    @property
    def ice_servers(self):
        """Public address discovery is explicitly opt-in; no TURN credentials."""
        return [{"urls": self.stun_url}] if self.stun_url else []

    @property
    def stt_model_id(self):
        return "whistle-cact" if self.stt_backend == "whistle" else "parakeet-tdt-0.6b-v3-Q8_0"

    @property
    def stt_device(self):
        return "cpu" if self.stt_backend == "whistle" else "cuda"

    @classmethod
    def from_env(cls):
        load_dotenv(Path(__file__).resolve().parents[4] / ".env")
        def path(name, *, required=True):
            value = os.environ.get(name)
            if not value:
                if not required:
                    return None
                raise ValueError(f"Set {name} in the project .env; see .env.example")
            return Path(value).expanduser().resolve()
        backend = os.getenv("STT_BACKEND", "parakeet")
        result = cls(
            parakeet_bin=path("PARAKEET_BIN", required=backend == "parakeet"),
            parakeet_model=path("PARAKEET_MODEL", required=backend == "parakeet"),
            phonon_model=path("PHONON_MODEL"),
            stt_backend=backend,
            needle_bin=path("NEEDLE_BIN", required=backend == "whistle"),
            whistle_model=path("WHISTLE_MODEL", required=backend == "whistle"),
            stt_threads=int(os.getenv("STT_THREADS", "4")),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_source=os.getenv("OLLAMA_SOURCE_MODEL", "qwen3.5:9b"),
            ollama_model=os.getenv("OLLAMA_MODEL", "local-voice-qwen"),
            llm_max_checkpoint_bytes=int(os.getenv("LLM_MAX_CHECKPOINT_BYTES", "8000000000")),
            metrics_dir=Path(os.getenv("METRICS_DIR", str(Path(__file__).resolve().parents[4] / ".local/metrics"))).expanduser().resolve(),
            metrics_max_bytes=int(os.getenv("METRICS_MAX_BYTES", "10000000")),
            metrics_backups=int(os.getenv("METRICS_BACKUPS", "5")),
            parakeet_mode=os.getenv("PARAKEET_MODE", "cli"),
            parakeet_library=Path(os.environ["PARAKEET_LIBRARY"]).expanduser().resolve() if os.getenv("PARAKEET_LIBRARY") else None,
            parakeet_bindings=Path(os.environ["PARAKEET_BINDINGS"]).expanduser().resolve() if os.getenv("PARAKEET_BINDINGS") else None,
            parakeet_startup_timeout=float(os.getenv("PARAKEET_STARTUP_TIMEOUT", "45")),
            allowed_origins=tuple(x.strip() for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if x.strip()),
            stun_url=os.getenv("WEBRTC_STUN_URL", "").strip(),
        )
        result.validate()
        return result

    def validate(self):
        if not 1 <= self.llm_max_checkpoint_bytes <= 64_000_000_000:
            raise ValueError("LLM_MAX_CHECKPOINT_BYTES must be between 1 and 64000000000")
        if self.stt_backend not in {"parakeet", "whistle"}:
            raise ValueError("STT_BACKEND must be parakeet or whistle")
        if not 1 <= self.stt_threads <= 64:
            raise ValueError("STT_THREADS must be between 1 and 64")
        if self.parakeet_mode not in {"cli", "resident"}:
            raise ValueError("PARAKEET_MODE must be cli or resident")
        if not 1 <= self.parakeet_startup_timeout <= 120:
            raise ValueError("PARAKEET_STARTUP_TIMEOUT must be between 1 and 120 seconds")
        if self.stt_backend == "parakeet" and self.parakeet_mode == "resident":
            if not self.parakeet_library or not self.parakeet_library.is_file():
                raise ValueError("Set PARAKEET_LIBRARY to the pinned shared library")
            if not self.parakeet_bindings or not (self.parakeet_bindings / "transcribe_cpp/__init__.py").is_file():
                raise ValueError("Set PARAKEET_BINDINGS to the matching pinned Python bindings directory")
        if not 1024 <= self.metrics_max_bytes <= 1_000_000_000 or not 1 <= self.metrics_backups <= 100:
            raise ValueError("Metrics retention must be 1 KB–1 GB per file with 1–100 backups")
        if self.stun_url:
            try:
                stun = urlparse("stun://" + self.stun_url.removeprefix("stun:"))
                valid_stun = (
                    self.stun_url.startswith("stun:") and len(self.stun_url) <= 200
                    and not any(c.isspace() for c in self.stun_url)
                    and bool(stun.hostname) and not stun.username and not stun.password
                    and not stun.path and not stun.query and not stun.fragment
                    and (stun.port is None or 1 <= stun.port <= 65535)
                )
            except ValueError:
                valid_stun = False
            if not valid_stun:
                raise ValueError("WEBRTC_STUN_URL must be a bare stun:host[:port] URL without credentials")
        url = urlparse(self.ollama_url)
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.username or url.password:
            raise ValueError("OLLAMA_URL must be a plain HTTP loopback endpoint")
        if self.stt_backend == "parakeet":
            if not self.parakeet_bin or not self.parakeet_bin.is_file() or not os.access(self.parakeet_bin, os.X_OK):
                raise ValueError("PARAKEET_BIN must point to an executable transcribe-cli")
            if not self.parakeet_model or not self.parakeet_model.is_file():
                raise ValueError("PARAKEET_MODEL is missing")
            with self.parakeet_model.open("rb") as model:
                if model.read(4) != b"GGUF":
                    raise ValueError("PARAKEET_MODEL is not a GGUF file")
            checkpoint = self.parakeet_model
        else:
            if not self.needle_bin or not self.needle_bin.is_file() or not os.access(self.needle_bin, os.X_OK):
                raise ValueError("NEEDLE_BIN must point to the native Needle executable, not the Python management CLI")
            if not self.whistle_model or not self.whistle_model.is_file():
                raise ValueError("WHISTLE_MODEL is missing")
            if self.whistle_model.suffix != ".cact":
                raise ValueError("WHISTLE_MODEL must be the Whistle .cact export matching your Needle runner")
            checkpoint = self.whistle_model
        if not 0 < checkpoint.stat().st_size < 8_000_000_000:
            raise ValueError("STT checkpoint must be nonempty and smaller than 8 GB")
        if not self.phonon_model.is_dir():
            raise ValueError("PHONON_MODEL must point to the local English model snapshot directory")
        if sum(p.stat().st_size for p in self.phonon_model.rglob("*") if p.is_file()) >= 8_000_000_000:
            raise ValueError("Phonon snapshot exceeds the 8 GB model limit")
