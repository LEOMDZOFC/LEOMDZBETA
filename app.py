# ============================================================
#  LEO MDZ YT CONVERTER — API para Vercel & Termux Local
#  Suporta: MP3 (áudio) e MP4 (vídeo)
# ============================================================
import os
import uuid
import threading
import time

import yt_dlp
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Injeta o caminho padrão do Termux se o diretório existir
TERMUX_BIN = "/data/data/com.termux/files/usr/bin"
if os.path.exists(TERMUX_BIN):
    os.environ["PATH"] = TERMUX_BIN + os.pathsep + os.environ.get("PATH", "")

# ------------------------------------------------------------
# Configurações dinâmicas de Pastas (Vercel vs Termux)
# ------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Se estiver no Termux, usa uma pasta dentro da home. Se for na Vercel, usa /tmp
if os.path.exists(TERMUX_BIN):
    DOWNLOAD_DIR = os.path.expanduser("~/leomodz/downloads")
else:
    DOWNLOAD_DIR = "/tmp/downloads"

AUTO_DELETE_SECONDS = 120
FORMATOS_VALIDOS = ["mp3", "mp4"]

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

download_status = {}
delete_timers = {}


# ------------------------------------------------------------
# Procura o ffmpeg/ffprobe baixado no momento do build
# ------------------------------------------------------------
def achar_pasta_ffmpeg():
    if os.path.exists(TERMUX_BIN) and os.path.exists(os.path.join(TERMUX_BIN, "ffmpeg")):
        return TERMUX_BIN
        
    cache = os.path.join(BASE_DIR, "static-ffmpeg-cache")
    if not os.path.exists(cache):
        return None
    for dirpath, dirnames, filenames in os.walk(cache):
        if "ffmpeg" in filenames and "ffprobe" in filenames:
            return dirpath
    return None


FFMPEG_DIR = achar_pasta_ffmpeg()
if FFMPEG_DIR:
    print(f"✅ FFmpeg localizado em: {FFMPEG_DIR}")
else:
    print("⚠️ FFmpeg não encontrado — usando o instalado no sistema (teste local).")


