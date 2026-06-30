# PowerShell script to create a GitHub release for this project
# Usage: .\scripts\create_release.ps1 [-Version "1.0.0"] [-Tag "v1.0.0"] [-Message "Release description"]
#
# Examples:
#   .\scripts\create_release.ps1
#   .\scripts\create_release.ps1 -Version "1.1.0" -Message "Bug fixes and security updates"
#
# Switches:
#   -Draft        Create as draft release
#   -Prerelease   Create as pre-release

param(
    [string]$Version = "",
    [string]$Tag = "",
    [string]$Message = "",
    [switch]$Draft,
    [switch]$Prerelease
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Get git info
$CommitHash = & git rev-parse --short HEAD 2>$null
$Branch = & git branch --show-current 2>$null
$RepoName = (Split-Path (git remote get-url origin 2>$null) -Leaf) -replace '\.git$', ''
if (-not $RepoName) { $RepoName = "hvt-shutdown-addons" }

# Extract owner for GitHub Pages URL
$Owner = ""
$RemoteUrl = git remote get-url origin 2>$null
if ($RemoteUrl -match 'github\.com[/:](?<owner>[^/]+)') {
    $Owner = $Matches.owner
}
if (-not $Owner) {
    $Owner = "yourusername"
}

# Prompt for version if not provided
if (-not $Version) {
    $Version = Read-Host "Enter release version (e.g., 1.0.0)"
}

# Default tag to v + version
if (-not $Tag) {
    $Tag = "v$Version"
}

# Default message
if (-not $Message) {
    $Message = "Release $Tag ($CommitHash)"
}

Write-Host ""
Write-Host "=== GitHub Release Creator ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Version: $Version" -ForegroundColor Yellow
Write-Host "Tag: $Tag" -ForegroundColor Yellow
Write-Host "Message: $Message" -ForegroundColor Yellow
Write-Host "Branch: $Branch" -ForegroundColor Yellow
Write-Host "Latest Commit: $CommitHash" -ForegroundColor Yellow
Write-Host ""

# Check if helm is installed
try {
    helm version --short 2>&1 | Out-Null
    Write-Host "Helm: Found" -ForegroundColor Green
} catch {
    Write-Host "Error: Helm is not installed. Please install Helm first." -ForegroundColor Red
    Write-Host "Download from: https://helm.sh/docs/intro/install/" -ForegroundColor Yellow
    exit 1
}

# Check if gh CLI is installed
try {
    gh auth status 2>&1 | Out-Null
    Write-Host "GitHub CLI: Authenticated" -ForegroundColor Green
} catch {
    Write-Host "Error: GitHub CLI is not authenticated. Run 'gh auth login' first." -ForegroundColor Red
    exit 1
}

# Confirm before proceeding
$confirm = Read-Host "Proceed with release? (y/n)"
if ($confirm -ne 'y' -and $confirm -ne 'Y') {
    Write-Host "Release cancelled." -ForegroundColor Yellow
    exit 0
}

# Create directories
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ReleasesDir = Join-Path $ProjectRoot "releases"
$OutputDir = Join-Path $ProjectRoot "charts-output"

New-Item -ItemType Directory -Force -Path $ReleasesDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# Package the Helm chart
Write-Host ""
Write-Host "Packaging Helm chart..." -ForegroundColor Cyan
$ChartDir = Join-Path $ProjectRoot "Charts"
helm package $ChartDir --destination $OutputDir

# Generate index with GitHub Pages URL
Write-Host "Generating index.yaml..." -ForegroundColor Cyan
helm repo index --url "https://$Owner.github.io/$RepoName" "$OutputDir"

# Find the packaged chart file
$ChartTgz = Get-ChildItem "$OutputDir\*.tgz" | Select-Object -First 1
if (-not $ChartTgz) {
    Write-Host "Error: Failed to package Helm chart." -ForegroundColor Red
    exit 1
}

$ChartFilename = $ChartTgz.Name
Write-Host "Chart packaged: $ChartFilename" -ForegroundColor Green

# Copy chart to releases directory
Copy-Item $ChartTgz.FullName (Join-Path $ReleasesDir $ChartFilename)
Copy-Item (Join-Path $OutputDir "index.yaml") (Join-Path $ReleasesDir "index.yaml")

# Create compressed archive of the repository
Write-Host ""
Write-Host "Creating release archive..." -ForegroundColor Cyan

$ArchiveName = "$RepoName-$Version.zip"
$ArchivePath = Join-Path $ReleasesDir $ArchiveName

# Create a temporary directory for staging
$TempDir = New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "temp-release-staging") | Out-Null

# Copy files excluding .git, releases/, charts-output/, __pycache__, etc.
$ExcludePatterns = @('.git', 'releases', 'charts-output', '__pycache__', '.pytest_cache', '*.pyc', '*.pyo')

Get-ChildItem -Path $ProjectRoot -Recurse -File |
    Where-Object {
        $relativePath = $_.FullName.Substring($ProjectRoot.Length).TrimStart('\').Replace('\', '/')
        $shouldExclude = $false
        foreach ($pattern in $ExcludePatterns) {
            if ($relativePath -like "*$pattern*" -or $_.DirectoryName -like "*\$pattern*") {
                $shouldExclude = $true
                break
            }
        }
        -not $shouldExclude
    } |
    Copy-Item -Destination { Join-Path $TempDir $_.DirectoryName.Substring($ProjectRoot.Length).TrimStart('\') }

# Create zip archive
Compress-Archive -Path (Join-Path $TempDir "*") -DestinationPath $ArchivePath -Force

# Clean up temp directory
Remove-Item -Path $TempDir -Recurse -Force

Write-Host "Archive created: $ArchiveName" -ForegroundColor Green

# Show release artifacts
Write-Host ""
Write-Host "=== Release Artifacts ===" -ForegroundColor Green
Get-ChildItem $ReleasesDir | Format-Table Name, Length -AutoSize

Write-Host ""
Write-Host "To create the release, run:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  cd $ProjectRoot"
Write-Host "  gh release create $Tag ($ReleasesDir | ForEach-Object { `"$_`" }) --title `$Tag $([if($Draft){'--draft'}] -replace '^\s+','') $([if($Prerelease){'--prerelease'}] -replace '^\s+','')"
Write-Host ""
Write-Host "Or manually:" -ForegroundColor Yellow
Write-Host "  gh release create $Tag $ReleasesDir\* --title `$Tag --generate-notes $([if($Draft){'--draft'}] -replace '^\s+','') $([if($Prerelease){'--prerelease'}] -replace '^\s+','')"
Write-Host ""
Write-Host "Then push tags and create release on GitHub:" -ForegroundColor Yellow
Write-Host "  git tag $Tag"
Write-Host "  git push origin $Tag"
Write-Host ""