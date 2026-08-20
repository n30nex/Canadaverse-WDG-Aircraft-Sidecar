[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $PackageRoot,
    [Parameter(Mandatory)] [string] $InputFile,
    [string] $ExpectedVersion = "1.1.0"
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PackageRoot "runtime\python.exe"
$sidecar = Join-Path $PackageRoot "app\sidecar.py"
$decoder = Join-Path $PackageRoot "decoder\dump1090.exe"
$config = Join-Path $PackageRoot "decoder\wdg-dump1090.cfg"

$reportedVersion = & $python $sidecar --version
if ($LASTEXITCODE -ne 0 -or $reportedVersion.Trim() -ne $ExpectedVersion) {
    throw "Portable runtime version check failed: $reportedVersion"
}

$startInfo = [Diagnostics.ProcessStartInfo]::new($decoder)
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
foreach ($argument in @("--config", $config, "--infile", $InputFile, "--net")) {
    $startInfo.ArgumentList.Add($argument)
}
$process = [Diagnostics.Process]::Start($startInfo)

try {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        if ($process.HasExited) {
            throw "Decoder exited before opening its SBS listener (code $($process.ExitCode))"
        }
        $listeners = @(Get-NetTCPConnection -State Listen -OwningProcess $process.Id -ErrorAction SilentlyContinue)
        if ($listeners.Count -gt 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)

    if ($listeners.Count -ne 1) {
        throw "Expected exactly one decoder listener, found $($listeners.Count)"
    }
    $listener = $listeners[0]
    if ($listener.LocalAddress -ne "127.0.0.1" -or $listener.LocalPort -ne 30003) {
        throw "Unexpected decoder listener: $($listener.LocalAddress):$($listener.LocalPort)"
    }

    $client = [Net.Sockets.TcpClient]::new()
    $client.Connect("127.0.0.1", 30003)
    $reader = [IO.StreamReader]::new($client.GetStream())
    $lineTask = $reader.ReadLineAsync()
    if (-not $lineTask.Wait([TimeSpan]::FromSeconds(20))) {
        throw "SBS listener opened but did not produce an aircraft message"
    }
    $line = $lineTask.Result
    if (-not $line.StartsWith("MSG,")) {
        throw "Expected an SBS MSG line, received: $line"
    }
    $reader.Dispose()
    $client.Dispose()
    Write-Output "Windows package passed: version $reportedVersion, one loopback SBS listener, live MSG feed."
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
}