# ------------------------------------------------------------
# Monta as opções do yt-dlp conforme o formato
# ------------------------------------------------------------
def montar_opcoes(download_id, formato):
    base = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, f'{download_id}_%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
    }
    if FFMPEG_DIR:
        base['ffmpeg_location'] = FFMPEG_DIR

    if formato == 'mp3':
        base.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:  # mp4
        base.update({
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    return base


def pegar_arquivo_final(ydl, info, formato):
    filename = ydl.prepare_filename(info)

    if formato == 'mp3':
        return filename.rsplit('.', 1)[0] + '.mp3'

    try:
        arquivo_merged = info['requested_downloads'][0]['filepath']
        if arquivo_merged:
            return arquivo_merged
    except (KeyError, IndexError, TypeError):
        pass

    return filename.rsplit('.', 1)[0] + '.mp4'


app = FastAPI(
    title="LEO MDZ YT CONVERTER — Downloader YouTube",
    description="API gratuita para baixar MP3 e MP4 de vídeos do YouTube.",
    version="2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------
# Rota: informações da API
# ------------------------------------------------------------
@app.get("/api/info")
async def informacoes():
    return {
        "message": "🎬 LEO MDZ YT CONVERTER — API de Download do YouTube",
        "formatos": ["mp3", "mp4"],
        "endpoints": {
            "/download?url=URL&formato=mp3": "Download direto (mp3 ou mp4)",
            "/async-download?url=URL&formato=mp3": "Download assíncrono (melhor esforço no Vercel)",
            "/status?download_id=ID": "Verificar status",
            "/download-file?download_id=ID": "Baixar o arquivo",
            "/files": "Listar arquivos ativos",
            "/cleanup": "Forçar a exclusão"
        }
    }


# ------------------------------------------------------------
# Rota: download direto (usado pelo site no Vercel)
# ------------------------------------------------------------
@app.get("/download")
async def download_direto(
    url: str = Query(..., description="URL do vídeo do YouTube"),
    formato: str = Query("mp3", description="mp3 ou mp4"),
):
    if formato not in FORMATOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Formato inválido. Use 'mp3' ou 'mp4'.")

    try:
        download_id = str(uuid.uuid4())[:8]

        with yt_dlp.YoutubeDL(montar_opcoes(download_id, formato)) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = pegar_arquivo_final(ydl, info, formato)

            media_type = "audio/mpeg" if formato == "mp3" else "video/mp4"
            return FileResponse(
                path=filename,
                filename=os.path.basename(filename),
                media_type=media_type,
                headers={
                    "X-Auto-Delete": f"{AUTO_DELETE_SECONDS} segundos",
                    "X-Download-ID": download_id
                }
            )

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha no download: {str(e)}")


# ------------------------------------------------------------
# Rota: download assíncrono (mantida para compatibilidade)
# ------------------------------------------------------------
@app.get("/async-download")
async def async_download(
    url: str = Query(..., description="URL do vídeo do YouTube"),
    formato: str = Query("mp3", description="mp3 ou mp4"),
):
    if formato not in FORMATOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Formato inválido. Use 'mp3' ou 'mp4'.")

    download_id = str(uuid.uuid4())[:8]
    download_status[download_id] = {
        'status': 'pendente',
        'progresso': 0,
        'url': url,
        'formato': formato,
        'file_path': None,
        'error': None,
        'created_at': time.time(),
        'expires_in': AUTO_DELETE_SECONDS
    }

    def tarefa():
        try:
            download_status[download_id]['status'] = 'baixando'
            with yt_dlp.YoutubeDL(montar_opcoes(download_id, formato)) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = pegar_arquivo_final(ydl, info, formato)
                download_status[download_id]['status'] = 'concluido'
                download_status[download_id]['file_path'] = filename
                download_status[download_id]['title'] = info.get('title', 'arquivo')
        except Exception as e:
            download_status[download_id]['status'] = 'falhou'
            download_status[download_id]['error'] = str(e)

    thread = threading.Thread(target=tarefa)
    thread.daemon = True
    thread.start()

    return JSONResponse({
        "status": "sucesso",
        "message": "Download iniciado!",
        "download_id": download_id,
        "formato": formato,
        "verificar_status": f"/status?download_id={download_id}"
    })


@app.get("/status")
async def get_status(download_id: str = Query(..., description="ID do download")):
    if download_id not in download_status:
        raise HTTPException(status_code=404, detail="ID do download não encontrado")

    status_data = download_status[download_id]
    resposta = {
        "status": status_data['status'],
        "titulo": status_data.get('title', 'Desconhecido'),
        "formato": status_data.get('formato', 'mp3'),
    }

    if status_data['status'] == 'concluido':
        file_path = status_data.get('file_path')
        if file_path and os.path.exists(file_path):
            resposta["arquivo_pronto"] = True
            resposta["download_url"] = f"/download-file?download_id={download_id}"
        else:
            resposta["arquivo_pronto"] = False
            resposta["status"] = 'excluido'
            resposta["message"] = "O arquivo foi excluído automaticamente"

    elif status_data['status'] == 'falhou':
        resposta["erro"] = status_data.get('error', 'Erro desconhecido')

    return JSONResponse(resposta)


@app.get("/download-file")
async def download_file(download_id: str = Query(..., description="ID do download")):
    if download_id not in download_status:
        raise HTTPException(status_code=404, detail="ID de arquivo inválido.")
        
    status_data = download_status[download_id]
    file_path = status_data.get('file_path')
    
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=410, detail="O arquivo não existe mais ou foi deletado.")
        
    media_type = "audio/mpeg" if status_data.get('formato') == "mp3" else "video/mp4"
    return FileResponse(
        path=file_path,
        filename=os.path.basename(file_path),
        media_type=media_type
    )
