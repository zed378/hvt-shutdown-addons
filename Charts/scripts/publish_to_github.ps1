# PowerShell script to publish Helm chart and UI extension to GitHub Pages
# Usage: .\scripts\publish_to_github.ps1
#
# Flow:
#   1. Generate Helm tarball and index.yaml
#   2. Generate UI plugin static files (yarn build-pkg)
#   3. Push tarball + index to 'pages' branch
#   4. Push UI static files to 'pages' branch
#
# GitHub Pages serves both at:
#   https://zed378.github.io/hvt-shutdown-addons
#
# For automated publishing on every push to main, see:
#   .github/workflows/publish-pages.yml
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
#   - Node.js 24+ with yarn or npm installed
#   - Helm installed
#   - Git configured with remote origin
#
# BEFORE FIRST USE: Enable GitHub Pages in repository settings:
#   1. Go to https://github.com/zed378/hvt-shutdown-addons/settings/pages
#   2. Source: Deploy from a branch
#   3. Branch: pages (root /)
#   4. Save

param(
    [string]$Owner    = "zed378",
    [string]$RepoName = "hvt-shutdown-addons",
    [string]$Branch   = "pages"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Configuration ──────────────────────────────────────────────────────
$HelmRepoUrl = "https://${Owner}.github.io/${RepoName}"
$GithubRepo  = "https://github.com/${Owner}/${RepoName}.git"
$UiDir       = "hvt-shutdown-ui"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ChartDir    = Join-Path $ProjectRoot "Charts"
$UiSrcDir    = Join-Path $ProjectRoot $UiDir

$ChartYamlPath = Join-Path $ChartDir "Chart.yaml"
$ChartVersion  = (Select-String -Path $ChartYamlPath -Pattern '^version:\s*(.+)').Matches.Groups[1].Value.Trim()
$ChartName     = (Select-String -Path $ChartYamlPath -Pattern '^name:\s*(.+)').Matches.Groups[1].Value.Trim()
$ChartTgzFile  = Join-Path $ProjectRoot "${ChartName}-${ChartVersion}.tgz"
$IndexYamlFile = Join-Path $ProjectRoot "index.yaml"
$TempDir       = Join-Path $env:TEMP ("hvt-shutdown-pages-" + [Guid]::NewGuid().ToString())

Write-Host ""
Write-Host "=== GitHub Pages Publisher ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Repository:  $GithubRepo"                    -ForegroundColor Yellow
Write-Host "Branch:      $Branch"                        -ForegroundColor Yellow
Write-Host "Helm Repo:   $HelmRepoUrl"                   -ForegroundColor Yellow
Write-Host "Chart:       ${ChartName} v${ChartVersion}"  -ForegroundColor Yellow
Write-Host ""

# ── Prerequisites ──────────────────────────────────────────────────────
foreach ($cmd in @("helm", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Error: '$cmd' is not installed." -ForegroundColor Red; exit 1
    }
}

try   { $nodeVer = node --version; Write-Host "Node: $nodeVer" }
catch { Write-Host "Error: Node.js is not installed." -ForegroundColor Red; exit 1 }

if     (Get-Command yarn -ErrorAction SilentlyContinue) { $pkgMgr = "yarn"; Write-Host "Yarn: $(yarn --version)" }
elseif (Get-Command npm  -ErrorAction SilentlyContinue) { $pkgMgr = "npm";  Write-Host "npm:  $(npm --version)"  }
else   { Write-Host "Error: Neither yarn nor npm is installed." -ForegroundColor Red; exit 1 }
Write-Host ""

$remoteUrl = git remote get-url origin 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: No git remote 'origin' configured." -ForegroundColor Red; exit 1
}
Write-Host "Git remote: $remoteUrl" -ForegroundColor Green

if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "Warning: gh CLI not authenticated." -ForegroundColor Yellow }
    else                     { Write-Host "GitHub CLI: Authenticated"           -ForegroundColor Green  }
} else {
    Write-Host "Note: gh CLI not found — using git credential manager." -ForegroundColor Yellow
}
Write-Host ""

# ── Step 1: Generate Helm tarball and index.yaml ───────────────────────
Write-Host "=== [1/4] Generating Helm tarball and index.yaml ===" -ForegroundColor Cyan

# Copy scripts into Charts/scripts/ for ConfigMap inclusion
$ChartScriptsDir = Join-Path $ChartDir "scripts"
New-Item -ItemType Directory -Force -Path $ChartScriptsDir | Out-Null
Get-ChildItem "$ScriptDir\*.sh"  | Where-Object { $_.Name -ne "publish_to_github.sh" } |
    Copy-Item -Destination $ChartScriptsDir -ErrorAction SilentlyContinue
Get-ChildItem "$ScriptDir\*.ps1"    -ErrorAction SilentlyContinue |
    Copy-Item -Destination $ChartScriptsDir -ErrorAction SilentlyContinue
Get-ChildItem "$ScriptDir\*.mac.sh" -ErrorAction SilentlyContinue |
    Copy-Item -Destination $ChartScriptsDir -ErrorAction SilentlyContinue

helm package $ChartDir --destination $ProjectRoot
if (-not (Test-Path $ChartTgzFile)) {
    Write-Host "Error: Failed to package Helm chart." -ForegroundColor Red; exit 1
}
$ChartFilename = Split-Path $ChartTgzFile -Leaf

helm repo index --url $HelmRepoUrl $ProjectRoot

Write-Host "Tarball:  $ChartFilename"  -ForegroundColor Green
Write-Host "Index:    index.yaml"      -ForegroundColor Green
Write-Host ""
Write-Host "=== index.yaml ===" -ForegroundColor Cyan
Get-Content $IndexYamlFile
Write-Host ""

