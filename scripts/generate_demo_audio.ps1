<#
.SYNOPSIS
    Generate a synthetic multi-speaker Chinese meeting recording.

.DESCRIPTION
    Lets you verify the whole pipeline (upload -> ASR -> distillation -> context
    card) on Windows without using anyone's real private conversations.

    The dialogue lives in a UTF-8 data file rather than inline, because Windows
    PowerShell 5.1 reads .ps1 sources using the ANSI code page and would corrupt
    inline CJK.

    Requires: a zh-CN SAPI voice, plus ffmpeg/ffprobe on PATH.

.EXAMPLE
    .\generate_demo_audio.ps1 -OutFile C:\tmp\meeting.m4a

.EXAMPLE
    .\generate_demo_audio.ps1 -Dialogue .\my-lines.txt -OutFile .\meeting.m4a
#>
[CmdletBinding()]
param(
    [string]$Dialogue = (Join-Path $PSScriptRoot 'demo_dialogue.zh.txt'),
    [string]$OutFile  = (Join-Path ([System.IO.Path]::GetTempPath()) 'shadowscribe-demo.m4a'),
    [string]$Voice    = 'Microsoft Huihui Desktop',
    [int]$BitrateKbps = 64
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Dialogue)) { throw "dialogue file not found: $Dialogue" }
foreach ($tool in 'ffmpeg', 'ffprobe') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found on PATH" }
}

Add-Type -AssemblyName System.Speech

$lines = [System.IO.File]::ReadAllLines($Dialogue, [System.Text.Encoding]::UTF8) |
    Where-Object { $_.Trim().Length -gt 0 -and -not $_.Trim().StartsWith('#') }
if (-not $lines) { throw "dialogue file is empty: $Dialogue" }

$outDir  = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$wavFile = [System.IO.Path]::ChangeExtension($OutFile, '.wav')

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$available = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
if ($Voice -and $available -contains $Voice) {
    $synth.SelectVoice($Voice)
} else {
    $zh = $synth.GetInstalledVoices() |
        Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' } |
        Select-Object -First 1
    if (-not $zh) { throw "no Chinese SAPI voice installed. Available: $($available -join ', ')" }
    $synth.SelectVoice($zh.VoiceInfo.Name)
    Write-Warning "voice '$Voice' unavailable; using '$($zh.VoiceInfo.Name)'"
}

Write-Host "Rendering $($lines.Count) lines to $wavFile ..."
$synth.Volume = 100
$synth.SetOutputToWaveFile($wavFile)
try {
    foreach ($line in $lines) {
        $parts = $line.Split('|')
        $rate = 0
        if ($parts.Count -ge 3) {
            $text = $parts[2]
            [void][int]::TryParse($parts[1], [ref]$rate)
        } else {
            $text = $line
        }
        # A single installed voice forces us to vary prosody to distinguish
        # speakers. ASR does not care, but it keeps the sample from sounding
        # like a flat read-through.
        $synth.Rate = $rate
        $synth.Speak($text)
        Start-Sleep -Milliseconds 350
    }
} finally {
    $synth.SetOutputToNull()
    $synth.Dispose()
}

# Hand the server a compressed, phone-like container rather than a canonical WAV,
# so ffmpeg normalisation is genuinely exercised.
& ffmpeg -hide_banner -loglevel error -y -i $wavFile -c:a aac -b:a "${BitrateKbps}k" -ac 1 $OutFile
if ($LASTEXITCODE -ne 0) { throw 'ffmpeg failed' }

Remove-Item $wavFile -ErrorAction SilentlyContinue

Write-Host ''
Get-Item $OutFile | Select-Object Name, Length | Format-Table -AutoSize
& ffprobe -v error -show_entries format=duration,format_name -of default=nw=1 $OutFile
Write-Host "audio ready: $OutFile" -ForegroundColor Green
Write-Host ''
Write-Host 'Next:' -ForegroundColor Cyan
Write-Host "  SS_TOKEN=<token> ./scripts/verify_e2e.sh `"$OutFile`" --hint `"与老王、张总在会议室`""
