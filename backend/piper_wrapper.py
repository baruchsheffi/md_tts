import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List


class PiperWrapper:
    """Manage a Piper TTS subprocess."""

    def __init__(
        self,
        executable: str = "piper",
        model: str | None = None,
        config: str | None = None,
    ) -> None:
        self.executable = executable
        cmd = [executable]
        if model:
            cmd.extend(["--model", model])
        if config:
            cmd.extend(["--config", config])
        cmd.append("--json-input")

        print(f"Executing Piper command: {cmd}", flush=True)

        if shutil.which(executable):
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        else:
            # Fallback stub when Piper is not installed
            self.process = None

    def synthesize(self, text: str) -> Dict[str, object]:
        if self.process is None:
            # Piper is not installed or failed to start
            raise RuntimeError(self._get_error_message())
        if self.process.poll() is not None:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"Piper terminated: {stderr.strip()}")
        if not self.process.stdin or not self.process.stdout:
            raise RuntimeError("Piper process not started correctly")

        # Piper expects a JSON line with text and an output WAV path
        with tempfile.TemporaryDirectory() as tmpdir:
            out_wav = Path(tmpdir) / "output.wav"
            timings_path = Path(tmpdir) / "timings.json"

            request = json.dumps(
                {"text": text, "output_file": str(out_wav)}
            )  # noqa: E501
            try:
                self.process.stdin.write(request + "\n")
                self.process.stdin.flush()
            except BrokenPipeError as exc:
                stderr = (
                    self.process.stderr.read() if self.process.stderr else ""
                )  # noqa: E501
                raise RuntimeError(
                    f"Piper pipe broken: {stderr.strip()}"
                ) from exc  # noqa: E501

            # Read response from Piper (output path or error message)
            try:
                response_line = self.process.stdout.readline()
            except BrokenPipeError as exc:
                stderr = (
                    self.process.stderr.read() if self.process.stderr else ""
                )  # noqa: E501
                raise RuntimeError(
                    f"Piper pipe broken: {stderr.strip()}"
                ) from exc  # noqa: E501
            if not response_line:
                stderr = (
                    self.process.stderr.read() if self.process.stderr else ""
                )  # noqa: E501
                raise RuntimeError(f"Piper failed: {stderr}")

            resp_path = response_line.strip()
            if resp_path and not Path(resp_path).exists():
                raise RuntimeError(f"Piper error: {resp_path}")

            if timings_path.exists():
                timings = json.loads(timings_path.read_text())
            else:
                # Fallback: basic timings assuming 0.2s per word
                timings = []
                start = 0.0
                for word in text.split():
                    timings.append(
                        {
                            "word": word,
                            "startTime": start,
                            "endTime": start + 0.2,
                        }  # noqa: E501
                    )
                    start += 0.2

            audio_b64 = base64.b64encode(out_wav.read_bytes()).decode(
                "ascii"
            )  # noqa: E501

        return {
            "audioContent": audio_b64,
            "mimeType": "audio/wav",
            "timings": timings,
        }

    def _fake_response(self, text: str) -> Dict[str, object]:
        """Return dummy audio and timings when Piper is unavailable."""
        words = text.split()
        timings: List[Dict[str, float]] = []
        start = 0.0
        for word in words:
            timings.append(
                {"word": word, "startTime": start, "endTime": start + 0.2}
            )  # noqa: E501
            start += 0.2
        # create 1 second of silence as WAV
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            tmp.write(
                b"\x52\x49\x46\x46\x24\x00\x00\x00\x57\x41\x56\x45"
                b"\x66\x6d\x74\x20\x10\x00\x00\x00\x01\x00\x01\x00"
                b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00"
                b"\x64\x61\x74\x61\x00\x00\x00\x00"
            )
            tmp.flush()
            audio = base64.b64encode(Path(tmp.name).read_bytes()).decode(
                "ascii"
            )  # noqa: E501
        return {
            "audioContent": audio,
            "mimeType": "audio/wav",
            "timings": timings,
        }  # noqa: E501

    def _get_error_message(self) -> str:
        """Return an informative error message when Piper is unavailable."""
        try:
            result = subprocess.run(
                [self.executable, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            msg = result.stderr.strip() or result.stdout.strip()
            return msg or "Piper executable not found"
        except OSError as exc:  # FileNotFoundError or similar
            return str(exc)

    def terminate(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
