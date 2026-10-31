<#
.SYNOPSIS
  Scan a folder with Microsoft Defender and fail on any detection (#199).

.DESCRIPTION
  defender-scan.ps1 -Path <dir> [-UpdateSignatures]

  Runs `MpCmdRun.exe -Scan -ScanType 3 -File <dir> -DisableRemediation`: a
  custom scan of that folder that reports and touches nothing. MpCmdRun exits
  0 when it found nothing and 2 when it found a threat; anything but 0 fails
  this script, so a false positive stops the release instead of reaching
  users. -UpdateSignatures fetches the latest definitions first.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Path,
  [switch]$UpdateSignatures
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$target = (Resolve-Path -LiteralPath $Path).ProviderPath

function Find-MpCmdRun {
  # The platform folder holds the current engine; Program Files the inbox one.
  $platform = Join-Path $env:ProgramData 'Microsoft\Windows Defender\Platform'
  $newest = Get-ChildItem -LiteralPath $platform -Directory -ErrorAction SilentlyContinue |
    Sort-Object { try { [version]($_.Name -replace '-.*$', '') } catch { [version]'0.0' } } -Descending |
    ForEach-Object { Join-Path $_.FullName 'MpCmdRun.exe' } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
  if ($newest) { return $newest }
  $inbox = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
  if (Test-Path -LiteralPath $inbox) { return $inbox }
  throw 'MpCmdRun.exe not found: is Microsoft Defender installed on this runner?'
}

$mp = Find-MpCmdRun
Write-Host "defender: $mp"

if ($UpdateSignatures) {
  & $mp -SignatureUpdate
  if ($LASTEXITCODE -ne 0) { throw "MpCmdRun -SignatureUpdate failed (exit $LASTEXITCODE)" }
}

& $mp -Scan -ScanType 3 -File $target -DisableRemediation
$code = $LASTEXITCODE
if ($code -eq 2) { throw "defender: threat(s) found in $target" }
if ($code -ne 0) { throw "defender: scan failed (MpCmdRun exit $code)" }
Write-Host "defender: no threats in $target"
