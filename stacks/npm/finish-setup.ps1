# The two steps that need Administrator on Windows. Run from an elevated
# PowerShell:
#
#     cd stacks\npm
#     powershell -ExecutionPolicy Bypass -File .\finish-setup.ps1
#
#   1. Points the .test names at 127.0.0.1 so the browser can find the proxy.
#      Nginx Proxy Manager routes by hostname but does not do DNS.
#   2. Trusts the local CA so HTTPS shows a padlock instead of a warning.
#
# The Windows counterpart of finish-setup.sh, which covers macOS and Linux only.
# Until this existed the .test names simply did not work on Windows -- the
# browser answered DNS_PROBE_FINISHED_NXDOMAIN and the only way in was
# http://localhost:<port>, which is not what the docs tell you to use.
#
# Both halves are idempotent: re-running changes nothing.
#
# THIS DOES NOT RUN IN THE TOOLBOX, and cannot. The toolbox is a container --
# it has its own hosts file and its own trust store, and writing either would
# change nothing on the machine whose browser you are using. That is why this is
# a host script rather than a make target.

#Requires -Version 5.1

$ErrorActionPreference = 'Stop'

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This needs Administrator. Right-click PowerShell -> Run as administrator, then re-run."
    exit 1
}

$certPath  = Join-Path $PSScriptRoot 'certs\rootCA.pem'

# ------------------------------------------------------------------ hosts --
# Delegated: scripts\hosts-setup.ps1 owns this now, derives the names from the
# stack manifests, and is what wd.cmd bootstrap runs. This script keeps only
# the CA-trust half. (The hosts logic used to be triplicated across three
# scripts with three different matching rules.)
& (Join-Path $PSScriptRoot '..\..\scripts\hosts-setup.ps1')

# --------------------------------------------------------------- CA trust --
if (-not (Test-Path $certPath)) {
    Write-Error "No CA at $certPath -- run the bootstrap first so the certificates exist."
    exit 1
}

# certutil reads PEM here. The store is LocalMachine\Root so every user and
# every browser that uses the Windows store (Chrome, Edge) trusts it. Firefox
# keeps its own store and needs the CA imported separately -- same caveat as
# Linux, and the same non-answer: it is a demonstration stack.
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certPath)
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
$store.Open('ReadWrite')
$already = $store.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
if ($already) {
    Write-Host "    CA already trusted ($($cert.Thumbprint))"
} else {
    Write-Host "==> trusting the local CA in LocalMachine\Root"
    $store.Add($cert)
    Write-Host "    added $($cert.Subject)  $($cert.Thumbprint)"
}
$store.Close()

Write-Host ""
Write-Host "Done. Try https://ignition.test/"
Write-Host ""
Write-Host "To undo:"
Write-Host "  Copy-Item `"$hostsFile.bak`" `"$hostsFile`" -Force"
Write-Host "  Get-ChildItem Cert:\LocalMachine\Root\$($cert.Thumbprint) | Remove-Item"
