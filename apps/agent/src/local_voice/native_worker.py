"""Resident pinned Parakeet model, exclusively owned by this supervised child.

The parent bounds startup/abort/shutdown even if a native call stops responding.
No audio or transcript reaches stdout/stderr or disk; only the private IPC socket.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import math
import socket
import threading
import time

from .native_protocol import encode, receive_blocking

REVISION = "5700f127e38dd53c4735ea23ef743b4b538eacd4"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-fd", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--threads", type=int, required=True)
    args = parser.parse_args()
    sock = socket.socket(fileno=args.socket_fd)
    writing = threading.Lock()
    owner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet-native-owner")
    state_lock = threading.Lock()
    state = {"active": None, "model": None, "session": None}

    def send(message):
        with writing:
            sock.sendall(encode(message))

    def load():
        import transcribe_cpp as native
        # Binding validates ABI/header layout at import; also pin runtime revision.
        native.set_log_callback(None)
        commit = native.native_commit()
        if native.__version__ != "0.4.1" or native.native_version() != "0.4.1" or len(commit) < 7 or not REVISION.startswith(commit):
            raise RuntimeError("Unexpected Parakeet native revision")
        began = time.monotonic()
        model = native.Model(args.model, backend="cuda")  # No silent CPU fallback.
        model_load_ms = (time.monotonic() - began) * 1000
        try:
            session = model.session(n_threads=args.threads)
        except BaseException:
            model.close()
            raise
        state.update(model=model, session=session)
        return native, model_load_ms

    def cancel(active):
        active["cancelled"].set()
        # Session.run clears a stale cancel flag at entry. Repeat until it exits,
        # closing the narrow race between a queued run and its native start.
        def abort():
            while True:
                with state_lock:
                    if state["active"] is not active or active["done"].is_set():
                        return
                    state["session"].cancel()
                active["done"].wait(0.02)
        threading.Thread(target=abort, name="parakeet-native-abort", daemon=True).start()

    try:
        started = time.monotonic()
        native, model_load_ms = owner.submit(load).result()
        send({"type": "ready", "version": native.native_version(), "commit": native.native_commit(),
              "startup_ms": (time.monotonic() - started) * 1000, "model_load_ms": model_load_ms})

        def compute(active, pcm):
            import numpy as np
            started = time.monotonic()
            try:
                if active["cancelled"].is_set():
                    raise native.Aborted("cancelled before inference")
                # Matches dr_wav int16 conversion: sample / 32768.0f.
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(32768)
                result = state["session"].run(samples, timestamps="none")
                timings = {}
                for field in ("mel_ms", "encode_ms", "decode_ms"):
                    value = getattr(result.timings, field, None)
                    timings["native_" + field] = value if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None
                timings["native_total_ms"] = (time.monotonic() - started) * 1000
                response = {"type": "result", "id": active["id"], "text": result.text.strip(), "timings": timings}
            except native.Aborted:
                response = {"type": "cancelled", "id": active["id"]}
            except BaseException:
                # Native exception strings can include checkpoint paths/input.
                response = {"type": "error", "id": active["id"]}
            finally:
                active["done"].set()
            if active["cancelled"].is_set():
                response = {"type": "cancelled", "id": active["id"]}
            with state_lock:
                if state["active"] is active:
                    state["active"] = None
            try:
                send(response)
            except (OSError, ValueError):
                sock.close()

        while True:
            message, pcm = receive_blocking(sock)
            kind = message["type"]
            if kind == "run":
                request_id = message.get("id")
                if not isinstance(request_id, str) or len(request_id) != 32 or not pcm or len(pcm) % 2:
                    raise ValueError("Invalid inference request")
                with state_lock:
                    if state["active"] is not None:
                        raise ValueError("Only one native inference may be active")
                    active = {"id": request_id, "cancelled": threading.Event(), "done": threading.Event()}
                    state["active"] = active
                owner.submit(compute, active, pcm)
            elif kind == "cancel" and not pcm:
                with state_lock:
                    active = state["active"]
                    if active and active["id"] == message.get("id") and not active["cancelled"].is_set():
                        cancel(active)
            elif kind == "close" and not pcm:
                break
            else:
                raise ValueError("Invalid native worker command")
    except BaseException:
        try:
            send({"type": "worker_error"})
        except (OSError, ValueError):
            pass
    finally:
        with state_lock:
            active = state["active"]
            if active and not active["cancelled"].is_set():
                cancel(active)
        def close_owned():
            if state["session"]:
                state["session"].close()
            if state["model"]:
                state["model"].close()
        owner.submit(close_owned).result()
        owner.shutdown(wait=True, cancel_futures=True)
        sock.close()


if __name__ == "__main__":
    main()
