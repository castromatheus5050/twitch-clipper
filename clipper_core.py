"""
clipper_core.py — motor do Twitch Clipper (sem interface).

Monitoriza o chat de canais da Twitch, mantém um buffer circular do vídeo de
cada live e, num pico de mensagens, guarda os últimos N segundos em .mp4.
"""
from __future__ import annotations

import asyncio
import collections
import csv
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

SEG_TIME = 2  # segundos por segmento do buffer
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

BOTS = {
    "nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot",
    "soundalerts", "sery_bot", "kofistreambot", "botrixoficial", "pokemoncommunitygame",
}
PRIVMSG = re.compile(r"^:(\w+)!\S+ PRIVMSG #(\w+) :(.*)$")


def ffmpeg_path() -> str | None:
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for base in (getattr(sys, "_MEIPASS", None), Path(sys.executable).parent, Path(__file__).parent):
        if base and (Path(base) / exe).exists():
            return str(Path(base) / exe)
    return shutil.which("ffmpeg")


def normalize_channel(ch: str) -> str:
    ch = ch.strip().lower().rstrip("/")
    return ch.rsplit("/", 1)[-1].lstrip("#@")


@dataclass
class Settings:
    channels: list[str] = field(default_factory=list)
    out_dir: str = str(Path.home() / "Videos" / "TwitchClips")
    clip_len: int = 30
    post_roll: int = 5
    mult: float = 3.0
    min_rate: float = 1.0
    window: int = 5
    baseline: int = 300
    warmup: int = 60
    cooldown: int = 45
    count_all: bool = False
    quality: str = "best"
    oauth: str = ""


@dataclass
class ChannelStatus:
    state: str = "parado"
    rate: float = 0.0
    baseline: float = 0.0
    threshold: float = 0.0
    clips: int = 0


# ───────────────────────────── Gravação (buffer) ─────────────────────────────
class Recorder:
    def __init__(self, channel: str, s: Settings, workdir: Path, ffmpeg: str, log: Callable[[str], None],
                 status: ChannelStatus):
        self.channel = channel
        self.s = s
        self.ffmpeg = ffmpeg
        self.log = log
        self.status = status
        self.dir = workdir / channel
        self.dir.mkdir(parents=True, exist_ok=True)
        self.wrap = (s.clip_len + s.post_roll + 20) // SEG_TIME + 5
        self.stop_evt = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    @property
    def live(self) -> bool:
        segs = list(self.dir.glob("seg*.ts"))
        return bool(segs) and time.time() - max(x.stat().st_mtime for x in segs) < 10

    def _clean(self) -> None:
        for f in self.dir.glob("seg*.ts"):
            f.unlink(missing_ok=True)

    def _open_stream(self):
        from streamlink import Streamlink
        from streamlink.options import Options

        session = Streamlink()
        opts = Options({"disable-ads": True, "low-latency": True})
        if self.s.oauth.strip():
            token = self.s.oauth.strip().removeprefix("oauth:")
            opts.set("api-header", [("Authorization", f"OAuth {token}")])
        streams = session.streams(f"https://twitch.tv/{self.channel}", options=opts)
        if not streams:
            return None
        for q in [q.strip() for q in self.s.quality.split(",")] + ["best"]:
            if q in streams:
                return streams[q]
        return None

    def _run(self) -> None:
        announced_offline = False
        while not self.stop_evt.is_set():
            self._clean()
            started = time.time()
            try:
                self.status.state = "a ligar…"
                stream = self._open_stream()
                if stream is None:
                    self.status.state = "offline"
                    if not announced_offline:
                        self.log(f"[{self.channel}] offline — à espera que a live comece")
                        announced_offline = True
                else:
                    announced_offline = False
                    self._pump(stream)
            except Exception as e:  # noqa: BLE001
                self.status.state = "erro"
                self.log(f"[{self.channel}] erro no vídeo: {e}")
            if self.stop_evt.is_set():
                break
            if time.time() - started > 60:
                self.log(f"[{self.channel}] a transmissão caiu — a tentar de novo")
            self.stop_evt.wait(15 if time.time() - started > 60 else 45)
        self.status.state = "parado"

    def _pump(self, stream) -> None:
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
               "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
               "-f", "segment", "-segment_time", str(SEG_TIME),
               "-segment_wrap", str(self.wrap), str(self.dir / "seg%03d.ts")]
        with open(self.dir / "ffmpeg.log", "ab") as errlog:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=errlog, creationflags=NO_WINDOW)
            fd = stream.open()
            self.status.state = "a gravar"
            self.log(f"[{self.channel}] live online — a gravar buffer ✔")
            try:
                while not self.stop_evt.is_set():
                    data = fd.read(65536)
                    if not data:
                        break
                    self.proc.stdin.write(data)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    fd.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()

    def segments_since(self, t0: float) -> list[Path]:
        segs = sorted((x.stat().st_mtime, x) for x in self.dir.glob("seg*.ts"))
        return [x for m, x in segs if m >= t0]

    def save_clip(self, out: Path, clip_len: int) -> bool:
        now = time.time()
        segs = self.segments_since(now - clip_len - 2 * SEG_TIME)
        if not segs or now - segs[-1].stat().st_mtime > 10:
            return False
        tmp = self.dir / f"_clip_{int(now * 1000)}.ts"
        with open(tmp, "wb") as f:
            for x in segs:
                f.write(x.read_bytes())
        r = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-sseof", f"-{clip_len}", "-i", str(tmp), "-c", "copy",
                            "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(out)],
                           capture_output=True, creationflags=NO_WINDOW)
        tmp.unlink(missing_ok=True)
        return r.returncode == 0 and out.exists()