# ── Step 2: Generate UI plugin static files ────────────────────────────
Write-Host "=== [2/4] Generating UI plugin static files ===" -ForegroundColor Cyan
Set-Location $UiSrcDir

if (-not (Test-Path "node_modules")) {
    Write-Host "Installing UI dependencies..." -ForegroundColor Yellow
    if ($pkgMgr -eq "yarn") { yarn install --frozen-lockfile } else { npm install }
}

yarn build-pkg $UiDir true

$DistPkgDir = Join-Path $UiSrcDir "dist-pkg"
if (-not (Test-Path $DistPkgDir)) {
    Write-Host "Error: Build output not found at $DistPkgDir" -ForegroundColor Red; exit 1
}

$versionDir = Get-ChildItem -Directory $DistPkgDir |
    Where-Object { $_.Name -match '^hvt-shutdown-ui-.+' } |
    Select-Object -First 1
if (-not $versionDir) {
    Write-Host "Error: No versioned build directory found in $DistPkgDir" -ForegroundColor Red
    Get-ChildItem $DistPkgDir; exit 1
}

$UiVersionDir = $versionDir.FullName
$UiVersion    = $versionDir.Name -replace '^hvt-shutdown-ui-', ''
Write-Host "UI version: $UiVersion" -ForegroundColor Green
Write-Host "Output:     $UiVersionDir" -ForegroundColor Green
Write-Host ""

Set-Location $ProjectRoot

# ── Step 3: Push tarball + index to pages branch ───────────────────────
Write-Host "=== [3/4] Pushing tarball and index to '$Branch' branch ===" -ForegroundColor Cyan
$PagesDir = Join-Path $TempDir "pages"
New-Item -ItemType Directory -Force -Path $PagesDir | Out-Null

git clone --branch $Branch --single-branch $GithubRepo $PagesDir 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Cannot clone '$Branch' branch." -ForegroundColor Red
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue; exit 1
}
Write-Host "Cloned '$Branch' branch." -ForegroundColor Green

Copy-Item $ChartTgzFile  (Join-Path $PagesDir $ChartFilename)
Copy-Item $IndexYamlFile (Join-Path $PagesDir "index.yaml")

Set-Location $PagesDir
git config user.email "${Owner}@users.noreply.github.com"
git config user.name  $Owner
git add $ChartFilename index.yaml
git commit -m "Publish ${ChartName} v${ChartVersion}" --allow-empty 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Nothing to commit." -ForegroundColor Yellow }
git push origin $Branch
Write-Host "Pushed: $ChartFilename + index.yaml" -ForegroundColor Green
Write-Host ""

# ── Step 4: Push UI static files to pages branch ──────────────────────
Write-Host "=== [4/4] Pushing UI static files to '$Branch' branch ===" -ForegroundColor Cyan

# Pull latest after the helm push to avoid conflicts
git pull origin $Branch

$DestVersionDir = Join-Path $PagesDir "hvt-shutdown-ui-$UiVersion"
if (Test-Path $DestVersionDir) { Remove-Item -Recurse -Force $DestVersionDir }
New-Item -ItemType Directory -Force -Path $DestVersionDir | Out-Null
Copy-Item -Recurse -Path "$UiVersionDir\*" -Destination $DestVersionDir
Copy-Item (Join-Path $UiVersionDir "package.json") (Join-Path $PagesDir "package.json") -Force

git add "hvt-shutdown-ui-$UiVersion" package.json
git commit -m "Publish UI v${UiVersion}" --allow-empty 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Nothing to commit." -ForegroundColor Yellow }
git push origin $Branch
Write-Host "Pushed: hvt-shutdown-ui-$UiVersion/ + package.json" -ForegroundColor Green

Set-Location $ProjectRoot

# ── Cleanup ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Cleaning up ===" -ForegroundColor Yellow
$cleanupScripts = @(
    "publish_to_github.sh", "publish_to_github.ps1",
    "publish_chart.sh",     "publish_chart.ps1", "publish_chart.mac.sh",
    "create_release.sh",    "create_release.ps1"
)
foreach ($s in $cleanupScripts) {
    Remove-Item (Join-Path $ChartDir "scripts\$s") -Force -ErrorAction SilentlyContinue
}
Remove-Item $ChartTgzFile  -Force -ErrorAction SilentlyContinue
Remove-Item $IndexYamlFile -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "charts-output") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectRoot "releases")      -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green

Write-Host ""
Write-Host "=== Published successfully ===" -ForegroundColor Green
Write-Host ""
Write-Host "Helm index:   https://${Owner}.github.io/${RepoName}/index.yaml"                                              -ForegroundColor Green
Write-Host "Helm chart:   https://${Owner}.github.io/${RepoName}/${ChartFilename}"                                        -ForegroundColor Green
Write-Host "UIPlugin URL: https://${Owner}.github.io/${RepoName}"                                                         -ForegroundColor Green
Write-Host "UI plugin:    https://${Owner}.github.io/${RepoName}/hvt-shutdown-ui-${UiVersion}/package/index.html"         -ForegroundColor Green
Write-Host ""
Write-Host "To add the Helm repo:" -ForegroundColor Cyan
Write-Host "  helm repo add hvt-shutdown ${HelmRepoUrl}"
Write-Host "  helm repo update"
Write-Host "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system"
Write-Host ""
Write-Host "NOTE: If you get 404 errors, enable GitHub Pages:" -ForegroundColor Yellow
Write-Host "  1. Go to https://github.com/${Owner}/${RepoName}/settings/pages"
Write-Host "  2. Source: Deploy from a branch"
Write-Host "  3. Branch: pages (root /)"
Write-Host "  4. Save"