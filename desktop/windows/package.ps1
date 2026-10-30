<#
.SYNOPSIS
  Package the frozen Windows build: Setup.exe and the portable zip (#199).

.DESCRIPTION
  package.ps1 -Version X.Y.Z[-rc.N] [-Sign]

  Takes PyInstaller's onedir at desktop\dist\gt7-datalogger (from
  `python desktop/scripts/build.py`) and writes into desktop\dist\:

    GT7-Datalogger-Windows-x64-Setup.exe      Inno Setup, from gt7.iss
    GT7-Datalogger-Windows-x64-Portable.zip   GT7 Datalogger\gt7-datalogger.exe ...

  With -Sign, through sign.ps1 and the route in $env:WINDOWS_SIGNING:
    1. every .exe, .dll and .pyd in the onedir, before anything is packed,
       so the zip and the installer both carry signed files;
    2. the uninstaller and Setup.exe, which ISCC signs as it builds them
       (SignTool + SignedUninstaller in gt7.iss).
  On the signpath route step 1 only verifies (SignPath's action signed the
  onedir first), and Setup.exe goes back through SignPath afterwards; its
  uninstaller stays unsigned, the one exception the plan writes down.

  ISCC is found as $env:ISCC, on PATH, or in the usual Inno Setup 7/6 folders.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Version,
  [switch]$Sign
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not ($Version -match '^(\d+\.\d+\.\d+)(-rc\.\d+)?$')) {
  throw "-Version must look like X.Y.Z or X.Y.Z-rc.N (got '$Version')"
}
$FileVersion = $Matches[1]

$Here = $PSScriptRoot
$Desktop = Split-Path -Parent $Here
$Dist = Join-Path $Desktop 'dist'
$OneDir = Join-Path $Dist 'gt7-datalogger'
$Exe = Join-Path $OneDir 'gt7-datalogger.exe'
$SignScript = Join-Path $Here 'sign.ps1'
$Setup = Join-Path $Dist 'GT7-Datalogger-Windows-x64-Setup.exe'
$Portable = Join-Path $Dist 'GT7-Datalogger-Windows-x64-Portable.zip'

if (-not (Test-Path -LiteralPath $Exe)) {
  throw "$Exe not found: run python desktop/scripts/build.py first"
}

function Find-Iscc {
  if ($env:ISCC) { return $env:ISCC }
  $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($onPath) { return $onPath.Source }
  foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
    foreach ($name in @('Inno Setup 7', 'Inno Setup 6')) {
      $candidate = Join-Path $base "$name\ISCC.exe"
      if ($base -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
  }
  throw 'ISCC.exe not found: install Inno Setup or set ISCC'
}

$route = "$env:WINDOWS_SIGNING".Trim().ToLowerInvariant()
if ($Sign -and -not $route) { throw '-Sign needs $env:WINDOWS_SIGNING (azure, signpath or pfx)' }

# 1. The onedir's own PE files.
if ($Sign) {
  $pe = @(Get-ChildItem -LiteralPath $OneDir -Recurse -File |
    Where-Object { $_.Extension -in '.exe', '.dll', '.pyd' } |
    ForEach-Object { $_.FullName })
  Write-Host "package: signing $($pe.Count) PE file(s) in the onedir"
  & $SignScript -Path $pe
}

# 2. The portable zip: one top folder, so it unpacks tidily anywhere.
Remove-Item -LiteralPath $Portable -Force -ErrorAction SilentlyContinue
$stage = Join-Path $Dist 'portable-stage'
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
$top = Join-Path $stage 'GT7 Datalogger'
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -LiteralPath $OneDir -Destination $top -Recurse
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory(
  $top, $Portable, [IO.Compression.CompressionLevel]::Optimal, $true)
Remove-Item -LiteralPath $stage -Recurse -Force
Write-Host "package: wrote $Portable"

# 3. The installer.
$iscc = Find-Iscc
$isccArgs = @(
  "/DAppVersion=$Version",
  "/DFileVersion=$FileVersion",
  "/DSourceDir=$OneDir",
  "/DOutputDir=$Dist"
)
if ($Sign -and $route -ne 'signpath') {
  # $q is a quote and $f the quoted file to sign, expanded by ISCC itself.
  $hostExe = (Get-Process -Id $PID).Path
  $isccArgs += "/Sgt7sign=`$q$hostExe`$q -NoProfile -ExecutionPolicy Bypass -File `$q$SignScript`$q -Path `$f"
  $isccArgs += '/DSignTool=gt7sign'
}
$isccArgs += (Join-Path $Here 'gt7.iss')
Remove-Item -LiteralPath $Setup -Force -ErrorAction SilentlyContinue
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }
if (-not (Test-Path -LiteralPath $Setup)) { throw "ISCC did not write $Setup" }
if ($Sign -and $route -ne 'signpath') {
  & $SignScript -Path $Setup  # ISCC signed it; this verifies
}
elseif ($Sign) {
  Write-Host "package: Setup.exe is unsigned here and goes through SignPath next"
}
Write-Host "package: wrote $Setup"
