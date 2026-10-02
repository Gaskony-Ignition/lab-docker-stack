# Point the stack's .test names at the local Nginx Proxy Manager. Windows half
# of scripts/hosts-setup.sh -- see that file for why this runs on the HOST and
# not in the toolbox, and for what breaks without it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\hosts-setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\hosts-setup.ps1 -Check
#   powershell -ExecutionPolicy Bypass -File scripts\hosts-setup.ps1 -Prune
#
# Adding is automatic; REMOVING is opt-in. A retired stack leaves its name
# resolving to the proxy, so every run reports a name no stack declares -- but
# wd.cmd calls this on ordinary invocations and silently deleting lines from
# the machine's hosts file every time somebody runs a command is worse than the
# untidiness it fixes. Report always, delete only when asked. See the bash half
# for the full reasoning; the two must behave the same.
#
# A separate implementation rather than reusing the bash one because Git Bash is
# not a given on Windows, and this is a prerequisite step -- needing a shell the
# machine may not have, to install the thing that makes the stack reachable, is
# the wrong way round.
param([switch]$Check, [switch]$Prune)

$ErrorActionPreference = 'Stop'

$Marker   = '# Ignition-Demos-Stack .test names -- added by scripts/hosts-setup.ps1'
$TargetIp = '127.0.0.1'

# Derived from the stack manifests, same as the bash half and the proxy-host
# table -- one TEST_HOST line per stack that wants a name. Select-String with a
# line regex is the whole parser; the grammar (make validate) guarantees bare,
# non-empty values, and the .gitattributes LF pin plus Trim() covers CRLF.
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Names = @(Select-String -Path (Join-Path $RepoRoot 'stacks\*\stack.meta') `
             -Pattern '^TEST_HOST=(.+)$' |
           ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() })
if ($Names.Count -eq 0) { Write-Error "no stack.meta files under $RepoRoot\stacks"; exit 1 }

function Test-Administrator {
  ([Security.Principal.WindowsPrincipal] `
   [Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# WRITE THROUGH A TEMP FILE, NEVER Set-Content ON THE HOSTS FILE ITSELF.
#
# Set-Content opens the target for write -- which TRUNCATES it -- and only then
# discovers it cannot finish. Something on Windows holds this file open often
# enough to matter (`The process cannot access the file ... because it is being
# used by another process`), and the result is a ZERO-BYTE hosts file: name
# resolution gone for the whole machine, including whatever you would use to
# fix it. Seen twice on 22/08/2026, and elevation makes no difference -- the
# run that did it was already Administrator.
#
# copy replaces the file in one step and fails without touching it. This is
# what the bash half has always done with mktemp + cp, and the comment there
# says why; the two halves must not differ on something this consequential.
function Write-HostsFile([string[]]$lines) {
  $tmp = Join-Path $env:TEMP ("wd-hosts-" + [guid]::NewGuid().ToString("N"))
  Set-Content -Path $tmp -Value $lines -Encoding ASCII
  try {
    Copy-Item -Path $tmp -Destination $HostsFile -Force -ErrorAction Stop
    return $true
  } catch {
    Write-Warning "hosts-setup: could not write ${HostsFile}: $($_.Exception.Message)"
    return $false
  } finally {
    Remove-Item $tmp -ErrorAction SilentlyContinue
  }
}

$HostsFile = Join-Path $env:WINDIR 'System32\drivers\etc\hosts'
if (-not (Test-Path $HostsFile)) { Write-Error "no hosts file at $HostsFile"; exit 1 }

$content = Get-Content $HostsFile

# Whole-word match on a non-comment line. A substring test would see a name
# inside a comment, or inside a longer name, and skip one that is not mapped.
$missing = @($Names | Where-Object {
  $n = [regex]::Escape($_)
  -not ($content | Where-Object { $_ -match "^[^#]*\s$n(\s|$)" })
})

# Names in the file that no stack declares. Narrow on purpose, and identical to
# the bash half: a non-comment line with exactly two fields, the second ending
# in .test. A comment, a multi-name line or a non-.test name is never touched --
# this file belongs to the machine, not to this repo.
function Test-StaleLine([string]$line) {
  if ($line -match '^\s*#') { return $false }
  $f = -split $line
  if ($f.Count -ne 2) { return $false }
  if ($f[1] -notmatch '\.test$') { return $false }
  return -not ($Names -contains $f[1])
}
$stale = @($content | Where-Object { Test-StaleLine $_ } |
           ForEach-Object { (-split $_)[1] } | Sort-Object -Unique)
if ($stale.Count -gt 0) {
  Write-Host "hosts-setup: no stack declares these, still in ${HostsFile}: $($stale -join ' ')"
}

if ($Prune -and $stale.Count -gt 0) {
  if (-not (Test-Administrator)) {
    Write-Warning "hosts-setup: -Prune needs Administrator. Remove by hand: $($stale -join ' ')"
  } else {
    $kept = @($content | Where-Object { -not (Test-StaleLine $_) })
    if (Write-HostsFile $kept) {
      Write-Host "hosts-setup: removed $($stale -join ' ')"
      $content = $kept
    }
  }
}

if ($missing.Count -eq 0) {
  if (-not $Check) { Write-Host "hosts-setup: all .test names already present in $HostsFile" }
  exit 0
}

if ($Check) {
  Write-Host "hosts-setup: missing from ${HostsFile}: $($missing -join ' ')"
  exit 1
}

if (-not (Test-Administrator)) {
  Write-Host "hosts-setup: $HostsFile needs Administrator. Requesting elevation..."
  # Re-launch this same script elevated. The UAC prompt is the point: editing
  # the hosts file silently, on a machine that may not be the user's own, would
  # be the wrong default.
  # Forward -Prune: without it an elevated re-launch adds the missing names and
  # silently drops the removal the caller asked for, which reads as a prune that
  # did not work.
  $relaunch = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath)
  if ($Prune) { $relaunch += '-Prune' }
  $p = Start-Process -FilePath 'powershell' -Verb RunAs -Wait -PassThru `
       -ArgumentList $relaunch
  if ($p.ExitCode -ne 0) {
    Write-Warning "hosts-setup: elevated run failed. Add these lines to $HostsFile by hand:"
    $missing | ForEach-Object { Write-Host "       $TargetIp $_" }
    exit 1
  }
  exit 0
}

# Append via a rewrite of the whole file rather than Add-Content in place: a
# half-written hosts file breaks name resolution for the entire machine,
# including whatever you would use to fix it.
$new = @($content) + @('', $Marker) + @($missing | ForEach-Object { "$TargetIp $_" })
if (-not (Write-HostsFile $new)) { exit 1 }

# Windows caches negative lookups, so a name added now still fails to resolve
# until the cache is dropped -- which reads exactly like the entry not working.
ipconfig /flushdns | Out-Null

Write-Host "hosts-setup: added $($missing -join ' ') -> $TargetIp"
