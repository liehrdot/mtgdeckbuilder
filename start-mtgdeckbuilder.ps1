# One-click launcher for the MTG Commander Deckbuilder GUI.
# First run: installs uv (if missing), checks the Claude login, installs dependencies.
# Every run: syncs dependencies (fast no-op when nothing changed), starts the GUI, opens the browser.

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'MTG Deckbuilder'

$port    = if ($env:MTG_GUI_PORT) { [int]$env:MTG_GUI_PORT } else { 8765 }
$url     = "http://127.0.0.1:$port"
$userBin = Join-Path $HOME '.local\bin'   # where the uv and Claude Code installers put their exe

function Add-UserBinToPath {
    if ((Test-Path $userBin) -and -not (($env:Path -split ';') -contains $userBin)) {
        $env:Path = "$userBin;$env:Path"
    }
}

function Test-Port([int]$p) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $p, $null, $null)
        return ($iar.AsyncWaitHandle.WaitOne(300) -and $client.Connected)
    } catch { return $false } finally { $client.Close() }
}

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

try {
    Add-UserBinToPath

    # Already running? Then just open the browser.
    if (Test-Port $port) {
        Step "Deckbuilder is already running - opening $url"
        Start-Process $url
        exit 0
    }

    # 1) uv (Python package manager; fetches a fitting Python by itself)
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Step 'uv not found - installing it (one-time)...'
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        Add-UserBinToPath
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv installation failed. Install it manually from https://docs.astral.sh/uv/ and run this again.'
        }
    }

    # 2) Claude login (the GUI builds decks by running Claude Code)
    $credFile = Join-Path $HOME '.claude\.credentials.json'
    $loggedIn = (Test-Path $credFile) -or $env:ANTHROPIC_API_KEY -or $env:CLAUDE_CODE_OAUTH_TOKEN
    if (-not $loggedIn) {
        Write-Host ''
        Write-Host 'No Claude Code login found. The deck building needs one (browsing decks works without).' -ForegroundColor Yellow
        $answer = Read-Host 'Log in now? [Y/n]'
        if ($answer -notmatch '^[nN]') {
            if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
                Step 'Installing Claude Code (one-time)...'
                powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
                Add-UserBinToPath
            }
            if (Get-Command claude -ErrorAction SilentlyContinue) {
                Write-Host ''
                Write-Host 'Claude Code opens now. Log in in the browser window, confirm trust for this folder,' -ForegroundColor Yellow
                Write-Host 'then type  /exit  to come back here and continue.' -ForegroundColor Yellow
                Write-Host ''
                & claude
            } else {
                Write-Host 'Claude Code could not be installed - continuing without login.' -ForegroundColor Yellow
            }
        }
    }

    # 3) Dependencies incl. GUI extras (quick when already up to date, picks up changes after a git pull)
    Step 'Checking dependencies...'
    & uv sync --extra gui --quiet
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit code $LASTEXITCODE)." }

    # 4) Start the GUI, open the browser once it answers
    Step "Starting Deckbuilder on $url  (close this window or press Ctrl+C to stop)"
    $server = Start-Process -FilePath 'uv' -ArgumentList 'run', '--quiet', 'mtg-gui' -NoNewWindow -PassThru

    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Port $port)) {
        if ($server.HasExited) { throw "The GUI stopped right away (exit code $($server.ExitCode))." }
        if ((Get-Date) -gt $deadline) { throw "The GUI did not answer on $url within 60 seconds." }
        Start-Sleep -Milliseconds 400
    }
    Start-Process $url
    $server.WaitForExit()
}
catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    Read-Host 'Press Enter to close'
    exit 1
}