# ───────────────────────────── Deteção de picos ──────────────────────────────
class SpikeDetector:
    def __init__(self, s: Settings):
        self.s = s
        self.msgs: collections.deque = collections.deque()
        self.hist: collections.deque = collections.deque(maxlen=s.baseline)

    def add(self, user: str, text: str) -> None:
        self.msgs.append((time.time(), user, text))

    def tick(self, now: float) -> tuple[float, float, float, bool]:
        while self.msgs and self.msgs[0][0] < now - self.s.window:
            self.msgs.popleft()
        n = len(self.msgs) if self.s.count_all else len({u for _, u, _ in self.msgs})
        rate = n / self.s.window
        baseline = sum(self.hist) / len(self.hist) if self.hist else 0.0
        threshold = max(self.s.min_rate, baseline * self.s.mult)
        ready = len(self.hist) >= self.s.warmup
        self.hist.append(rate)
        return rate, baseline, threshold, ready and rate >= threshold

    def top_word(self) -> str:
        c = collections.Counter()
        for _, _, text in self.msgs:
            c.update({w for w in text.split() if len(w) >= 2})
        if not c:
            return "spike"
        return re.sub(r"[^\w-]", "", c.most_common(1)[0][0])[:24] or "spike"


# ─────────────────────────────────── Motor ───────────────────────────────────
class Engine:
    def __init__(self, s: Settings, log: Callable[[str], None]):
        self.s = s
        self._log_cb = log
        self.channels = list(dict.fromkeys(normalize_channel(c) for c in s.channels if c.strip()))
        self.status = {ch: ChannelStatus() for ch in self.channels}
        self.out_dir = Path(s.out_dir).expanduser()
        self.workdir = Path(tempfile.gettempdir()) / "twitch_clipper"
        self.ffmpeg = ffmpeg_path()
        self.recorders: dict[str, Recorder] = {}
        self.detectors: dict[str, SpikeDetector] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.running = False

    def log(self, msg: str) -> None:
        self._log_cb(f"[{datetime.now():%H:%M:%S}] {msg}")

    # API pública ------------------------------------------------------------
    def start(self) -> None:
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg não encontrado.")
        if not self.channels:
            raise RuntimeError("Adiciona pelo menos um canal.")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.running = True
        for ch in self.channels:
            self.detectors[ch] = SpikeDetector(self.s)
            self.recorders[ch] = Recorder(ch, self.s, self.workdir, self.ffmpeg, self.log, self.status[ch])
            self.recorders[ch].start()
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.thread.start()
        self.log(f"a monitorizar {len(self.channels)} canal(is) — clips em {self.out_dir}")

    def stop(self) -> None:
        self.running = False
        for r in self.recorders.values():
            r.stop()
        if self.loop:
            self.loop.call_soon_threadsafe(self._cancel_all)
        for st in self.status.values():
            st.state = "parado"
        self.log("parado.")

    def clip_now(self, ch: str) -> None:
        if self.loop and ch in self.recorders:
            asyncio.run_coroutine_threadsafe(self._save(ch, datetime.now(), "manual", 0, 0, 0), self.loop)

    # Interno ----------------------------------------------------------------
    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        tasks = [self.loop.create_task(self._chat())]
        tasks += [self.loop.create_task(self._monitor(ch)) for ch in self.channels]
        try:
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        finally:
            self.loop.close()

    def _cancel_all(self) -> None:
        for t in asyncio.all_tasks(self.loop):
            t.cancel()

    async def _chat(self) -> None:
        backoff = 1
        while self.running:
            writer = None
            try:
                reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)
                writer.write(f"NICK justinfan{random.randint(10000, 99999)}\r\n".encode())
                for ch in self.channels:
                    writer.write(f"JOIN #{ch}\r\n".encode())
                await writer.drain()
                self.log(f"chat ligado: {', '.join(self.channels)}")
                backoff = 1
                while self.running:
                    raw = await asyncio.wait_for(reader.readline(), timeout=360)
                    if not raw:
                        raise ConnectionError("ligação fechada")
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("PING"):
                        writer.write(f"PONG{line[4:]}\r\n".encode())
                        await writer.drain()
                        continue
                    m = PRIVMSG.match(line)
                    if m:
                        user, ch, text = m.group(1).lower(), m.group(2).lower(), m.group(3)
                        if user not in BOTS and ch in self.detectors:
                            self.detectors[ch].add(user, text)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self.log(f"chat caiu ({e}) — a religar em {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                if writer:
                    writer.close()

    async def _monitor(self, ch: str) -> None:
        det, rec, st = self.detectors[ch], self.recorders[ch], self.status[ch]
        last_clip = 0.0
        while self.running:
            await asyncio.sleep(1)
            now = time.time()
            rate, base, thr, spike = det.tick(now)
            st.rate, st.baseline, st.threshold = rate, base, thr
            if spike and now - last_clip >= self.s.cooldown and rec.live:
                last_clip = now
                word = det.top_word()
                self.log(f"[{ch}] 🔥 pico: {rate:.2f}/s (média {base:.2f}/s) — \"{word}\"")
                asyncio.create_task(self._save(ch, datetime.now(), word, rate, base, self.s.post_roll))

    async def _save(self, ch: str, stamp: datetime, word: str, rate: float, base: float, delay: int) -> None:
        await asyncio.sleep(delay)
        rec = self.recorders[ch]
        dest = self.out_dir / ch
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{ch}_{stamp:%Y%m%d_%H%M%S}_{word}.mp4"
        ok = await asyncio.to_thread(rec.save_clip, out, self.s.clip_len)
        if not ok:
            self.log(f"[{ch}] ⚠ sem vídeo no buffer (live offline?) — clip ignorado")
            return
        self.status[ch].clips += 1
        self.log(f"[{ch}] 💾 clip guardado: {out.name}")
        logfile = self.out_dir / "clips_log.csv"
        new = not logfile.exists()
        with open(logfile, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["data_hora", "canal", "taxa_pico", "media", "palavra", "ficheiro"])
            w.writerow([f"{stamp:%Y-%m-%d %H:%M:%S}", ch, f"{rate:.2f}", f"{base:.2f}", word, str(out)])
