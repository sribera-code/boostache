"""
ocr.py – Texte d'une image, lu par l'OCR intégré à Windows (Windows.Media.Ocr).

Appelé via PowerShell (accès natif aux API WinRT) : rien à installer. La
langue est celle du profil Windows. Sert à l'aide contextuelle : les petits
modèles Ollama voient les captures en basse résolution et ne lisent pas le
texte d'une fenêtre, on le leur donne donc à part.

Le script est passé en clair (-Command) : sur certains postes, une commande
encodée en base64 (-EncodedCommand, précédée de -NonInteractive ou
-ExecutionPolicy) est neutralisée, sans doute par l'antivirus, et PowerShell
ne renvoie rien. Il ne doit pas contenir de guillemets doubles (ligne de
commande Windows) ; le chemin de l'image arrive par l'environnement.
"""

import os
import subprocess

from engine import logger

TIMEOUT = 20                # secondes

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$Path = $env:BOOSTACHE_OCR_PATH
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $null = $task.Wait(-1)
  $task.Result
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { exit 3 }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$result.Lines | ForEach-Object { $_.Text }
"""


def read_text(path: str) -> str:
    """Lignes de texte de l'image (PNG, JPEG…), dans l'ordre de lecture.
    Chaîne vide si l'OCR est indisponible (aucune langue OCR installée) ou échoue."""
    # GetFileFromPathAsync n'accepte que des chemins absolus à barres obliques inverses
    env = {**os.environ, "BOOSTACHE_OCR_PATH": os.path.abspath(path)}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
            env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        logger.log(f"OCR : lecture impossible ({e})")
        return ""
    if result.returncode == 3:
        logger.log("OCR : aucune langue de reconnaissance installée dans Windows.")
        return ""
    if result.returncode:
        logger.log(f"OCR : échec ({(result.stderr or '').strip().splitlines()[:1]})")
        return ""
    return "\n".join(line.strip() for line in result.stdout.splitlines() if line.strip())
