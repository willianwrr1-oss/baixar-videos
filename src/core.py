"""Lógica de análise e download — independente da interface gráfica."""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import yt_dlp
from yt_dlp.utils import DownloadCancelled

# Limite de itens listados (evita travar com canais enormes ou "mixes" infinitos)
MAX_ENTRIES = 500

KIND_VIDEO = "video"
KIND_AUDIO = "audio"

VIDEO_QUALITIES = {
    "Melhor disponível": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}
AUDIO_QUALITIES = {"320 kbps": "320", "192 kbps": "192", "128 kbps": "128"}


# --------------------------------------------------------------------------- #
# Ferramentas embutidas (ffmpeg, ffprobe, deno)
# --------------------------------------------------------------------------- #
def app_dir() -> Path:
    """Pasta onde está o .exe (ou este arquivo, em desenvolvimento)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """Pasta onde o PyInstaller extrai os arquivos embutidos."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent


def _setup_tools() -> Optional[str]:
    """Coloca ffmpeg/ffprobe/deno no PATH e devolve a pasta do ffmpeg, se achar.

    A pasta do .exe vem primeiro: quem quiser pode largar um ffmpeg.exe mais novo
    ao lado do programa e ele terá prioridade sobre o embutido.
    """
    folders = [app_dir(), bundle_dir(), bundle_dir() / "bin"]
    existing = [str(p) for p in folders if p.is_dir()]
    os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else None


FFMPEG_DIR = _setup_tools()


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
@dataclass
class VideoItem:
    url: str
    title: str
    duration: Optional[int] = None
    channel: str = ""


class _QuietLogger:
    """Evita que o yt-dlp escreva no console (o app roda sem janela de console)."""

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def friendly_error(exc: BaseException) -> str:
    """Traduz erros comuns do yt-dlp para mensagens curtas em português."""
    msg = _ANSI.sub("", str(exc)).strip()
    low = msg.lower()
    rules = [
        ("unsupported url", "Não encontrei vídeos baixáveis nesse link (site não suportado)."),
        ("sign in to confirm", "O site pediu verificação anti-robô ou login. Tente de novo mais tarde."),
        ("private video", "Esse vídeo é privado."),
        ("video unavailable", "Vídeo indisponível."),
        ("members-only", "Conteúdo exclusivo para membros."),
        ("getaddrinfo failed", "Sem conexão com a internet (ou o site está fora do ar)."),
        ("unable to download webpage", "Não consegui abrir a página. Verifique o link e a conexão."),
        ("http error 403", "O site recusou o acesso (erro 403)."),
        ("http error 404", "Página não encontrada (erro 404)."),
        ("is not a valid url", "O link parece inválido."),
    ]
    for needle, text in rules:
        if needle in low:
            return text
    if "ffmpeg" in low and "not found" in low:
        return "FFmpeg não encontrado — o recurso de conversão não está disponível."
    last = msg.splitlines()[-1] if msg else "Erro desconhecido."
    last = re.sub(r"^ERROR:\s*", "", last)
    last = re.sub(r"^\[[^\]]+\]\s+[\w-]+:\s*", "", last) or last  # "[youtube] abc123: ..."
    return last[:220]


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "socket_timeout": 30,
        "retries": 5,
    }
    if FFMPEG_DIR:
        opts["ffmpeg_location"] = FFMPEG_DIR
    return opts


# --------------------------------------------------------------------------- #
# Análise do link
# --------------------------------------------------------------------------- #
def _collect(info: Optional[dict], out: list, seen: set) -> None:
    if not info or len(out) >= MAX_ENTRIES:
        return
    entries = info.get("entries")
    if entries is not None:
        for entry in entries:
            _collect(entry, out, seen)
            if len(out) >= MAX_ENTRIES:
                break
        return
    if str(info.get("ie_key", "")).endswith("Tab"):  # link para outra playlist/aba
        return
    url = info.get("webpage_url") or info.get("url")
    if not url or not str(url).lower().startswith("http"):
        return
    vid = info.get("id")
    key = f"{info.get('ie_key') or info.get('extractor_key') or ''}:{vid}" if vid else str(url)
    if key in seen or str(url) in seen:
        return
    seen.update((key, str(url)))
    duration = info.get("duration")
    out.append(
        VideoItem(
            url=str(url),
            title=str(info.get("title") or url),
            duration=int(duration) if duration else None,
            channel=str(info.get("channel") or info.get("uploader") or ""),
        )
    )


