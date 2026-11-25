"""HTTPS update check and safe replacement for the frozen Windows executable."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests


MANIFEST_URL = "https://prodatastelecom.com.br/sites/catraca_vale/latest.json"
UPDATE_PATH_PREFIX = "/sites/catraca_vale/"
MAX_MANIFEST_BYTES = 32 * 1024
MAX_UPDATE_BYTES = 500 * 1024 * 1024

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-preview\.(\d+))?$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)

_APPLY_UPDATE_SCRIPT = r'''param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$TargetPath,
    [Parameter(Mandatory=$true)][string]$StagedPath,
    [Parameter(Mandatory=$true)][string]$LogPath
)

$ErrorActionPreference = 'Stop'
$UpdateId = [guid]::NewGuid().ToString('N')
$BackupPath = "$TargetPath.update-previous-$UpdateId"
$FailedPath = "$TargetPath.update-failed-$UpdateId"
$ReadyPath = Join-Path $env:LOCALAPPDATA "ValeCatracaSimples\updates\startup-$UpdateId.ready"

function Write-UpdateLog([string]$Message) {
    try {
        Add-Content -LiteralPath $LogPath -Value ("{0:o} {1}" -f (Get-Date), $Message) -Encoding UTF8
    } catch { }
}

try {
    $waitUntil = (Get-Date).AddSeconds(90)
    while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $waitUntil) { throw 'O aplicativo antigo não encerrou dentro do prazo.' }
        Start-Sleep -Milliseconds 250
    }

    if (Test-Path -LiteralPath $BackupPath) {
        Remove-Item -LiteralPath $BackupPath -Force
    }
    [System.IO.File]::Replace($StagedPath, $TargetPath, $BackupPath, $true)

    $env:VALE_CATRACA_UPDATE_READY_FILE = $ReadyPath
    $newProcess = Start-Process -FilePath $TargetPath -PassThru
    Remove-Item Env:\VALE_CATRACA_UPDATE_READY_FILE -ErrorAction SilentlyContinue
    $startedAt = Get-Date
    $healthy = $false
    $checkUntil = $startedAt.AddSeconds(60)
    while ((Get-Date) -lt $checkUntil) {
        Start-Sleep -Milliseconds 500
        if (Test-Path -LiteralPath $ReadyPath) {
            $healthy = $true
            break
        }
        if (-not (Get-Process -Id $newProcess.Id -ErrorAction SilentlyContinue)) { break }
    }

    if (-not $healthy) {
        if (Get-Process -Id $newProcess.Id -ErrorAction SilentlyContinue) {
            & taskkill.exe /T /F /PID $newProcess.Id *> $null
        }
        Start-Sleep -Milliseconds 500
        throw 'A nova versão não confirmou a inicialização; a versão anterior será restaurada.'
    }

    Remove-Item -LiteralPath $ReadyPath -Force -ErrorAction SilentlyContinue
    try {
        Remove-Item -LiteralPath $BackupPath -Force -ErrorAction Stop
    } catch {
        Write-UpdateLog ("Atualização iniciada; backup antigo mantido em " + $BackupPath)
    }
    Write-UpdateLog 'Atualização instalada e aplicativo reiniciado.'
} catch {
    Remove-Item Env:\VALE_CATRACA_UPDATE_READY_FILE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ReadyPath -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $BackupPath) {
        try {
            if ($newProcess -and (Get-Process -Id $newProcess.Id -ErrorAction SilentlyContinue)) {
                & taskkill.exe /T /F /PID $newProcess.Id *> $null
                Start-Sleep -Milliseconds 500
            }
            if (Test-Path -LiteralPath $TargetPath) {
                [System.IO.File]::Replace($BackupPath, $TargetPath, $FailedPath, $true)
                Remove-Item -LiteralPath $FailedPath -Force -ErrorAction SilentlyContinue
            } else {
                Move-Item -LiteralPath $BackupPath -Destination $TargetPath
            }
            Start-Process -FilePath $TargetPath | Out-Null
        } catch { }
    } elseif (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and
              (Test-Path -LiteralPath $TargetPath)) {
        try { Start-Process -FilePath $TargetPath | Out-Null } catch { }
    }
    Remove-Item -LiteralPath $StagedPath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog ("Falha ao aplicar atualização: " + $_.Exception.Message)
    exit 1
}
'''


def _version_key(version: str) -> tuple[int, int, int, int, int]:
    match = _VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError("formato de versão inválido")
    major, minor, patch, preview = match.groups()
    # Releases estáveis ordenam depois de qualquer preview do mesmo número.
    return int(major), int(minor), int(patch), 1 if preview is None else 0, int(preview or 0)


def _allowed_update_url(value: str) -> bool:
    try:
        url = urlparse(value)
        return (
            url.scheme == "https"
            and url.netloc.lower() == "prodatastelecom.com.br"
            and url.username is None
            and url.password is None
            and url.path.startswith(UPDATE_PATH_PREFIX)
        )
    except (TypeError, ValueError):
        return False


def _read_manifest() -> dict:
    with requests.get(MANIFEST_URL, stream=True, timeout=(3, 5)) as response:
        response.raise_for_status()
        if not _allowed_update_url(response.url):
            raise ValueError("o manifesto redirecionou para um endereço não permitido")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_MANIFEST_BYTES:
            raise ValueError("manifesto maior que o limite permitido")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=4096):
            if chunk:
                body.extend(chunk)
                if len(body) > MAX_MANIFEST_BYTES:
                    raise ValueError("manifesto maior que o limite permitido")
    manifest = json.loads(body.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifesto inválido")
    return manifest


def _download_update(url: str, expected_size: int, expected_sha256: str, destination: Path) -> None:
    digest = hashlib.sha256()
    total = 0
    with requests.get(url, stream=True, timeout=(5, 30)) as response:
        response.raise_for_status()
        if not _allowed_update_url(response.url):
            raise ValueError("o download redirecionou para um endereço não permitido")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) != expected_size:
            raise ValueError("o tamanho informado pelo servidor não confere com o manifesto")
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > expected_size or total > MAX_UPDATE_BYTES:
                    raise ValueError("o arquivo baixado excede o tamanho anunciado")
                digest.update(chunk)
                output.write(chunk)
    if total != expected_size:
        raise ValueError("o arquivo baixado tem tamanho diferente do manifesto")
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
        raise ValueError("a verificação SHA-256 do arquivo falhou")


def _start_replacement(target: Path, staged: Path) -> None:
    app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ValeCatracaSimples"
    updater_dir = app_data / "updates"
    updater_dir.mkdir(parents=True, exist_ok=True)
    log_path = app_data / "updater.log"
    script_path = updater_dir / "apply_update.ps1"
    script_path.write_text(_APPLY_UPDATE_SCRIPT, encoding="utf-8-sig")

    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    powershell = windows_dir / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise FileNotFoundError("Windows PowerShell não foi encontrado")

    command = [
        str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-File", str(script_path),
        "-ParentPid", str(os.getpid()), "-TargetPath", str(target),
        "-StagedPath", str(staged), "-LogPath", str(log_path),
    ]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def check_and_apply_update(current_version: str) -> bool:
    """Return True only after scheduling a verified update of a frozen Windows EXE."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False

    target = Path(sys.executable).resolve()
    if target.suffix.lower() != ".exe":
        return False

    staged: Path | None = None
    try:
        manifest = _read_manifest()
        version = manifest.get("version")
        download_url = manifest.get("download_url")
        expected_size = manifest.get("size_bytes")
        expected_sha256 = manifest.get("sha256")

        if not isinstance(version, str):
            raise ValueError("o manifesto não contém uma versão válida")
        if _version_key(version) <= _version_key(current_version):
            return False
        if not isinstance(download_url, str) or not _allowed_update_url(download_url):
            raise ValueError("o manifesto contém um endereço de download não permitido")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int):
            raise ValueError("o manifesto não contém um tamanho válido")
        if expected_size <= 0 or expected_size > MAX_UPDATE_BYTES:
            raise ValueError("o tamanho anunciado está fora do limite permitido")
        if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
            raise ValueError("o manifesto não contém um SHA-256 válido")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}-update-", suffix=".exe", dir=str(target.parent)
        )
        os.close(descriptor)
        staged = Path(temporary_name)
        _download_update(download_url, expected_size, expected_sha256, staged)
        _start_replacement(target, staged)
        return True
    except Exception as exc:
        try:
            app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ValeCatracaSimples"
            app_data.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                filename=app_data / "updater.log",
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
                encoding="utf-8",
            )
            logging.warning("Verificação/instalação de atualização ignorada: %s", exc)
        except Exception:
            pass
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
        return False
