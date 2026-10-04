# Run on the Windows PC; the Orange Pi uses update.sh.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9._:-]*$')]
    [string]$PiHost,
    [ValidatePattern('^[a-z_][a-z0-9_-]*[$]?$')]
    [string]$User = 'root',
    [ValidateRange(1, 65535)]
    [int]$Port = 22,
    [string]$IdentityFile,
    [switch]$Rollback,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$packageDir = Split-Path -Parent $PSScriptRoot
$sshArguments = @('-p', "$Port", '-l', $User, '-o', 'ConnectTimeout=10',
    '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3')
$scpArguments = @('-P', "$Port", '-o', 'ConnectTimeout=10')
if ($IdentityFile) {
    $key = (Resolve-Path -LiteralPath $IdentityFile).Path
    $sshArguments += @('-i', $key)
    $scpArguments += @('-i', $key)
}

function Invoke-CheckedCommand {
    param([string]$Executable, [string[]]$Arguments)
    # Windows PowerShell 5.1 can turn native stderr into a PowerShell error.
    # SSH's exit status, including authentication failure, is the result to use.
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = $null
    & $Executable @Arguments
    if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE."
    }
}

if ($Rollback) {
    Write-Host "Restore the previous receiver files on ${User}@${PiHost}:$Port."
    if ($DryRun) { return }
    $ssh = (Get-Command ssh -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    Invoke-CheckedCommand $ssh ($sshArguments + @('-tt', $PiHost,
        'if [ $(id -u) -eq 0 ]; then bash /var/lib/cluster-receiver/updates/update.sh rollback; else sudo bash /var/lib/cluster-receiver/updates/update.sh rollback; fi'))
    return
}

$files = @('README.md', 'requirements.txt', 'cluster-hdmi.service')
$files += @(Get-ChildItem -LiteralPath $packageDir -Filter '*.py' -File | ForEach-Object { $_.Name })
$files += @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.sh' -File | ForEach-Object { 'scripts/' + $_.Name })
$files = @($files | Sort-Object -Unique)
foreach ($required in @('cluster_receiver.py', 'hdmi_display.py', 'scripts/update.sh')) {
    if ($files -notcontains $required) { throw "Package is missing $required." }
}
Write-Host "Update ${User}@${PiHost}:$Port with $($files.Count) receiver files."
Write-Host 'Keep the installed systemd unit, account, display options and boot settings.'
if ($DryRun) {
    $files | ForEach-Object { Write-Output "  $_" }
    Write-Host 'Dry run: no SSH connection or file changes.'
    return
}

$ssh = (Get-Command ssh -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$scp = (Get-Command scp -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('cluster-deploy-' + [guid]::NewGuid().ToString('N'))
$payload = Join-Path $tempRoot 'payload'
$remoteDirectory = $null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    New-Item -ItemType Directory -Path $payload -Force | Out-Null
    $checksums = @()
    foreach ($relative in $files) {
        if ($relative -notmatch '^([a-zA-Z0-9_]+[.]py|README[.]md|requirements[.]txt|cluster-hdmi[.]service|scripts/[a-zA-Z0-9_-]+[.]sh)$') {
            throw "Unexpected package filename: $relative"
        }
        $source = Join-Path $packageDir $relative
        if ((Get-Item -LiteralPath $source).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Package files must not be symlinks: $relative"
        }
        $destination = Join-Path $payload $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        # Git checkouts on Windows may have CRLF. Upload UTF-8/LF text to Linux.
        $contents = [System.IO.File]::ReadAllText($source).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText($destination, $contents, $utf8)
        $hash = [System.BitConverter]::ToString($sha256.ComputeHash([System.IO.File]::ReadAllBytes($destination))).Replace('-', '').ToLowerInvariant()
        $checksums += "$hash  $relative"
    }
    [System.IO.File]::WriteAllText((Join-Path $payload 'SHA256SUMS'), (($checksums -join "`n") + "`n"), $utf8)

    $response = @(Invoke-CheckedCommand $ssh ($sshArguments + @($PiHost, 'mktemp -d /tmp/cluster-update.XXXXXXXX')))
    $remoteDirectory = ($response -join "`n").Trim()
    if ($remoteDirectory -notmatch '^/tmp/cluster-update[.][a-zA-Z0-9]{8}$') {
        $remoteDirectory = $null
        throw 'SSH did not return a valid staging directory; check remote shell startup output.'
    }
    $scpHost = $PiHost
    if ($PiHost.Contains(':')) { $scpHost = "[$PiHost]" }
    Push-Location -LiteralPath $tempRoot
    try {
        Invoke-CheckedCommand $scp ($scpArguments + @('-r', 'payload', "${User}@${scpHost}:$remoteDirectory/"))
    } finally {
        Pop-Location
    }
    $command = "if [ `$(id -u) -eq 0 ]; then bash '$remoteDirectory/payload/scripts/update.sh' apply '$remoteDirectory/payload'; else sudo bash '$remoteDirectory/payload/scripts/update.sh' apply '$remoteDirectory/payload'; fi"
    Invoke-CheckedCommand $ssh ($sshArguments + @('-tt', $PiHost, $command))
} finally {
    $sha256.Dispose()
    if ($remoteDirectory) {
        try {
            Invoke-CheckedCommand $ssh ($sshArguments + @($PiHost, "rm -rf -- '$remoteDirectory'"))
        } catch {
            Write-Warning "Remote staging cleanup failed: $remoteDirectory"
        }
    }
    # Delete only this invocation's verified temporary workspace.
    $fullTempRoot = [System.IO.Path]::GetFullPath($tempRoot)
    $tempParent = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $fullTempRoot.StartsWith($tempParent, [System.StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Leaf $fullTempRoot) -notmatch '^cluster-deploy-[a-f0-9]{32}$') {
        throw 'Refusing cleanup outside the deployment temporary workspace.'
    }
    if (Test-Path -LiteralPath $fullTempRoot) { Remove-Item -LiteralPath $fullTempRoot -Recurse -Force }
}
