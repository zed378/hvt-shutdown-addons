# PowerShell script to publish Helm chart to GitHub as a Helm repository
# Usage: .\scripts\publish_to_github.ps1
#
# This script:
# 1. Packages the Helm chart to root directory
# 2. Generates index.yaml in root directory
# 3. Checkout only generated files to 'pages' branch via git worktree
# 4. Deletes generated files from root after publish
# 5. GitHub Pages will serve the files automatically
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
#   - Helm installed
#   - Git configured with remote origin
#
# BEFORE FIRST USE: Enable GitHub Pages in repository settings:
#   1. Go to https://github.com/zed378/hvt-shutdown-addons/settings/pages
#   2. Source: Deploy from a branch
#   3. Branch: pages (root /)
#   4. Save

param(
    [string]$Owner = "zed378",
    [string]$RepoName = "hvt-shutdown-addons",
    [string]$Branch = "pages"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$HelmRepoUrl = "https://${Owner}.github.io/${RepoName}"
$GithubRepo = "https://github.com/${Owner}/${RepoName}.git"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ChartDir = Join-Path $ProjectRoot "Charts"

# Generated files in root directory
$ChartTgzFile = Join-Path $ProjectRoot "node-shutdown-1.0.0.tgz"
$IndexYamlFile = Join-Path $ProjectRoot "index.yaml"
$TempDir = New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "temp-github-pages-$$") | Select-Object -ExpandProperty FullName

Write-Host ""
Write-Host "=== GitHub Helm Repository Publisher ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Repository: $GithubRepo" -ForegroundColor Yellow
Write-Host "Branch: $Branch" -ForegroundColor Yellow
Write-Host "Helm Repo URL: $HelmRepoUrl" -ForegroundColor Yellow
Write-Host ""

# Check prerequisites
foreach ($cmd in @("helm", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Error: $cmd is not installed." -ForegroundColor Red
        exit 1
    }
}

# Check git remote
$remoteUrl = git remote get-url origin 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: No git remote 'origin' configured." -ForegroundColor Red
    Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "Git remote: $remoteUrl" -ForegroundColor Green

# Check if gh is available for nicer auth experience
if (Get-Command "gh" -ErrorAction SilentlyContinue) {
    $ghStatus = gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Warning: GitHub CLI (gh) authentication failed." -ForegroundColor Yellow
        Write-Host "Ensure you're authenticated via 'gh auth login' or git credential manager." -ForegroundColor Yellow
    } else {
        Write-Host "GitHub CLI: Authenticated" -ForegroundColor Green
    }
} else {
    Write-Host "Note: GitHub CLI (gh) not installed. Using git credential manager for authentication." -ForegroundColor Yellow
    Write-Host "Install gh at: https://cli.github.com" -ForegroundColor Yellow
}

Write-Host "Owner: $Owner" -ForegroundColor Yellow
Write-Host "Repo: $RepoName" -ForegroundColor Yellow
Write-Host ""

# Package the Helm chart to root directory
Write-Host "=== Packaging Helm chart to root ===" -ForegroundColor Cyan
helm package $ChartDir --destination $ProjectRoot

if (-not (Test-Path $ChartTgzFile)) {
    Write-Host "Error: Failed to package Helm chart." -ForegroundColor Red
    Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}

$ChartFilename = "node-shutdown-1.0.0.tgz"
Write-Host "Chart: $ChartFilename" -ForegroundColor Green

# Generate index.yaml in root directory
Write-Host "Generating index.yaml in root..." -ForegroundColor Cyan
helm repo index --url $HelmRepoUrl $ProjectRoot

Write-Host ""
Write-Host "=== index.yaml content ===" -ForegroundColor Cyan
Get-Content $IndexYamlFile
Write-Host ""

# Checkout generated files to pages branch
Write-Host "=== Checking out generated files to '$Branch' ===" -ForegroundColor Cyan
$PagesDir = Join-Path $TempDir "pages"

# Clone pages branch directly
if (git clone --branch $Branch --single-branch $GithubRepo $PagesDir 2>$null) {
    Write-Host "Cloned $Branch branch." -ForegroundColor Green
} else {
    Write-Host "Error: Cannot clone $Branch branch." -ForegroundColor Red
    Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}

Set-Location $PagesDir

# Copy generated files
Copy-Item $ChartTgzFile (Join-Path $PagesDir $ChartFilename)
Copy-Item $IndexYamlFile (Join-Path $PagesDir "index.yaml")

git add $ChartFilename index.yaml
git config user.email "${Owner}@users.noreply.github.com"
git config user.name $Owner
git commit -m "Add helm chart $ChartFilename" --allow-empty 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "No changes to commit." -ForegroundColor Yellow
}

git push origin $Branch

Set-Location $ProjectRoot

# Delete generated files from root
Write-Host ""
Write-Host "=== Cleaning up generated files ===" -ForegroundColor Yellow
Remove-Item $ChartTgzFile -Force -ErrorAction SilentlyContinue
Remove-Item $IndexYamlFile -Force -ErrorAction SilentlyContinue
Write-Host "Removed generated files from root." -ForegroundColor Green

Write-Host ""
Write-Host "=== Chart published successfully ===" -ForegroundColor Green
Write-Host ""
Write-Host "Chart location: https://${Owner}.github.io/${RepoName}/charts/${ChartFilename}" -ForegroundColor Green
Write-Host "Index file: https://${Owner}.github.io/${RepoName}/index.yaml" -ForegroundColor Green
Write-Host ""
Write-Host "To install:" -ForegroundColor Cyan
Write-Host "  helm repo add hvt-shutdown ${HelmRepoUrl}" -ForegroundColor White
Write-Host "  helm repo update" -ForegroundColor White
Write-Host "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system" -ForegroundColor White
Write-Host ""
Write-Host "NOTE: If you get 404 errors, enable GitHub Pages:" -ForegroundColor Yellow
Write-Host "  1. Go to https://github.com/${Owner}/${RepoName}/settings/pages" -ForegroundColor White
Write-Host "  2. Source: Deploy from a branch" -ForegroundColor White
Write-Host "  3. Branch: pages (root /)" -ForegroundColor White
Write-Host "  4. Save" -ForegroundColor White

# Cleanup temp directory
Set-Location $ProjectRoot
Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue