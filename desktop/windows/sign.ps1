<#
.SYNOPSIS
  Authenticode-sign Windows files for a GT7 Datalogger release (#199).

.DESCRIPTION
  sign.ps1 -Path <file> [<file> ...]

  The route is $env:WINDOWS_SIGNING (the maintainer's signing decision, plan 08):

    azure     Azure Artifact Signing (formerly Trusted Signing): signtool with
              the Artifact Signing dlib and a metadata JSON built from
              AZURE_SIGNING_ENDPOINT, AZURE_SIGNING_ACCOUNT and
              AZURE_SIGNING_PROFILE. The dlib authenticates through
              DefaultAzureCredential, so the job logs in first (azure/login
              with OIDC). AZURE_SIGNING_DLIB names the dlib when it is not in
              the client tools' default folder. Timestamp:
              http://timestamp.acs.microsoft.com.
    signpath  SignPath signs through its own GitHub action, outside this
              script, so here the files are only verified: each must already
              carry a valid signature.
    pfx       signtool with a certificate file: WINDOWS_PFX_BASE64 (the .pfx,
              base64) and WINDOWS_PFX_PASSWORD. Timestamp:
              http://timestamp.digicert.com.

  Every signature carries an RFC 3161 timestamp (/tr, /td SHA256). A file that
  already has a valid signature (python3xx.dll, vcruntime140.dll) is left as it
  is: Smart App Control wants every PE file signed, not signed by us. Every
  file is verified afterwards, and any failure stops the script.

  Inno Setup calls this script too, as its SignTool, for Setup.exe and the
  uninstaller (package.ps1 sets that up).

  SIGNTOOL may name signtool.exe; otherwise the newest Windows SDK's x64 one.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string[]]$Path
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Route = "$env:WINDOWS_SIGNING".Trim().ToLowerInvariant()
$Timestamp = if ($Route -eq 'azure') { 'http://timestamp.acs.microsoft.com' } else { 'http://timestamp.digicert.com' }

function Find-SignTool {
  if ($env:SIGNTOOL) {
    if (-not (Test-Path -LiteralPath $env:SIGNTOOL)) { throw "SIGNTOOL=$env:SIGNTOOL does not exist" }
    return $env:SIGNTOOL
  }
  $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($onPath) { return $onPath.Source }
  $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
  $found = Get-ChildItem -Path $kits -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Directory.Name -eq 'x64' } |
    Sort-Object { $_.Directory.Parent.Name } -Descending |
    Select-Object -First 1
  if (-not $found) { throw 'signtool.exe not found: install the Windows SDK or set SIGNTOOL' }
  return $found.FullName
}

function Find-AzureDlib {
  if ($env:AZURE_SIGNING_DLIB) {
    if (-not (Test-Path -LiteralPath $env:AZURE_SIGNING_DLIB)) { throw "AZURE_SIGNING_DLIB=$env:AZURE_SIGNING_DLIB does not exist" }
    return $env:AZURE_SIGNING_DLIB
  }
  # Where the Artifact (Trusted) Signing Client Tools installer puts it.
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Microsoft\MicrosoftArtifactSigningClientTools'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\MicrosoftTrustedSigningClientTools'),
    (Join-Path $env:ProgramFiles 'Microsoft\MicrosoftArtifactSigningClientTools'),
    (Join-Path $env:ProgramFiles 'Microsoft\MicrosoftTrustedSigningClientTools')
  )
  foreach ($dir in $candidates) {
    $dll = Get-ChildItem -Path $dir -Filter 'Azure.CodeSigning.Dlib.dll' -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -notmatch '\\(x86|arm64)\\' } |
      Sort-Object { $_.FullName -notmatch '\\x64\\' } |
      Select-Object -First 1
    if ($dll) { return $dll.FullName }
  }
  throw 'Azure.CodeSigning.Dlib.dll not found: install the Artifact Signing Client Tools or set AZURE_SIGNING_DLIB'
}

function Require-Env([string[]]$Names) {
  $missing = @($Names | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
  if ($missing.Count -gt 0) { throw "WINDOWS_SIGNING=$Route needs: $($missing -join ', ')" }
}

function Test-Signed([string]$File) {
  return (Get-AuthenticodeSignature -LiteralPath $File).Status -eq 'Valid'
}

$files = @($Path | ForEach-Object { (Resolve-Path -LiteralPath $_).ProviderPath })
if ($files.Count -eq 0) { throw 'sign.ps1: no files given' }

if ($Route -eq 'signpath') {
  $unsigned = @($files | Where-Object { -not (Test-Signed $_) })
  if ($unsigned.Count -gt 0) {
    throw "WINDOWS_SIGNING=signpath: these are not signed (SignPath's action signs them before this step):`n  $($unsigned -join "`n  ")"
  }
  Write-Host "sign: $($files.Count) file(s) already carry a valid signature (SignPath)"
  exit 0
}

$todo = @($files | Where-Object { -not (Test-Signed $_) })
if ($todo.Count -eq 0) {
  Write-Host "sign: all $($files.Count) file(s) already signed"
  exit 0
}

$signtool = Find-SignTool
$common = @('sign', '/v', '/fd', 'SHA256', '/tr', $Timestamp, '/td', 'SHA256')
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("gt7-sign-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null

try {
  switch ($Route) {
    'azure' {
      Require-Env @('AZURE_SIGNING_ENDPOINT', 'AZURE_SIGNING_ACCOUNT', 'AZURE_SIGNING_PROFILE')
      $metadata = Join-Path $scratch 'metadata.json'
      [ordered]@{
        Endpoint               = $env:AZURE_SIGNING_ENDPOINT
        CodeSigningAccountName = $env:AZURE_SIGNING_ACCOUNT
        CertificateProfileName = $env:AZURE_SIGNING_PROFILE
      } | ConvertTo-Json | Set-Content -LiteralPath $metadata -Encoding ascii
      $routeArgs = @('/dlib', (Find-AzureDlib), '/dmdf', $metadata)
    }
    'pfx' {
      Require-Env @('WINDOWS_PFX_BASE64', 'WINDOWS_PFX_PASSWORD')
      $pfx = Join-Path $scratch 'signing.pfx'
      [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_PFX_BASE64))
      $routeArgs = @('/f', $pfx, '/p', $env:WINDOWS_PFX_PASSWORD)
    }
    default {
      throw "WINDOWS_SIGNING must be azure, signpath or pfx (got '$env:WINDOWS_SIGNING')"
    }
  }

  # signtool takes many files per call; batches keep the command line short.
  for ($i = 0; $i -lt $todo.Count; $i += 32) {
    $batch = $todo[$i..([Math]::Min($i + 31, $todo.Count - 1))]
    & $signtool @common @routeArgs @batch
    if ($LASTEXITCODE -ne 0) { throw "signtool failed (exit $LASTEXITCODE)" }
  }
}
finally {
  Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}

$bad = @($files | Where-Object { -not (Test-Signed $_) })
if ($bad.Count -gt 0) { throw "not validly signed after signing:`n  $($bad -join "`n  ")" }
Write-Host "sign: signed $($todo.Count), verified $($files.Count) file(s) via $Route"
