# Twitch Clipper

Monitoriza o chat de uma ou mais lives da Twitch e, quando há um pico de mensagens, guarda automaticamente os últimos 30 segundos da live num `.mp4`.

## Como funciona

- **Chat:** ligação anónima ao chat da Twitch (não precisa de conta).
- **Vídeo:** o [streamlink](https://streamlink.github.io/) puxa a live e o ffmpeg mantém um buffer circular de ~1 minuto numa pasta temporária.
- **Pico:** a cada segundo mede quantos chatters diferentes escreveram nos últimos 5 s e compara com a média dos últimos 5 min. Se passar de N× a média (e de um mínimo absoluto), corta o clip.
- **Clip:** espera alguns segundos após o pico (para apanhar a reação) e guarda `pasta/canal/canal_data_hora_palavra.mp4`, onde a "palavra" é a mais usada no chat naquele momento. Todos os clips ficam registados em `clips_log.csv`.

## Download

Cada push para `main` gera o `TwitchClipper.exe` automaticamente: **Actions → Build Windows exe → último run → Artifacts**.
Ao criar uma tag `v*` (ex.: `v1.0`), o `.exe` é publicado em **Releases**.

## Gerar o .exe localmente (Windows)

Corra `build_windows.bat` (precisa de Python 3.10+ de python.org). Ele instala as dependências, descarrega o ffmpeg e gera `TwitchClipper.exe`.

## Correr a partir do código

```
pip install -r requirements.txt
python app.py
```
(precisa do `ffmpeg` no PATH)

## Ficheiros

| Ficheiro | O quê |
|---|---|
| `app.py` | Interface gráfica (tkinter) |
| `clipper_core.py` | Motor: chat, deteção de picos, buffer de vídeo e corte |
| `build_windows.bat` | Gera o `.exe` no Windows |
| `LEIA-ME.txt` | Instruções para quem só usa o `.exe` |