def analyze(url: str) -> tuple[str, list[VideoItem], bool]:
    """Lê o link e devolve (título da lista, vídeos encontrados, foi_truncado)."""
    opts = {
        **_base_opts(),
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": MAX_ENTRIES,
    }
    items: list[VideoItem] = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise RuntimeError("Nenhuma informação retornada para esse link.")
        _collect(info, items, set())
    title = str(info.get("title") or "") if info.get("entries") is not None else ""
    return title, items, len(items) >= MAX_ENTRIES


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download_one(
    url: str,
    kind: str,
    quality: str,
    dest: Path,
    on_progress: Callable[[float, str], None],
    cancel: threading.Event,
) -> str:
    """Baixa um item. Lança DownloadCancelled se `cancel` for acionado."""
    # O yt-dlp baixa vídeo e áudio em arquivos separados e depois junta. Sem tratar
    # isso, a barra chegava a 100% duas vezes e parecia que o vídeo baixava em dobro.
    state = {"files": [], "streams": 1, "last": 0.0}

    def hook(d: dict) -> None:
        if cancel.is_set():
            raise DownloadCancelled("Cancelado")
        fname = d.get("tmpfilename") or d.get("filename") or ""
        if fname and fname not in state["files"]:
            state["files"].append(fname)
            if len(state["files"]) == 1:
                info = d.get("info_dict") or {}
                video_only = (
                    kind == KIND_VIDEO
                    and info.get("vcodec") not in (None, "none")
                    and info.get("acodec") == "none"
                )
                state["streams"] = 2 if video_only else 1
        streams = state["streams"]
        stage = min(max(len(state["files"]) - 1, 0), streams - 1)
        status = d.get("status")
        if status == "downloading":
            now = time.monotonic()
            if now - state["last"] < 0.15:
                return
            state["last"] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                frac = (d.get("downloaded_bytes") or 0) / total
            elif d.get("fragment_count"):
                frac = (d.get("fragment_index") or 0) / d["fragment_count"]
            else:
                frac = 0.0
            overall = min(99.0, (stage + min(frac, 1.0)) / streams * 100)
            parts = [f"{overall:.0f}%"]
            if d.get("speed"):
                parts.append(f"{_fmt_bytes(d['speed'])}/s")
            if d.get("eta") is not None:
                parts.append(f"faltam {format_duration(d['eta'])}")
            on_progress(overall, " · ".join(parts))
        elif status == "finished" and stage + 1 >= streams:
            on_progress(99.0, "Processando…")

    def pp_hook(d: dict) -> None:
        if d.get("status") == "started":
            on_progress(99.0, "Convertendo…")

    opts = {
        **_base_opts(),
        "outtmpl": str(Path(dest) / "%(title).150B.%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "concurrent_fragment_downloads": 4,
        "fragment_retries": 5,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    }

    if kind == KIND_AUDIO:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": AUDIO_QUALITIES.get(quality, "192"),
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ]
    else:
        height = VIDEO_QUALITIES.get(quality)
        if height:
            opts["format"] = f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"
        else:
            opts["format"] = "bv*+ba/b"
        # Prefere H.264/AAC (toca em qualquer aparelho) sem abrir mão da resolução
        opts["format_sort"] = ["res", "vcodec:h264", "acodec:m4a"]
        opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not info:
        raise RuntimeError("O download não retornou nenhum arquivo.")
    downloads = info.get("requested_downloads") or []
    return downloads[0].get("filepath", "") if downloads else ""
