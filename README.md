# Baixador de Vídeos

Aplicativo **portátil para Windows** (não precisa instalar nada): cole um link, o app lista os
vídeos encontrados, você marca os que quer e escolhe entre **Vídeo (MP4)** ou **Música (MP3)**.

Funciona com YouTube e com milhares de outros sites suportados pelo [yt-dlp](https://github.com/yt-dlp/yt-dlp),
inclusive playlists e canais.

## Baixar e usar

1. Abra a aba **Releases** deste repositório e baixe o `BaixadorDeVideos.exe` mais recente.
   (Link direto: `https://github.com/SEU_USUARIO/SEU_REPO/releases/latest/download/BaixadorDeVideos.exe`)
2. Dê dois cliques. Na primeira vez o Windows pode mostrar o aviso do SmartScreen
   (o arquivo não é assinado digitalmente): clique em **Mais informações → Executar assim mesmo**.
3. Cole o link, clique em **Verificar**, marque os vídeos, escolha o formato e clique em **Baixar selecionados**.

Os arquivos vão para a pasta Downloads (ou a que você escolher). O app lembra a última escolha,
inclusive o tema (botão **Tema claro/escuro** no topo).

- **Limpar** apaga o link e a lista de vídeos.
- **Sobre** mostra versão, contato e proteção de dados.
- Se a janela estiver em segundo plano quando os downloads terminarem, o Windows mostra uma notificação,
  toca um som e o app pisca na barra de tarefas.

## Como o .exe é gerado

Tudo é automático pelo GitHub Actions (`.github/workflows/build.yml`):

- a cada push na branch `main`, manualmente (**Actions → Build Windows EXE → Run workflow**)
  e **toda segunda-feira** (para trazer a versão mais nova do yt-dlp, já que os sites mudam com frequência);
- o executável embute FFmpeg (conversão MP3 e junção de áudio/vídeo) e Deno (necessário para o YouTube);
- o resultado é publicado em **Releases**.

## Rodar pelo código (opcional)

```powershell
pip install -r requirements.txt
python src\app.py
```
Para MP3/MP4 em alta qualidade é preciso ter `ffmpeg` e `ffprobe` no PATH (ou ao lado de `src\core.py`),
e o `deno` para o YouTube.

## Observações

- Se um site parar de funcionar, rode o workflow de novo para recompilar com o yt-dlp atualizado.
- Alguns antivírus desconfiam de executáveis gerados pelo PyInstaller (falso positivo comum).
- Baixe apenas conteúdo que você tem direito de baixar e respeite os termos de uso de cada site.
