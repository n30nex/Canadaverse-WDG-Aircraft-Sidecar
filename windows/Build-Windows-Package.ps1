[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $DecoderPath,
    [Parameter(Mandatory)] [string] $DecoderLicensePath,
    [Parameter(Mandatory)] [string] $DecoderSourcePath,
    [Parameter(Mandatory)] [string] $OutputDirectory,
    [string] $Version = "1.1.0"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pythonVersion = "3.13.15"
$pythonName = "python-$pythonVersion-embed-amd64.zip"
$pythonUri = "https://www.python.org/ftp/python/$pythonVersion/$pythonName"
$pythonSha256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
$gplSha256 = "edaef632cbb643e4e7a221717a6c441a4c1a7c918e6e4d56debc3d8739b233f6"
$gplPath = Join-Path $repo "windows\GPL-2.0.txt"
$decoderCommit = "252cef736d24e146545aecf7f316c289ef82b3b4"
$packageName = "WDG-Aircraft-Sidecar-Windows-x64-v$Version"
$sourceName = "WDG-Dump1090-Corresponding-Source-v$Version"

foreach ($required in @($DecoderPath, $DecoderLicensePath, $DecoderSourcePath)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required input does not exist: $required"
    }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$packageRoot = Join-Path $OutputDirectory $packageName
$sourceRoot = Join-Path $OutputDirectory $sourceName
$packageArchive = "$packageRoot.zip"
$sourceArchive = "$sourceRoot.zip"
foreach ($target in @($packageRoot, $sourceRoot, $packageArchive, $sourceArchive)) {
    if (Test-Path -LiteralPath $target) {
        throw "Refusing to overwrite existing build output: $target"
    }
}

$downloadRoot = Join-Path $OutputDirectory "verified-downloads"
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
$pythonArchive = Join-Path $downloadRoot $pythonName

function Invoke-DownloadWithRetry([string] $Uri, [string] $Destination) {
    foreach ($attempt in 1..3) {
        try {
            Invoke-WebRequest -Uri $Uri -OutFile $Destination
            return
        }
        catch {
            if ($attempt -eq 3) { throw }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
}

function Assert-Sha256([string] $Path, [string] $Expected) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Expected) {
        throw "SHA256 mismatch for $Path. Expected $Expected, got $actual"
    }
}

Invoke-DownloadWithRetry $pythonUri $pythonArchive
Assert-Sha256 $pythonArchive $pythonSha256
Assert-Sha256 $gplPath $gplSha256

New-Item -ItemType Directory -Path $packageRoot | Out-Null
foreach ($folder in @("runtime", "app", "decoder")) {
    New-Item -ItemType Directory -Path (Join-Path $packageRoot $folder) | Out-Null
}
Expand-Archive -LiteralPath $pythonArchive -DestinationPath (Join-Path $packageRoot "runtime")
Copy-Item -LiteralPath (Join-Path $repo "src\sidecar.py") -Destination (Join-Path $packageRoot "app\sidecar.py")
Copy-Item -LiteralPath $DecoderPath -Destination (Join-Path $packageRoot "decoder\dump1090.exe")
Copy-Item -LiteralPath $DecoderLicensePath -Destination (Join-Path $packageRoot "decoder\Dump1090-LICENSE.txt")
Copy-Item -LiteralPath $gplPath -Destination (Join-Path $packageRoot "decoder\GPL-2.0.txt")
Copy-Item -LiteralPath (Join-Path $repo "windows\wdg-dump1090.cfg") -Destination (Join-Path $packageRoot "decoder\wdg-dump1090.cfg")
Copy-Item -LiteralPath (Join-Path $repo "windows\Start-WDG-Aircraft-Sidecar.cmd") -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $repo "windows\Configure-WDG-API-Key.cmd") -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $repo "windows\Status.cmd") -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $repo "windows\README-WINDOWS.txt") -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $repo "windows\THIRD-PARTY-NOTICES.txt") -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $repo "LICENSE") -Destination $packageRoot

@"
WDG Aircraft Sidecar: $Version
CPython: $pythonVersion
CPython archive SHA256: $pythonSha256
Dump1090 source commit: $decoderCommit
Dump1090 patch: windows/dump1090-sbs-only.patch
"@ | Set-Content -LiteralPath (Join-Path $packageRoot "BUILD-METADATA.txt") -Encoding utf8

$reportedVersion = & (Join-Path $packageRoot "runtime\python.exe") (Join-Path $packageRoot "app\sidecar.py") --version
if ($LASTEXITCODE -ne 0 -or $reportedVersion.Trim() -ne $Version) {
    throw "Bundled sidecar version check failed: $reportedVersion"
}

New-Item -ItemType Directory -Path $sourceRoot | Out-Null
Get-ChildItem -LiteralPath $DecoderSourcePath -Force |
    Where-Object { $_.Name -notin @(".git", "dump1090.exe") } |
    Copy-Item -Destination $sourceRoot -Recurse
Copy-Item -LiteralPath $gplPath -Destination (Join-Path $sourceRoot "COPYING")
Copy-Item -LiteralPath (Join-Path $repo "windows\dump1090-sbs-only.patch") -Destination (Join-Path $sourceRoot "WDG-SBS-ONLY.patch")
@"
CORRESPONDING SOURCE AND BUILD
==============================

This is gvanem/Dump1090 commit $decoderCommit with WDG-SBS-ONLY.patch applied.
The shipped x64 decoder was built on Windows with:

  cmake -S src -B build -G "Visual Studio 17 2022" -A x64
  cmake --build build --config Release

The patch binds the SBS listener to loopback, omits RAW/HTTP listeners, and
uses the static MSVC runtime. It is included both applied and as a patch file.
"@ | Set-Content -LiteralPath (Join-Path $sourceRoot "SOURCE-BUILD.txt") -Encoding utf8

Compress-Archive -LiteralPath $packageRoot -DestinationPath $packageArchive -CompressionLevel Optimal
Compress-Archive -LiteralPath $sourceRoot -DestinationPath $sourceArchive -CompressionLevel Optimal

foreach ($archive in @($packageArchive, $sourceArchive)) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($archive))" |
        Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
    Write-Output "$archive ($hash)"
}
