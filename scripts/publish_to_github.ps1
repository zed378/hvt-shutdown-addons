# PowerShell script to publish the Helm charts to GitHub Pages
# Usage: .\scripts\publish_to_github.ps1
#
# Each chart is published into a folder on the 'pages' branch, and EVERY folder
# gets its OWN index.yaml. A chart's add-on `repo:` URL must match its folder:
#
#   Charts/          -> node-shutdown        -> <pages>/            (repo root)
#   DashboardChart/  -> harvester-dashboard  -> <pages>/dashboard/  (own index)
#   VpnChart/        -> vpn                  -> <pages>/vpn/       (own index)
#
# GitHub Pages serves the Helm chart repository at:
#   https://zed378.github.io/hvt-shutdown-addons
#
# Requirements: helm, git (and push access to the 'pages' branch).

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

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ReadmeFile  = Join-Path $ProjectRoot "README.md"
$TempDir     = Join-Path $env:TEMP ("hvt-shutdown-pages-" + [Guid]::NewGuid().ToString())

# Chart directory -> pages subfolder. "" means the repo root.
$Charts = @(
    @{ Dir = "Charts";         Folder = "" },
    @{ Dir = "DashboardChart"; Folder = "dashboard" },
    @{ Dir = "VpnChart";       Folder = "vpn" }
)

Write-Host ""
Write-Host "=== GitHub Pages Publisher (per-folder indexes) ===" -ForegroundColor Cyan
Write-Host "Repository:  $GithubRepo" -ForegroundColor Yellow
Write-Host "Helm Repo:   $HelmRepoUrl" -ForegroundColor Yellow
Write-Host ""

# ── Prerequisites ──────────────────────────────────────────────────────
foreach ($cmd in @("helm", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Error: '$cmd' is not installed." -ForegroundColor Red; exit 1
    }
}
$remoteUrl = git remote get-url origin 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: No git remote 'origin' configured." -ForegroundColor Red; exit 1
}
Write-Host "Git remote: $remoteUrl" -ForegroundColor Green
Write-Host ""

# ── Step 1: package each chart into its folder's staging dir, index each ─
Write-Host "=== [1/2] Packaging + indexing charts ===" -ForegroundColor Cyan
$Folders = $Charts | ForEach-Object { $_.Folder } | Select-Object -Unique
$Staged  = @{}

foreach ($folder in $Folders) {
    $label = if ($folder) { $folder } else { "root" }
    $stage = Join-Path $TempDir "stage-$label"
    New-Item -ItemType Directory -Force -Path $stage | Out-Null

    $packagedAny = $false
    foreach ($c in ($Charts | Where-Object { $_.Folder -eq $folder })) {
        $chartPath = Join-Path $ProjectRoot $c.Dir
        $chartYaml = Join-Path $chartPath "Chart.yaml"
        if (-not (Test-Path $chartYaml)) {
            Write-Host "Skip: $($c.Dir) has no Chart.yaml" -ForegroundColor Yellow; continue
        }
        $name    = (Select-String -Path $chartYaml -Pattern '^name:\s*(.+)').Matches.Groups[1].Value.Trim()
        $version = (Select-String -Path $chartYaml -Pattern '^version:\s*(.+)').Matches.Groups[1].Value.Trim()

        helm package $chartPath --destination $stage | Out-Null
        if (-not (Test-Path (Join-Path $stage "${name}-${version}.tgz"))) {
            Write-Host "Error: Failed to package $($c.Dir) ($name)." -ForegroundColor Red; exit 1
        }
        Write-Host ("Packaged: {0} v{1} -> /{2}" -f $name, $version, $label) -ForegroundColor Green
        $packagedAny = $true
    }
    if (-not $packagedAny) { continue }

    # Index URL must match where the folder is served from.
    $indexUrl = if ($folder) { "$HelmRepoUrl/$folder" } else { $HelmRepoUrl }
    helm repo index --url $indexUrl $stage
    Write-Host "Indexed: $indexUrl/index.yaml" -ForegroundColor Green
    $Staged[$folder] = $stage
}
Write-Host ""

# ── Step 2: push every folder to the pages branch ───────────────────────
Write-Host "=== [2/2] Pushing to '$Branch' branch ===" -ForegroundColor Cyan
$PagesDir = Join-Path $TempDir "pages"
New-Item -ItemType Directory -Force -Path $PagesDir | Out-Null

git clone --branch $Branch --single-branch $GithubRepo $PagesDir 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Cannot clone '$Branch' branch." -ForegroundColor Red
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue; exit 1
}
Write-Host "Cloned '$Branch' branch." -ForegroundColor Green

foreach ($folder in @($Staged.Keys)) {
    $dest = if ($folder) { Join-Path $PagesDir $folder } else { $PagesDir }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item (Join-Path $Staged[$folder] "*") $dest -Force
    $label = if ($folder) { "/$folder" } else { "/ (root)" }
    Write-Host "Staged files into $label" -ForegroundColor Green
}
if (Test-Path $ReadmeFile) { Copy-Item $ReadmeFile (Join-Path $PagesDir "README.md") }

Set-Location $PagesDir
git config user.email "${Owner}@users.noreply.github.com"
git config user.name  $Owner
git add -A
git commit -m "Publish charts (per-folder indexes)" --allow-empty 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "Nothing to commit." -ForegroundColor Yellow }
git push origin $Branch
Write-Host "Pushed." -ForegroundColor Green

Set-Location $ProjectRoot

# ── Cleanup ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Cleaning up ===" -ForegroundColor Yellow
Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
Write-Host "Done." -ForegroundColor Green

Write-Host ""
Write-Host "=== Published successfully ===" -ForegroundColor Green
Write-Host "node-shutdown: ${HelmRepoUrl}/index.yaml"            -ForegroundColor Green
Write-Host "Dashboard:     ${HelmRepoUrl}/dashboard/index.yaml"  -ForegroundColor Green
Write-Host "VPN:           ${HelmRepoUrl}/vpn/index.yaml"        -ForegroundColor Green
Write-Host ""
Write-Host "Add-on repo: URLs must match these folders:" -ForegroundColor Cyan
Write-Host "  Charts/addon.yaml          repo: ${HelmRepoUrl}"
Write-Host "  DashboardChart/addon.yaml  repo: ${HelmRepoUrl}/dashboard"
Write-Host "  VpnChart/addon.yaml        repo: ${HelmRepoUrl}/vpn"
Write-Host ""
Write-Host "Install order: enable 'harvester-dashboard' (the UI), then feature add-ons (node-shutdown, vpn)."
