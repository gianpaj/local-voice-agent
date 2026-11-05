"""Portable, fail-closed configuration. Inference URLs must stay on loopback."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    parakeet_bin: Path
    parakeet_model: Path
    phonon_model: Path
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_source: str = "qwen3.5:9b"
    ollama_model: str = "local-voice-qwen"
    allowed_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    stt_timeout: float = 15
    max_utterance_seconds: float = 30
    vad_stop_seconds: float = 0.6
    metrics_dir: Path = Path(".local/metrics")
    metrics_max_bytes: int = 10_000_000
    metrics_backups: int = 5

    @classmethod
    def from_env(cls):
        load_dotenv(Path(__file__).resolve().parents[4] / ".env")
        def path(name):
            value = os.environ.get(name)
            if not value:
                raise ValueError(f"Set {name} in the project .env; see .env.example")
            return Path(value).expanduser().resolve()
        result = cls(
            parakeet_bin=path("PARAKEET_BIN"), parakeet_model=path("PARAKEET_MODEL"),
            phonon_model=path("PHONON_MODEL"),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_source=os.getenv("OLLAMA_SOURCE_MODEL", "qwen3.5:9b"),
            ollama_model=os.getenv("OLLAMA_MODEL", "local-voice-qwen"),
            metrics_dir=Path(os.getenv("METRICS_DIR", str(Path(__file__).resolve().parents[4] / ".local/metrics"))).expanduser().resolve(),
            metrics_max_bytes=int(os.getenv("METRICS_MAX_BYTES", "10000000")),
            metrics_backups=int(os.getenv("METRICS_BACKUPS", "5")),
            allowed_origins=tuple(x.strip() for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if x.strip()),
        )
        result.validate()
        return result

    def validate(self):
        if not 1024 <= self.metrics_max_bytes <= 1_000_000_000 or not 1 <= self.metrics_backups <= 100:
            raise ValueError("Metrics retention must be 1 KB–1 GB per file with 1–100 backups")
        url = urlparse(self.ollama_url)
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.username or url.password:
            raise ValueError("OLLAMA_URL must be a plain HTTP loopback endpoint")
        if not self.parakeet_bin.is_file() or not os.access(self.parakeet_bin, os.X_OK):
            raise ValueError("PARAKEET_BIN must point to an executable transcribe-cli")
        if not self.parakeet_model.is_file():
            raise ValueError("PARAKEET_MODEL is missing")
        with self.parakeet_model.open("rb") as model:
            if model.read(4) != b"GGUF":
                raise ValueError("PARAKEET_MODEL is not a GGUF file")
        if self.parakeet_model.stat().st_size >= 8_000_000_000:
            raise ValueError("Parakeet checkpoint exceeds the 8 GB model limit")
        if not self.phonon_model.is_dir():
            raise ValueError("PHONON_MODEL must point to the local English model snapshot directory")
        if sum(p.stat().st_size for p in self.phonon_model.rglob("*") if p.is_file()) >= 8_000_000_000:
            raise ValueError("Phonon snapshot exceeds the 8 GB model limit")
