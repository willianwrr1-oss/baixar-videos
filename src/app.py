"""Baixador de Vídeos — interface (customtkinter)."""
from __future__ import annotations

import os
import sys

# App sem console: garante que sys.stdout/stderr existam para as bibliotecas.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import subprocess

# No Windows, impede que o ffmpeg/deno abram janelas de console piscando.
if os.name == "nt":
    _orig_popen_init = subprocess.Popen.__init__

    def _popen_init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
        _orig_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_init

import json
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import core

APP_NAME = "Baixador de Vídeos"

# Paleta
BG = "#12141A"
CARD = "#1A1D25"
CARD2 = "#222632"
BORDER = "#2D3340"
FG = "#E8EAF0"
MUTED = "#8B93A5"
ACCENT = "#4F8CFF"
ACCENT_H = "#3A76E6"
OK = "#3DD68C"
ERR = "#FF6B6B"
ROW_ALT = "#1E222C"

CHECK_ON, CHECK_OFF = "☑", "☐"
LABEL_VIDEO, LABEL_AUDIO = "Vídeo (MP4)", "Música (MP3)"

CONFIG_FILE = Path(os.environ.get("APPDATA", Path.home())) / "BaixadorDeVideos" / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def default_dest() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=BG)
        self.title(APP_NAME)
        self.geometry("1040x740")
        self.minsize(900, 640)

        self.cfg = load_config()
        self.msgs: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.items: dict[str, core.VideoItem] = {}
        self.checked: set[str] = set()
        self.mode: str | None = None  # None | "analyze" | "download"
        self.dest = Path(self.cfg.get("dest") or default_dest())
        self.kind_label = self.cfg.get("kind", LABEL_VIDEO)
        if self.kind_label not in (LABEL_VIDEO, LABEL_AUDIO):
            self.kind_label = LABEL_VIDEO

        self._build_ui()
        self._apply_kind(self.kind_label, restore=self.cfg.get("quality"))
        self._set_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll)
        self.entry.focus_set()

    # ------------------------------------------------------------------ UI --
    def _set_icon(self) -> None:
        path = core.bundle_dir() / "assets" / "icon.ico"
        if not path.exists():
            path = core.bundle_dir().parent / "assets" / "icon.ico"
        if path.exists() and os.name == "nt":
            # o customtkinter redefine o ícone ~200 ms após abrir; reaplicamos depois
            self.after(300, lambda: self._try_icon(str(path)))

    def _try_icon(self, path: str) -> None:
        try:
            self.iconbitmap(path)
        except Exception:
            pass

    def _scale(self) -> float:
        try:
            return float(self._get_window_scaling())
        except Exception:
            return 1.0

    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)

    def _build_ui(self) -> None:
        pad = 24
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Cabeçalho
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=pad, pady=(20, 10))
        ctk.CTkLabel(head, text=APP_NAME, font=self._font(26, "bold"), text_color=FG).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text="Cole um link, escolha os vídeos e baixe em MP4 ou MP3.",
            font=self._font(13),
            text_color=MUTED,
        ).pack(anchor="w")

        # Cartão do link
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=pad, pady=(6, 10))
        card.grid_columnconfigure(0, weight=1)
        self.url_var = tk.StringVar()
        self.entry = ctk.CTkEntry(
            card,
            textvariable=self.url_var,
            height=44,
            font=self._font(14),
            placeholder_text="Cole aqui o link (vídeo, playlist, canal ou página com vídeos)…",
            fg_color=CARD2,
            border_color=BORDER,
            text_color=FG,
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=16)
        self.entry.bind("<Return>", lambda _e: self.on_analyze())
        self.btn_paste = ctk.CTkButton(
            card, text="Colar", width=90, height=44, font=self._font(14),
            fg_color=CARD2, hover_color=BORDER, text_color=FG, command=self.on_paste,
        )
        self.btn_paste.grid(row=0, column=1, padx=4, pady=16)
        self.btn_analyze = ctk.CTkButton(
            card, text="Verificar", width=130, height=44, font=self._font(14, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_H, command=self.on_analyze,
        )
        self.btn_analyze.grid(row=0, column=2, padx=(4, 16), pady=16)

        # Cartão da lista
        lst = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        lst.grid(row=2, column=0, sticky="nsew", padx=pad, pady=(0, 10))
        lst.grid_columnconfigure(0, weight=1)
        lst.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(lst, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        self.lbl_found = ctk.CTkLabel(bar, text="Vídeos encontrados", font=self._font(14, "bold"), text_color=FG)
        self.lbl_found.pack(side="left")
        self.btn_none = ctk.CTkButton(
            bar, text="Desmarcar", width=90, height=28, font=self._font(12),
            fg_color="transparent", hover_color=CARD2, text_color=MUTED,
            command=lambda: self._set_all(False),
        )
        self.btn_none.pack(side="right")
        self.btn_all = ctk.CTkButton(
            bar, text="Marcar todos", width=100, height=28, font=self._font(12),
            fg_color="transparent", hover_color=CARD2, text_color=MUTED,
            command=lambda: self._set_all(True),
        )
        self.btn_all.pack(side="right", padx=(0, 4))
        self.lbl_sel = ctk.CTkLabel(bar, text="", font=self._font(12), text_color=MUTED)
        self.lbl_sel.pack(side="right", padx=12)

        wrap = ctk.CTkFrame(lst, fg_color=CARD2, corner_radius=8)
        wrap.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)
        self._style_tree()
        s = self._scale()
        self.tree = ttk.Treeview(
            wrap, style="Vid.Treeview", show="headings", selectmode="none",
            columns=("sel", "title", "dur", "channel", "status"),
        )
        self.tree.heading("sel", text=CHECK_ON, command=self._toggle_all)
        self.tree.heading("title", text="Título", anchor="w")
        self.tree.heading("dur", text="Duração")
        self.tree.heading("channel", text="Canal", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("sel", width=int(52 * s), anchor="center", stretch=False)
        self.tree.column("title", width=int(400 * s), anchor="w", stretch=True)
        self.tree.column("dur", width=int(90 * s), anchor="center", stretch=False)
        self.tree.column("channel", width=int(180 * s), anchor="w", stretch=False)
        self.tree.column("status", width=int(180 * s), anchor="w", stretch=False)
        self.tree.tag_configure("even", background=CARD2)
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("done", foreground=OK)
        self.tree.tag_configure("error", foreground=ERR)
        self.tree.tag_configure("active", foreground=ACCENT)
        self.tree.bind("<Button-1>", self._on_tree_click)
        sb = ctk.CTkScrollbar(wrap, command=self.tree.yview, fg_color=CARD2, button_color=BORDER)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 3), pady=4)
        self.empty = ctk.CTkLabel(
            wrap,
            text="Cole um link acima e clique em Verificar\npara listar os vídeos disponíveis.",
            font=self._font(14), text_color=MUTED, fg_color=CARD2, justify="center",
        )
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

        # Cartão de opções
        opt = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        opt.grid(row=3, column=0, sticky="ew", padx=pad, pady=(0, 10))
        opt.grid_columnconfigure(5, weight=1)
        lbl = dict(font=self._font(13), text_color=MUTED)
        ctk.CTkLabel(opt, text="Formato", **lbl).grid(row=0, column=0, padx=(16, 8), pady=(14, 6), sticky="w")
        self.seg_kind = ctk.CTkSegmentedButton(
            opt, values=[LABEL_VIDEO, LABEL_AUDIO], command=self._apply_kind, height=36,
            font=self._font(13), selected_color=ACCENT, selected_hover_color=ACCENT_H,
            unselected_color=CARD2, unselected_hover_color=BORDER, fg_color=CARD2,
        )
        self.seg_kind.grid(row=0, column=1, pady=(14, 6), sticky="w")
        ctk.CTkLabel(opt, text="Qualidade", **lbl).grid(row=0, column=2, padx=(24, 8), pady=(14, 6), sticky="w")
        self.quality_var = tk.StringVar()
        self.menu_quality = ctk.CTkOptionMenu(
            opt, variable=self.quality_var, values=list(core.VIDEO_QUALITIES), width=170, height=36,
            font=self._font(13), dropdown_font=self._font(13), fg_color=CARD2, button_color=BORDER,
            button_hover_color=ACCENT, dropdown_fg_color=CARD2, dropdown_hover_color=BORDER, text_color=FG,
        )
        self.menu_quality.grid(row=0, column=3, pady=(14, 6), sticky="w")

        ctk.CTkLabel(opt, text="Salvar em", **lbl).grid(row=1, column=0, padx=(16, 8), pady=(6, 14), sticky="w")
        self.dest_var = tk.StringVar(value=str(self.dest))
        ctk.CTkEntry(
            opt, textvariable=self.dest_var, state="readonly", height=36, font=self._font(13),
            fg_color=CARD2, border_color=BORDER, text_color=FG,
        ).grid(row=1, column=1, columnspan=5, sticky="ew", pady=(6, 14), padx=(0, 8))
        ctk.CTkButton(
            opt, text="Alterar…", width=90, height=36, font=self._font(13), fg_color=CARD2,
            hover_color=BORDER, text_color=FG, command=self.on_choose_dest,
        ).grid(row=1, column=6, pady=(6, 14), padx=4)
        ctk.CTkButton(
            opt, text="Abrir pasta", width=100, height=36, font=self._font(13), fg_color=CARD2,
            hover_color=BORDER, text_color=FG, command=self.on_open_dest,
        ).grid(row=1, column=7, pady=(6, 14), padx=(4, 16))

        # Rodapé
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=pad, pady=(0, 20))
        foot.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(foot, height=10, fg_color=CARD2, progress_color=ACCENT)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.progress.set(0)
        self.lbl_status = ctk.CTkLabel(
            foot, text="Pronto.", anchor="w", justify="left", wraplength=620,
            font=self._font(13), text_color=MUTED,
        )
        self.lbl_status.grid(row=1, column=0, sticky="w")
        btns = ctk.CTkFrame(foot, fg_color="transparent")
        btns.grid(row=1, column=1, sticky="e")
        self.btn_cancel = ctk.CTkButton(
            btns, text="Cancelar", width=110, height=44, font=self._font(14), state="disabled",
            fg_color="transparent", border_width=1, border_color=ERR, text_color=ERR,
            hover_color=CARD2, command=self.on_cancel,
        )
        self.btn_cancel.pack(side="left", padx=(0, 10))
        self.btn_download = ctk.CTkButton(
            btns, text="Baixar selecionados", width=210, height=44, font=self._font(15, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_H, command=self.on_download,
        )
        self.btn_download.pack(side="left")

    def _style_tree(self) -> None:
        s = self._scale()
        style = ttk.Style(self)
        style.theme_use("clam")
        style.layout("Vid.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Vid.Treeview", background=CARD2, fieldbackground=CARD2, foreground=FG,
            borderwidth=0, rowheight=int(36 * s), font=("Segoe UI", 10),
        )
        style.configure(
            "Vid.Treeview.Heading", background=CARD, foreground=MUTED, relief="flat",
            borderwidth=0, padding=(8, 8), font=("Segoe UI", 10, "bold"),
        )
        style.map("Vid.Treeview.Heading", background=[("active", CARD)], foreground=[("active", FG)])

    # ------------------------------------------------------------- Estado --
    def _status(self, text: str, color: str = MUTED) -> None:
        self.lbl_status.configure(text=text, text_color=color)

    def _set_mode(self, mode: str | None) -> None:
        self.mode = mode
        idle = mode is None
        state = "normal" if idle else "disabled"
        for w in (self.btn_analyze, self.btn_paste, self.btn_download, self.btn_all, self.btn_none):
            w.configure(state=state)
        self.seg_kind.configure(state=state)
        self.menu_quality.configure(state=state)
        self.btn_cancel.configure(state="normal" if mode == "download" else "disabled")
        self.progress.stop()
        if mode == "analyze":
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.configure(mode="determinate")
            self.progress.set(0)

    def _apply_kind(self, label: str, restore: str | None = None) -> None:
        self.kind_label = label
        self.seg_kind.set(label)
        qualities = list(core.AUDIO_QUALITIES if label == LABEL_AUDIO else core.VIDEO_QUALITIES)
        self.menu_quality.configure(values=qualities)
        if restore in qualities:
            self.quality_var.set(restore)
        else:
            self.quality_var.set("192 kbps" if label == LABEL_AUDIO else qualities[0])

    # ------------------------------------------------------------- Lista ---
    def _tags(self, iid: str, extra: str = "") -> tuple:
        zebra = "odd" if int(iid) % 2 else "even"
        return (zebra, extra) if extra else (zebra,)

    def _clear_list(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.checked.clear()
        self.lbl_found.configure(text="Vídeos encontrados")
        self.lbl_sel.configure(text="")
        self.tree.heading("sel", text=CHECK_ON)
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def _fill_list(self, title: str, items: list, truncated: bool) -> None:
        self._clear_list()
        for i, it in enumerate(items):
            iid = str(i)
            self.items[iid] = it
            self.checked.add(iid)
            self.tree.insert(
                "", "end", iid=iid, tags=self._tags(iid),
                values=(CHECK_ON, it.title, core.format_duration(it.duration), it.channel, ""),
            )
        if items:
            self.empty.place_forget()
        head = f"{len(items)} vídeo(s) encontrado(s)"
        if title:
            head += f" · {title}"
        self.lbl_found.configure(text=head[:90])
        self._update_counter()
        if truncated:
            self._status(f"Lista limitada aos primeiros {core.MAX_ENTRIES} vídeos.", MUTED)

    def _update_counter(self) -> None:
        total = len(self.items)
        self.lbl_sel.configure(text=f"{len(self.checked)} de {total} selecionados" if total else "")
        self.tree.heading("sel", text=CHECK_ON if total and len(self.checked) == total else CHECK_OFF)

    def _toggle(self, iid: str) -> None:
        if iid in self.checked:
            self.checked.discard(iid)
        else:
            self.checked.add(iid)
        self.tree.set(iid, "sel", CHECK_ON if iid in self.checked else CHECK_OFF)
        self._update_counter()

    def _set_all(self, value: bool) -> None:
        if self.mode is not None:
            return
        for iid in self.items:
            if value:
                self.checked.add(iid)
            else:
                self.checked.discard(iid)
            self.tree.set(iid, "sel", CHECK_ON if value else CHECK_OFF)
        self._update_counter()

    def _toggle_all(self) -> None:
        self._set_all(len(self.checked) != len(self.items))

    def _on_tree_click(self, event) -> None:
        if self.mode is not None or self.tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    # ------------------------------------------------------------ Ações ----
    def on_paste(self) -> None:
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            self._status("A área de transferência está vazia.")
            return
        if text:
            self.url_var.set(text)
            self.on_analyze()

    def on_analyze(self) -> None:
        if self.mode is not None:
            return
        url = self.url_var.get().strip()
        if not url:
            self._status("Cole um link primeiro.", ERR)
            return
        if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
            url = "https://" + url
            self.url_var.set(url)
        self._clear_list()
        self._set_mode("analyze")
        self._status("Verificando o link… pode levar alguns segundos.")
        threading.Thread(target=self._analyze_worker, args=(url,), daemon=True).start()

    def _analyze_worker(self, url: str) -> None:
        try:
            title, items, truncated = core.analyze(url)
            self.msgs.put(("analyzed", title, items, truncated))
        except Exception as exc:  # noqa: BLE001
            self.msgs.put(("analyze_error", core.friendly_error(exc)))

    def on_choose_dest(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.dest), title="Escolha a pasta de destino")
        if chosen:
            self.dest = Path(chosen)
            self.dest_var.set(str(self.dest))

    def on_open_dest(self) -> None:
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(self.dest)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(self.dest)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Não foi possível abrir a pasta:\n{exc}")

    def on_download(self) -> None:
        if self.mode is not None:
            return
        jobs = [(iid, self.items[iid]) for iid in self.tree.get_children() if iid in self.checked]
        if not jobs:
            self._status("Marque pelo menos um vídeo para baixar.", ERR)
            return
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível usar a pasta de destino:\n{exc}")
            return
        kind = core.KIND_AUDIO if self.kind_label == LABEL_AUDIO else core.KIND_VIDEO
        quality = self.quality_var.get()
        for iid, _ in jobs:
            self.tree.set(iid, "status", "Na fila")
            self.tree.item(iid, tags=self._tags(iid))
        self.cancel.clear()
        self._set_mode("download")
        self._status(f"Iniciando {len(jobs)} download(s)…")
        threading.Thread(
            target=self._download_worker, args=(jobs, kind, quality, self.dest), daemon=True
        ).start()

    def on_cancel(self) -> None:
        self.cancel.set()
        self.btn_cancel.configure(state="disabled")
        self._status("Cancelando…")

    def _download_worker(self, jobs: list, kind: str, quality: str, dest: Path) -> None:
        ok = err = 0
        last_error = ""
        total = len(jobs)
        for n, (iid, item) in enumerate(jobs):
            if self.cancel.is_set():
                break
            self.msgs.put(("row", iid, "Iniciando…", "active"))

            def prog(pct: float, text: str, n=n, iid=iid, title=item.title) -> None:
                self.msgs.put(("progress", n, total, iid, pct, text, title))

            try:
                core.download_one(item.url, kind, quality, dest, prog, self.cancel)
                ok += 1
                self.msgs.put(("row", iid, "✔ Concluído", "done"))
            except Exception as exc:  # noqa: BLE001
                if self.cancel.is_set():
                    self.msgs.put(("row", iid, "Cancelado", ""))
                    break
                err += 1
                last_error = core.friendly_error(exc)
                self.msgs.put(("row", iid, "✖ Erro", "error"))
        self.msgs.put(("finished", ok, err, self.cancel.is_set(), last_error))

    # --------------------------------------------------------- Mensagens ---
    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.msgs.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "analyzed":
            _, title, items, truncated = msg
            self._set_mode(None)
            self._fill_list(title, items, truncated)
            if items:
                if not truncated:
                    self._status("Pronto. Marque os vídeos, escolha o formato e clique em Baixar.")
            else:
                self._status("Nenhum vídeo encontrado nesse link.", ERR)
        elif kind == "analyze_error":
            self._set_mode(None)
            self._status(msg[1], ERR)
        elif kind == "row":
            _, iid, text, tag = msg
            self.tree.set(iid, "status", text)
            self.tree.item(iid, tags=self._tags(iid, tag))
        elif kind == "progress":
            _, n, total, iid, pct, text, title = msg
            self.tree.set(iid, "status", text)
            self.tree.item(iid, tags=self._tags(iid, "active"))
            self.progress.set(min(1.0, (n + pct / 100) / total))
            self._status(f"Baixando {n + 1} de {total} — {title[:70]}")
        elif kind == "finished":
            _, ok, err, cancelled, last_error = msg
            self._set_mode(None)
            if cancelled:
                self._status(f"Cancelado. {ok} concluído(s).", MUTED)
            elif err == 0:
                self.progress.set(1)
                self._status(f"Tudo certo! {ok} arquivo(s) salvo(s) em {self.dest}", OK)
            else:
                self._status(f"{ok} concluído(s), {err} com erro — {last_error}", ERR)

    def _on_close(self) -> None:
        if self.mode == "download" and not messagebox.askyesno(
            APP_NAME, "Há downloads em andamento. Deseja realmente sair?"
        ):
            return
        self.cancel.set()
        save_config({"dest": str(self.dest), "kind": self.kind_label, "quality": self.quality_var.get()})
        self.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == "__main__":
    main()
