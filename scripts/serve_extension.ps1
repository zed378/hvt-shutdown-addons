# Serve the UI extension for Rancher "Developer Load" — the fastest way to verify
# the extension renders in the Harvester/Rancher dashboard, with NO publishing,
# UIPlugin, container image, or Rancher plugin-caching involved.
#
# Usage: .\scripts\serve_extension.ps1
# Requirements: Docker (Node/Yarn run inside the container — nothing needed on host).
#
# It builds the extension package and serves it on http://127.0.0.1:4500. Then, in
# the dashboard:
#   1. Click your avatar -> Preferences -> enable "Extension developer features".
#   2. Extensions -> the three-dot menu (top-right) -> "Developer load".
#   3. Paste the URL printed below (…/hvt-shutdown-ui-<ver>.umd.min.js) and Load.
# The extension loads client-side, so serve it on the SAME machine you browse the
# dashboard from (the browser must be able to reach 127.0.0.1:4500).

param(
    [string]$ExtName = "hvt-shutdown-ui"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Error: docker is required." -ForegroundColor Red; exit 1
}

Write-Host "=== Building + serving the '$ExtName' extension for Developer Load ===" -ForegroundColor Cyan
Write-Host "This runs Node/Yarn inside Docker; first run downloads dependencies (~1-2 min)."
Write-Host ""

# Copy the extension into the container (avoids polluting host node_modules),
# install, build the package, then serve it on :4500. Ctrl+C to stop.
$inner = @"
set -e
cp -r /src/$ExtName /app && cd /app && rm -f .yarnrc
echo '--- yarn install ---';   yarn install
echo '--- yarn build-pkg ---'; yarn build-pkg $ExtName
echo '--- serving on :4500 (Ctrl+C to stop) ---'
yarn serve-pkgs
"@

docker run --rm -it -p 4500:4500 -v "${ProjectRoot}:/src:ro" node:24 bash -c $inner
