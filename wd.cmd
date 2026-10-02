@echo off
rem Ignition-Demos-Stack on Windows, from cmd or PowerShell.
rem
rem   .\wd.cmd bootstrap           first run on this machine, start to finish
rem   .\wd.cmd status              what is running
rem   .\wd.cmd deploy PROJECT=Site1  the ordinary work loop
rem   .\wd.cmd bash                a shell inside the toolbox
rem
rem The leading .\ is required in PowerShell, which does not run commands from
rem the current directory -- without it you get "The term 'wd.cmd' is not
rem recognized", which reads as a missing file that is plainly there. In cmd.exe
rem either form works.
rem
rem Everything runs inside the toolbox image, so this machine needs only Docker
rem Desktop and git. There is no make, no node, no python and no Playwright to
rem install here.
rem
rem This is a separate file from `wd` rather than a wrapper around it because
rem cmd is the one shell on Windows that is always present -- Git Bash is
rem usually there, but "usually" is how the whole portability problem started.
rem It also sidesteps MSYS path rewriting entirely: cmd does not touch the
rem arguments, so the volume mounts arrive as written.

setlocal enabledelayedexpansion

set "IMAGE=wd-toolbox"
rem Keep this in step with TAG in the `wd` script beside it.
set "TAG=2"

rem %~dp0 is this file's directory with a trailing backslash; strip it so the
rem mount source has no stray separator.
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

where docker >nul 2>&1
if errorlevel 1 (
  echo wd: docker is not on PATH.
  echo     Install Docker Desktop and make sure it is running.
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo wd: cannot talk to the Docker daemon. Is Docker Desktop running?
  exit /b 1
)

rem The shared network, created before the toolbox joins it -- a container
rem cannot create the network it is being attached to.
docker network inspect backbone >nul 2>&1
if errorlevel 1 (
  docker network create backbone >nul
  echo wd: created the shared 'backbone' network
)

docker image inspect %IMAGE%:%TAG% >nul 2>&1
if errorlevel 1 (
  echo wd: building the toolbox image ^(first run only, a few minutes^)...
  docker build -t %IMAGE%:%TAG% "%REPO%\tools\toolbox"
  if errorlevel 1 exit /b 1
)

rem No --user here, and that is right rather than an omission: Docker Desktop
rem maps ownership through its VM, so the host uid means nothing inside and
rem forcing one is how you get a container that cannot write its own HOME.
rem
rem The socket is mounted at its Linux path even on Windows -- Docker Desktop
rem proxies /var/run/docker.sock inside Linux containers regardless of the host
rem being Windows.
rem TZ is forwarded only if this machine actually has one -- see the long note
rem in `wd`. Windows almost never sets it, and `-e "TZ=%TZ%"` then handed every
rem container an EMPTY TZ, which beats `TZ=Australia/Adelaide` in the .env files
rem because compose prefers the shell environment. The gateways ran UTC.
rem --- the .test names ---------------------------------------------------
rem
rem Host-side prerequisite the toolbox cannot satisfy for itself: the hosts file
rem belongs to the machine and the container has its own. Without it every
rem .test name fails to resolve while every service still answers on its host
rem port -- so the stack looks completely healthy and the whole proxy layer,
rem including the redundant pair's failover front door, is unreachable by name.
rem
rem `bootstrap` fixes it and will raise a UAC prompt; everything else only warns,
rem because demanding elevation on every `wd status` would be intolerable.
echo %* | findstr /i /c:"bootstrap" >nul
if not errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%\scripts\hosts-setup.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%\scripts\hosts-setup.ps1" -Check >nul 2>&1
  if errorlevel 1 (
    echo wd: the .test names are not in your hosts file -- https://ignition.test
    echo     will not resolve. Fix with: powershell -ExecutionPolicy Bypass -File scripts\hosts-setup.ps1
  )
)

set "TZ_ARG="
if defined TZ set "TZ_ARG=-e TZ=%TZ%"

REM -t ONLY WITH A REAL TERMINAL, same rule the bash half has always had.
REM
REM This hard-coded -it, so `wd.cmd` could not run from anything that is not a
REM person at a console: over SSH, from a scheduled task, from a CI runner,
REM docker answers `the input device is not a TTY` and nothing runs at all.
REM That is exactly how the work machine gets driven, so the one front door
REM Windows has did not work in the one context it is most needed.
REM
REM cmd has no `[ -t 0 ]`, so ask PowerShell -- which this file already shells
REM out to twice above. Do NOT redirect its stdout here: that would make
REM IsOutputRedirected true and this would always decide "no terminal".
REM WD_NO_TTY=1 forces it off for a context the test cannot see.
REM One command and one errorlevel, deliberately -- an `if (` block would have
REM to expand %TTY_ARG% at parse time, which is the cmd trap this file's own
REM .gitattributes entry is about.
set "TTY_ARG=-t"
powershell -NoProfile -Command "if ($env:WD_NO_TTY -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected) { exit 1 } else { exit 0 }" 2>nul
if errorlevel 1 set "TTY_ARG="

docker run --rm -i %TTY_ARG% ^
  -v "%REPO%:/work" ^
  -v /var/run/docker.sock:/var/run/docker.sock ^
  --network backbone ^
  %TZ_ARG% ^
  -e "MODULES_MIRROR=%MODULES_MIRROR%" ^
  -w /work ^
  %IMAGE%:%TAG% %*

exit /b %errorlevel%
