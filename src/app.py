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

import ctypes
import json
import queue
import re
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

import customtkinter as ctk
from PIL import ImageTk

import core
import notify

APP_NAME = "Baixador de Vídeos"
APP_VERSION = "1.0.0"
APP_UPDATED = "15/11/2026"
APP_COMPAT = "Windows 10 ou superior"
APP_AUTHOR = "Willian R Rocha"
APP_EMAIL = "willian.wrr1@gmail.com"
APP_DESC = (
    "Baixe vídeos e músicas a partir de um link: o app lista os vídeos encontrados, "
    "você escolhe quais quer e o formato (MP4 ou MP3). Funciona sem instalação."
)
APP_PRIVACY = (
    "O aplicativo não coleta nem envia dados pessoais. A internet é usada apenas para "
    "listar e baixar os vídeos dos links que você informar. Suas preferências ficam "
    "somente neste computador."
)

THEMES = {
    "dark": {
        "bg": "#12141A", "card": "#1A1D25", "card2": "#222632", "border": "#2D3340",
        "fg": "#E8EAF0", "muted": "#8B93A5", "accent": "#4F8CFF", "accent_h": "#3A76E6",
        "ok": "#3DD68C", "err": "#FF6B6B", "row_alt": "#1E222C",
    },
    "light": {
        "bg": "#F2F4F8", "card": "#FFFFFF", "card2": "#EEF1F6", "border": "#D5DAE3",
        "fg": "#1B1F2A", "muted": "#667085", "accent": "#2F6FED", "accent_h": "#2459C4",
        "ok": "#1E9E60", "err": "#D64545", "row_alt": "#F6F8FB",
    },
}

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
        self.cfg = load_config()
        self.theme = self.cfg.get("theme", "dark")
        if self.theme not in THEMES:
            self.theme = "dark"
        self.P = THEMES[self.theme]
        ctk.set_appearance_mode(self.theme)
        super().__init__(fg_color=self.P["bg"])
        self.title(APP_NAME)
        self.geometry("1040x760")
        self.minsize(900, 660)

        self._themed: list[tuple] = []  # (widget, {opção: chave_da_paleta})
        self.msgs: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.items: dict[str, core.VideoItem] = {}
        self.checked: set[str] = set()
        self.gen = 0  # "geração" da lista: descarta miniaturas de listas antigas
        self.thumbs: dict[str, ImageTk.PhotoImage] = {}
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.mode: str | None = None  # None | "analyze" | "download"
        self.dest = Path(self.cfg.get("dest") or default_dest())
        self.kind_label = self.cfg.get("kind", LABEL_VIDEO)
        if self.kind_label not in (LABEL_VIDEO, LABEL_AUDIO):
            self.kind_label = LABEL_VIDEO

        self._build_ui()
        self._apply_theme()
        self._apply_kind(self.kind_label, restore=self.cfg.get("quality"))
        self._set_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll)
        self.entry.focus_set()

    # ------------------------------------------------------------- Tema ----
    def T(self, widget, **opts):
        """Registra o widget para ser repintado quando o tema mudar e aplica as cores atuais."""
        self._themed.append((widget, opts))
        widget.configure(**{k: self.P.get(v, v) for k, v in opts.items()})
        return widget

    def _apply_theme(self) -> None:
        P = self.P
        ctk.set_appearance_mode(self.theme)
        self.configure(fg_color=P["bg"])
        for widget, opts in self._themed:
            try:
                widget.configure(**{k: P.get(v, v) for k, v in opts.items()})
            except Exception:
                pass
        self._style_tree()
        self.btn_theme.configure(text="Tema claro" if self.theme == "dark" else "Tema escuro")

    def on_toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self.P = THEMES[self.theme]
        self._apply_theme()

    def _style_tree(self) -> None:
        P = self.P
        s = self._scale()
        style = ttk.Style(self)
        style.theme_use("clam")
        style.layout("Vid.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Vid.Treeview", background=P["card2"], fieldbackground=P["card2"], foreground=P["fg"],
            borderwidth=0, rowheight=int(54 * s), font=("Segoe UI", 10),
        )
        style.configure(
            "Vid.Treeview.Heading", background=P["card"], foreground=P["muted"], relief="flat",
            borderwidth=0, padding=(8, 8), font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Vid.Treeview.Heading",
            background=[("active", P["card"])], foreground=[("active", P["fg"])],
        )
        if hasattr(self, "tree"):
            self.tree.tag_configure("even", background=P["card2"])
            self.tree.tag_configure("odd", background=P["row_alt"])
            self.tree.tag_configure("done", foreground=P["ok"])
            self.tree.tag_configure("error", foreground=P["err"])
            self.tree.tag_configure("active", foreground=P["accent"])

    # ------------------------------------------------------------ Ícone ----
    def _set_icon(self, window) -> None:
        path = core.bundle_dir() / "assets" / "icon.ico"
        if not path.exists():
            path = core.bundle_dir().parent / "assets" / "icon.ico"
        if path.exists() and os.name == "nt":
            # o customtkinter redefine o ícone ~200 ms após abrir; reaplicamos depois
            window.after(300, lambda: self._try_icon(window, str(path)))

    @staticmethod
    def _try_icon(window, path: str) -> None:
        try:
            window.iconbitmap(path)
        except Exception:
            pass

    def _scale(self) -> float:
        try:
            return float(self._get_window_scaling())
        except Exception:
            return 1.0

    def _font(self, size: int, weight: str = "normal", underline: bool = False) -> ctk.CTkFont:
        return ctk.CTkFont(family="Segoe UI", size=size, weight=weight, underline=underline)

    # ------------------------------------------------------------- UI -------
    def _build_ui(self) -> None:
        T = self.T
        pad = 24
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Cabeçalho
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=pad, pady=(20, 10))
        head.grid_columnconfigure(0, weight=1)
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        T(ctk.CTkLabel(titles, text=APP_NAME, font=self._font(26, "bold")), text_color="fg").pack(anchor="w")
        T(
            ctk.CTkLabel(titles, text="Cole um link, escolha os vídeos e baixe em MP4 ou MP3.", font=self._font(13)),
            text_color="muted",
        ).pack(anchor="w")
        tools = ctk.CTkFrame(head, fg_color="transparent")
        tools.grid(row=0, column=1, sticky="e")
        self.btn_about = T(
            ctk.CTkButton(
                tools, text="Sobre", width=70, height=32, font=self._font(13, underline=True),
                fg_color="transparent", command=self.on_about,
            ),
            text_color="accent", hover_color="card2",
        )
        self.btn_about.pack(side="left", padx=(0, 6))
        self.btn_theme = T(
            ctk.CTkButton(
                tools, text="Tema claro", width=110, height=32, font=self._font(13),
                border_width=1, fg_color="transparent", command=self.on_toggle_theme,
            ),
            text_color="fg", border_color="border", hover_color="card2",
        )
        self.btn_theme.pack(side="left")

        # Cartão do link
        card = T(ctk.CTkFrame(self, corner_radius=14, border_width=1), fg_color="card", border_color="border")
        card.grid(row=1, column=0, sticky="ew", padx=pad, pady=(6, 10))
        card.grid_columnconfigure(0, weight=1)
        self.url_var = tk.StringVar()
        self.entry = T(
            ctk.CTkEntry(
                card, textvariable=self.url_var, height=44, font=self._font(14),
                placeholder_text="Cole aqui o link (vídeo, playlist, canal ou página com vídeos)…",
            ),
            fg_color="card2", border_color="border", text_color="fg", placeholder_text_color="muted",
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=16)
        self.entry.bind("<Return>", lambda _e: self.on_analyze())
        self.btn_paste = T(
            ctk.CTkButton(card, text="Colar", width=84, height=44, font=self._font(14), command=self.on_paste),
            fg_color="card2", hover_color="border", text_color="fg",
        )
        self.btn_paste.grid(row=0, column=1, padx=3, pady=16)
        self.btn_clear = T(
            ctk.CTkButton(card, text="Limpar", width=84, height=44, font=self._font(14), command=self.on_clear),
            fg_color="card2", hover_color="border", text_color="fg",
        )
        self.btn_clear.grid(row=0, column=2, padx=3, pady=16)
        self.btn_analyze = T(
            ctk.CTkButton(
                card, text="Verificar", width=130, height=44, font=self._font(14, "bold"), command=self.on_analyze
            ),
            fg_color="accent", hover_color="accent_h", text_color="#FFFFFF",
        )
        self.btn_analyze.grid(row=0, column=3, padx=(3, 16), pady=16)

        # Cartão da lista
        lst = T(ctk.CTkFrame(self, corner_radius=14, border_width=1), fg_color="card", border_color="border")
        lst.grid(row=2, column=0, sticky="nsew", padx=pad, pady=(0, 10))
        lst.grid_columnconfigure(0, weight=1)
        lst.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(lst, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        self.lbl_found = T(
            ctk.CTkLabel(bar, text="Vídeos encontrados", font=self._font(14, "bold")), text_color="fg"
        )
        self.lbl_found.pack(side="left")
        self.btn_none = T(
            ctk.CTkButton(
                bar, text="Desmarcar", width=90, height=28, font=self._font(12),
                fg_color="transparent", command=lambda: self._set_all(False),
            ),
            hover_color="card2", text_color="muted",
        )
        self.btn_none.pack(side="right")
        self.btn_all = T(
            ctk.CTkButton(
                bar, text="Marcar todos", width=100, height=28, font=self._font(12),
                fg_color="transparent", command=lambda: self._set_all(True),
            ),
            hover_color="card2", text_color="muted",
        )
        self.btn_all.pack(side="right", padx=(0, 4))
        self.lbl_sel = T(ctk.CTkLabel(bar, text="", font=self._font(12)), text_color="muted")
        self.lbl_sel.pack(side="right", padx=12)

        wrap = T(ctk.CTkFrame(lst, corner_radius=8), fg_color="card2")
        wrap.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)
        s = self._scale()
        self.tree = ttk.Treeview(
            wrap, style="Vid.Treeview", show="tree headings", selectmode="none",
            columns=("sel", "title", "dur", "channel", "status"),
        )
        self.thumb_size = (int(80 * s), int(45 * s))
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=int(96 * s), anchor="center", stretch=False)
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
        self.tree.bind("<Button-1>", self._on_tree_click)
        sb = T(ctk.CTkScrollbar(wrap, command=self.tree.yview), fg_color="card2", button_color="border",
               button_hover_color="muted")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 3), pady=4)
        self.empty = T(
            ctk.CTkLabel(
                wrap,
                text="Cole um link acima e clique em Verificar\npara listar os vídeos disponíveis.",
                font=self._font(14), justify="center",
            ),
            text_color="muted", fg_color="card2",
        )
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

        # Cartão de opções
        opt = T(ctk.CTkFrame(self, corner_radius=14, border_width=1), fg_color="card", border_color="border")
        opt.grid(row=3, column=0, sticky="ew", padx=pad, pady=(0, 10))
        opt.grid_columnconfigure(5, weight=1)
        T(ctk.CTkLabel(opt, text="Formato", font=self._font(13)), text_color="muted").grid(
            row=0, column=0, padx=(16, 8), pady=(14, 6), sticky="w")
        self.seg_kind = T(
            ctk.CTkSegmentedButton(
                opt, values=[LABEL_VIDEO, LABEL_AUDIO], command=self._apply_kind, height=36, font=self._font(13),
            ),
            selected_color="accent", selected_hover_color="accent_h", unselected_color="card2",
            unselected_hover_color="border", fg_color="card2", text_color="fg",
        )
        self.seg_kind.grid(row=0, column=1, pady=(14, 6), sticky="w")
        T(ctk.CTkLabel(opt, text="Qualidade", font=self._font(13)), text_color="muted").grid(
            row=0, column=2, padx=(24, 8), pady=(14, 6), sticky="w")
        self.quality_var = tk.StringVar()
        self.menu_quality = T(
            ctk.CTkOptionMenu(
                opt, variable=self.quality_var, values=list(core.VIDEO_QUALITIES), width=170, height=36,
                font=self._font(13), dropdown_font=self._font(13),
            ),
            fg_color="card2", button_color="border", button_hover_color="accent", dropdown_fg_color="card2",
            dropdown_hover_color="border", dropdown_text_color="fg", text_color="fg",
        )
        self.menu_quality.grid(row=0, column=3, pady=(14, 6), sticky="w")

        T(ctk.CTkLabel(opt, text="Salvar em", font=self._font(13)), text_color="muted").grid(
            row=1, column=0, padx=(16, 8), pady=(6, 14), sticky="w")
        self.dest_var = tk.StringVar(value=str(self.dest))
        T(
            ctk.CTkEntry(opt, textvariable=self.dest_var, state="readonly", height=36, font=self._font(13)),
            fg_color="card2", border_color="border", text_color="fg",
        ).grid(row=1, column=1, columnspan=5, sticky="ew", pady=(6, 14), padx=(0, 8))
        T(
            ctk.CTkButton(opt, text="Alterar…", width=90, height=36, font=self._font(13),
                          command=self.on_choose_dest),
            fg_color="card2", hover_color="border", text_color="fg",
        ).grid(row=1, column=6, pady=(6, 14), padx=4)
        T(
            ctk.CTkButton(opt, text="Abrir pasta", width=100, height=36, font=self._font(13),
                          command=self.on_open_dest),
            fg_color="card2", hover_color="border", text_color="fg",
        ).grid(row=1, column=7, pady=(6, 14), padx=(4, 16))

        # Rodapé
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=pad, pady=(0, 20))
        foot.grid_columnconfigure(0, weight=1)
        self.progress = T(ctk.CTkProgressBar(foot, height=10), fg_color="card2", progress_color="accent")
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.progress.set(0)
        self.lbl_status = T(
            ctk.CTkLabel(foot, text="Pronto.", anchor="w", justify="left", wraplength=620, font=self._font(13)),
            text_color="muted",
        )
        self.lbl_status.grid(row=1, column=0, sticky="w")
        btns = ctk.CTkFrame(foot, fg_color="transparent")
        btns.grid(row=1, column=1, sticky="e")
        self.btn_cancel = T(
            ctk.CTkButton(
                btns, text="Cancelar", width=110, height=44, font=self._font(14), state="disabled",
                fg_color="transparent", border_width=1, command=self.on_cancel,
            ),
            border_color="err", text_color="err", hover_color="card2",
        )
        self.btn_cancel.pack(side="left", padx=(0, 10))
        self.btn_download = T(
            ctk.CTkButton(
                btns, text="Baixar selecionados", width=210, height=44, font=self._font(15, "bold"),
                command=self.on_download,
            ),
            fg_color="accent", hover_color="accent_h", text_color="#FFFFFF",
        )
        self.btn_download.pack(side="left")

    # ------------------------------------------------------------- Estado --
    def _status(self, text: str, color: str = "muted") -> None:
        self.lbl_status.configure(text=text, text_color=self.P.get(color, color))
        # a cor do status é pontual: sai do registro do tema para não ser repintada de volta
        self._themed = [(w, o) for (w, o) in self._themed if w is not self.lbl_status]
        if color == "muted":
            self._themed.append((self.lbl_status, {"text_color": "muted"}))
        else:
            self._themed.append((self.lbl_status, {"text_color": color}))

    def _set_mode(self, mode: str | None) -> None:
        self.mode = mode
        idle = mode is None
        state = "normal" if idle else "disabled"
        for w in (self.btn_analyze, self.btn_paste, self.btn_clear, self.btn_download, self.btn_all, self.btn_none):
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
        self.thumbs.clear()
        self.gen += 1
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
        for iid, it in self.items.items():
            if it.thumbnail:
                self.pool.submit(self._thumb_worker, self.gen, iid, it.thumbnail)
        head = f"{len(items)} vídeo(s) encontrado(s)"
        if title:
            head += f" · {title}"
        self.lbl_found.configure(text=head[:90])
        self._update_counter()
        if truncated:
            self._status(f"Lista limitada aos primeiros {core.MAX_ENTRIES} vídeos.")

    def _thumb_worker(self, gen: int, iid: str, url: str) -> None:
        if gen != self.gen:
            return
        img = core.fetch_thumbnail(url, self.thumb_size)
        if img is not None:
            self.msgs.put(("thumb", gen, iid, img))

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
        if self.mode is not None or self.tree.identify_region(event.x, event.y) not in ("cell", "tree"):
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

    def on_clear(self) -> None:
        """Limpa o campo do link e a lista de vídeos."""
        if self.mode is not None:
            return
        self.url_var.set("")
        self._clear_list()
        self.progress.set(0)
        self._status("Pronto.")
        self.entry.focus_set()

    def on_analyze(self) -> None:
        if self.mode is not None:
            return
        url = self.url_var.get().strip()
        if not url:
            self._status("Cole um link primeiro.", "err")
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
            self._status("Marque pelo menos um vídeo para baixar.", "err")
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

    # ------------------------------------------------------------- Sobre ----
    def on_about(self) -> None:
        P = self.P
        s = self._scale()
        w, h = 480, 650
        dlg = ctk.CTkToplevel(self, fg_color=P["card"])
        dlg.title("Sobre")
        dlg.resizable(False, False)
        dlg.transient(self)
        x = self.winfo_rootx() + max(0, (self.winfo_width() - int(w * s)) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - int(h * s)) // 2)
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        self._set_icon(dlg)

        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=(24, 18))
        body.grid_columnconfigure(0, weight=1)

        def separator(row: int) -> None:
            ctk.CTkFrame(body, height=1, fg_color=P["border"]).grid(row=row, column=0, sticky="ew", pady=14)

        def info_rows(row: int, rows: list[tuple[str, str]]) -> None:
            box = ctk.CTkFrame(body, fg_color="transparent")
            box.grid(row=row, column=0, sticky="ew")
            box.grid_columnconfigure(1, weight=1)
            for i, (label, value) in enumerate(rows):
                ctk.CTkLabel(box, text=label, font=self._font(14), text_color=P["muted"], anchor="w").grid(
                    row=i, column=0, sticky="w", pady=5, padx=(0, 20))
                ctk.CTkLabel(box, text=value, font=self._font(14), text_color=P["fg"], anchor="w").grid(
                    row=i, column=1, sticky="w", pady=5)

        ctk.CTkLabel(body, text="Sobre", font=self._font(24), text_color=P["fg"], anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 14))
        ctk.CTkLabel(body, text=APP_NAME, font=self._font(22), text_color=P["accent"], anchor="w").grid(
            row=1, column=0, sticky="w")
        ctk.CTkLabel(
            body, text=APP_DESC, font=self._font(14), text_color=P["muted"], anchor="w", justify="left",
            wraplength=420,
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))
        separator(3)
        info_rows(4, [("Versão", APP_VERSION), ("Última atualização", APP_UPDATED), ("Compatibilidade", APP_COMPAT)])
        separator(5)
        info_rows(6, [("Desenvolvido por", APP_AUTHOR), ("Contato", APP_EMAIL)])
        ctk.CTkButton(
            body, text="Enviar e-mail", height=42, font=self._font(14, "bold"), fg_color="transparent",
            border_width=1, border_color=P["border"], text_color=P["accent"], hover_color=P["card2"],
            corner_radius=21, command=self._send_email,
        ).grid(row=7, column=0, sticky="ew", pady=(14, 0))
        separator(8)
        ctk.CTkLabel(body, text="Proteção de dados", font=self._font(15, "bold"), text_color=P["fg"],
                     anchor="w").grid(row=9, column=0, sticky="w")
        ctk.CTkLabel(
            body, text=APP_PRIVACY, font=self._font(13), text_color=P["muted"], anchor="w", justify="left",
            wraplength=420,
        ).grid(row=10, column=0, sticky="w", pady=(8, 0))
        body.grid_rowconfigure(11, weight=1)
        ctk.CTkButton(
            body, text="Fechar", width=90, height=36, font=self._font(14, "bold"), fg_color="transparent",
            text_color=P["accent"], hover_color=P["card2"], command=dlg.destroy,
        ).grid(row=12, column=0, sticky="e", pady=(10, 0))

        dlg.after(150, lambda: (dlg.grab_set(), dlg.focus_force()))

    @staticmethod
    def _send_email() -> None:
        webbrowser.open(f"mailto:{APP_EMAIL}?subject={quote(APP_NAME + ' - contato')}")

    # ---------------------------------------------------------- Mensagens ---
    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.msgs.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _in_background(self) -> bool:
        try:
            return self.state() == "iconic" or self.focus_displayof() is None
        except Exception:
            return False

    def _notify_done(self, ok: int, err: int) -> None:
        """Avisa o usuário (janela em segundo plano) que os downloads terminaram."""
        if not self._in_background():
            return
        if err:
            body = f"{ok} concluído(s), {err} com erro."
        else:
            body = f"{ok} arquivo(s) salvo(s) em {self.dest}"
        notify.toast("Downloads finalizados", body)
        notify.beep()
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()  # type: ignore[attr-defined]
            notify.flash_taskbar(hwnd)
        except Exception:
            pass

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
                self._status("Nenhum vídeo encontrado nesse link.", "err")
        elif kind == "analyze_error":
            self._set_mode(None)
            self._status(msg[1], "err")
        elif kind == "thumb":
            _, gen, iid, img = msg
            if gen == self.gen and iid in self.items:
                photo = ImageTk.PhotoImage(img)
                self.thumbs[iid] = photo  # guarda a referência, senão o Tk apaga a imagem
                self.tree.item(iid, image=photo)
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
                self._status(f"Cancelado. {ok} concluído(s).")
                return
            if err == 0:
                self.progress.set(1)
                self._status(f"Tudo certo! {ok} arquivo(s) salvo(s) em {self.dest}", "ok")
            else:
                self._status(f"{ok} concluído(s), {err} com erro — {last_error}", "err")
            self._notify_done(ok, err)

    def _on_close(self) -> None:
        if self.mode == "download" and not messagebox.askyesno(
            APP_NAME, "Há downloads em andamento. Deseja realmente sair?"
        ):
            return
        self.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        save_config({
            "dest": str(self.dest), "kind": self.kind_label,
            "quality": self.quality_var.get(), "theme": self.theme,
        })
        self.destroy()


def main() -> None:
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == "__main__":
    main()
