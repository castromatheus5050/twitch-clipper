"""Twitch Clipper — interface gráfica."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clipper_core import Engine, Settings, ffmpeg_path, normalize_channel

APP_NAME = "Twitch Clipper"
CONFIG = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "TwitchClipper" / "config.json"
QUALITIES = ["best", "1080p60", "720p60", "720p", "480p"]


def load_settings() -> Settings:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        return Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
    except Exception:  # noqa: BLE001
        return Settings()


def save_settings(s: Settings) -> None:
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(asdict(s), indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("760x640")
        self.minsize(680, 560)
        self.s = load_settings()
        self.engine: Engine | None = None
        self.logq: queue.Queue[str] = queue.Queue()
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(400, self._tick)
        if not ffmpeg_path():
            self._log("⚠ ffmpeg não encontrado — os clips não vão funcionar.")

    # ── Layout ──────────────────────────────────────────────────────────────
    def _style(self) -> None:
        st = ttk.Style(self)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        elif "clam" in st.theme_names():
            st.theme_use("clam")
        st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 8))
        st.configure("Treeview", rowheight=24)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        # Canais
        fc = ttk.LabelFrame(self, text="Canais")
        fc.pack(fill="both", expand=False, **pad)
        row = ttk.Frame(fc)
        row.pack(fill="x", padx=8, pady=(8, 4))
        self.ch_entry = ttk.Entry(row)
        self.ch_entry.pack(side="left", fill="x", expand=True)
        self.ch_entry.bind("<Return>", lambda e: self._add_channel())
        self.btn_add = ttk.Button(row, text="Adicionar", command=self._add_channel)
        self.btn_add.pack(side="left", padx=(6, 0))
        self.btn_rm = ttk.Button(row, text="Remover", command=self._remove_channel)
        self.btn_rm.pack(side="left", padx=(6, 0))

        cols = ("estado", "chat", "media", "limiar", "clips")
        self.tree = ttk.Treeview(fc, columns=cols, height=5, selectmode="browse")
        self.tree.heading("#0", text="Canal")
        for c, t, w in zip(cols, ("Estado", "Chat /s", "Média /s", "Limiar /s", "Clips"),
                           (110, 80, 80, 80, 60)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center", stretch=False)
        self.tree.column("#0", width=180)
        self.tree.pack(fill="x", padx=8, pady=4)
        for ch in self.s.channels:
            self._insert_channel(ch)
        self.btn_clip = ttk.Button(fc, text="✂ Clip agora (canal selecionado)", command=self._clip_now,
                                   state="disabled")
        self.btn_clip.pack(anchor="e", padx=8, pady=(0, 8))

        # Pasta
        fo = ttk.LabelFrame(self, text="Pasta dos clips")
        fo.pack(fill="x", **pad)
        self.out_var = tk.StringVar(value=self.s.out_dir)
        ttk.Entry(fo, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(fo, text="Escolher…", command=self._pick_folder).pack(side="left")
        ttk.Button(fo, text="Abrir", command=lambda: open_folder(Path(self.out_var.get()))).pack(
            side="left", padx=8)

        # Definições
        fs = ttk.LabelFrame(self, text="Definições")
        fs.pack(fill="x", **pad)
        self.v_len = tk.IntVar(value=self.s.clip_len)
        self.v_post = tk.IntVar(value=self.s.post_roll)
        self.v_mult = tk.DoubleVar(value=self.s.mult)
        self.v_min = tk.DoubleVar(value=self.s.min_rate)
        self.v_cool = tk.IntVar(value=self.s.cooldown)
        self.v_q = tk.StringVar(value=self.s.quality)
        self.v_oauth = tk.StringVar(value=self.s.oauth)

        def spin(parent, r, c, label, var, frm, to, inc=1.0, tip=""):
            ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=(8, 4), pady=4)
            sp = ttk.Spinbox(parent, from_=frm, to=to, increment=inc, textvariable=var, width=7)
            sp.grid(row=r, column=c + 1, sticky="w", pady=4)
            if tip:
                ttk.Label(parent, text=tip, foreground="#777").grid(row=r, column=c + 2, sticky="w", padx=(4, 14))
            return sp

        self.inputs = [
            spin(fs, 0, 0, "Duração do clip", self.v_len, 5, 120, 5, "s"),
            spin(fs, 0, 3, "Esperar após o pico", self.v_post, 0, 30, 1, "s"),
            spin(fs, 1, 0, "Mínimo de chatters", self.v_min, 0.1, 50, 0.1, "/s"),
            spin(fs, 1, 3, "Intervalo entre clips", self.v_cool, 5, 600, 5, "s"),
        ]
        ttk.Label(fs, text="Sensibilidade").grid(row=2, column=0, sticky="w", padx=(8, 4), pady=4)
        sens = ttk.Frame(fs)
        sens.grid(row=2, column=1, columnspan=5, sticky="we", pady=4)
        ttk.Label(sens, text="mais clips").pack(side="left")
        self.scale = ttk.Scale(sens, from_=1.5, to=8.0, variable=self.v_mult, length=260,
                               command=lambda v: self.mult_lbl.config(text=f"pico = {float(v):.1f}× a média"))
        self.scale.pack(side="left", padx=6)
        ttk.Label(sens, text="menos clips").pack(side="left")
        self.mult_lbl = ttk.Label(sens, text=f"pico = {self.s.mult:.1f}× a média", foreground="#777")
        self.mult_lbl.pack(side="left", padx=10)

        ttk.Label(fs, text="Qualidade").grid(row=3, column=0, sticky="w", padx=(8, 4), pady=4)
        self.q_box = ttk.Combobox(fs, values=QUALITIES, textvariable=self.v_q, width=10)
        self.q_box.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(fs, text="Token OAuth (opcional)").grid(row=3, column=3, sticky="w", padx=(8, 4), pady=4)
        self.oauth_entry = ttk.Entry(fs, textvariable=self.v_oauth, show="•", width=22)
        self.oauth_entry.grid(row=3, column=4, columnspan=2, sticky="w", pady=(4, 8))
        self.inputs += [self.scale, self.q_box, self.oauth_entry]

        # Botão principal
        fb = ttk.Frame(self)
        fb.pack(fill="x", padx=10, pady=(4, 0))
        self.btn_start = ttk.Button(fb, text="▶  Iniciar", style="Big.TButton", command=self._toggle)
        self.btn_start.pack(side="left")
        self.status_lbl = ttk.Label(fb, text="Parado", foreground="#777")
        self.status_lbl.pack(side="left", padx=12)

        # Registo
        fl = ttk.LabelFrame(self, text="Registo")
        fl.pack(fill="both", expand=True, **pad)
        self.log_txt = tk.Text(fl, height=8, wrap="word", state="disabled", relief="flat",
                               font=("Consolas", 9) if os.name == "nt" else ("Monospace", 9))
        sb = ttk.Scrollbar(fl, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=6)
        self.log_txt.pack(fill="both", expand=True, padx=(8, 0), pady=6)

    # ── Ações ──────────────────────────────────────────────────────────────
    def _insert_channel(self, ch: str) -> None:
        ch = normalize_channel(ch)
        if ch and not self.tree.exists(ch):
            self.tree.insert("", "end", iid=ch, text=ch, values=("parado", "–", "–", "–", 0))

    def _add_channel(self) -> None:
        for part in self.ch_entry.get().replace(",", " ").split():
            self._insert_channel(part)
        self.ch_entry.delete(0, "end")

    def _remove_channel(self) -> None:
        for iid in self.tree.selection():
            self.tree.delete(iid)

    def _pick_folder(self) -> None:
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if d:
            self.out_var.set(d)

    def _collect(self) -> Settings:
        s = Settings(**asdict(self.s))
        s.channels = list(self.tree.get_children())
        s.out_dir = self.out_var.get().strip()
        try:
            s.clip_len = max(5, int(self.v_len.get()))
            s.post_roll = max(0, min(int(self.v_post.get()), s.clip_len - 1))
            s.mult = round(float(self.v_mult.get()), 1)
            s.min_rate = max(0.1, float(self.v_min.get()))
            s.cooldown = max(5, int(self.v_cool.get()))
        except (tk.TclError, ValueError):
            raise ValueError("Há um valor inválido nas definições.") from None
        s.quality = self.v_q.get().strip() or "best"
        s.oauth = self.v_oauth.get().strip()
        return s

    def _toggle(self) -> None:
        if self.engine and self.engine.running:
            self.engine.stop()
            self.engine = None
            self._set_running(False)
            return
        try:
            s = self._collect()
            save_settings(s)
            self.s = s
            eng = Engine(s, self.logq.put)
            eng.start()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(e))
            return
        self.engine = eng
        self._set_running(True)

    def _set_running(self, on: bool) -> None:
        self.btn_start.config(text="■  Parar" if on else "▶  Iniciar")
        self.status_lbl.config(text="A monitorizar…" if on else "Parado",
                               foreground="#1a7f37" if on else "#777")
        flag = ["disabled"] if on else ["!disabled"]
        for w in (self.btn_add, self.btn_rm, self.ch_entry, *self.inputs):
            w.state(flag)
        self.btn_clip.state(["!disabled"] if on else ["disabled"])
        if not on:
            for iid in self.tree.get_children():
                vals = list(self.tree.item(iid, "values"))
                self.tree.item(iid, values=("parado", "–", "–", "–", vals[4]))

    def _clip_now(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Seleciona um canal na lista.")
            return
        if self.engine:
            self.engine.clip_now(sel[0])
            self._log(f"[{sel[0]}] ✂ clip manual pedido")

    def _log(self, line: str) -> None:
        self.log_txt.config(state="normal")
        self.log_txt.insert("end", line + "\n")
        if int(self.log_txt.index("end-1c").split(".")[0]) > 1000:
            self.log_txt.delete("1.0", "200.0")
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    def _tick(self) -> None:
        while True:
            try:
                self._log(self.logq.get_nowait())
            except queue.Empty:
                break
        if self.engine and self.engine.running:
            for ch, st in self.engine.status.items():
                if self.tree.exists(ch):
                    self.tree.item(ch, values=(st.state, f"{st.rate:.2f}", f"{st.baseline:.2f}",
                                               f"{st.threshold:.2f}", st.clips))
        self.after(500, self._tick)

    def _on_close(self) -> None:
        try:
            save_settings(self._collect())
        except Exception:  # noqa: BLE001
            pass
        if self.engine and self.engine.running:
            self.engine.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
