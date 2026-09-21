<#
.SYNOPSIS
Create a source archive of lit-reviewer.
.EXAMPLE
.\scripts\archive-project.ps1 D:\Backups
.EXAMPLE
.\scripts\archive-project.ps1 D:\Backups\review-demo.zip -Force
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string] $Destination,

    [switch] $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destinationPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
if ([System.IO.Directory]::Exists($destinationPath) -or
    [System.IO.Path]::GetExtension($destinationPath) -ine '.zip') {
    $archivePath = Join-Path $destinationPath 'hm-comp.zip'
} else {
    $archivePath = $destinationPath
}
$archivePath = [System.IO.Path]::GetFullPath($archivePath)
if ([System.IO.File]::Exists($archivePath) -and -not $Force) {
    throw "Archive already exists: $archivePath. Use -Force to replace it."
}

# These names are excluded BEFORE reading file contents or descending into folders.
$excludedDirectories = @(
    '.git', 'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache',
    '.ruff_cache', '.svelte-kit', 'build', 'dist', 'coverage', '.test-docs',
    '.ssh', '.aws', '.azure', '.kube', 'secrets', '.secrets'
)
$excludedFiles = @(
    '.env', '.env.*', '*.pem', '*.key', '*.pfx', '*.p12', '*.jks', '*.keystore',
    'credentials', 'credentials.*', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519',
    '*.zip', '*.7z', '*.tar', '*.gz', '*.log', '*.pyc', '.coverage',
    'coverage.xml', '.archive-*.tmp'
)

function Get-ArchiveSourceFiles([string] $DirectoryPath) {
    foreach ($item in Get-ChildItem -LiteralPath $DirectoryPath -Force) {
        # Do not follow links/junctions outside the project or read link targets.
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            continue
        }
        if ($item.PSIsContainer) {
            if ($item.Name -in $excludedDirectories -or $item.Name -like '*.egg-info') {
                continue
            }
            $relativeDirectory = $item.FullName.Substring($projectRoot.Length + 1).Replace('\', '/')
            if ($relativeDirectory -eq 'backend/imports') { continue }
            Get-ArchiveSourceFiles -DirectoryPath $item.FullName
        } else {
            $excluded = $false
            foreach ($pattern in $excludedFiles) {
                if ($item.Name -like $pattern) { $excluded = $true; break }
            }
            if (-not $excluded -and $item.FullName -ine $archivePath) { $item }
        }
    }
}

# Enumerate before creating output so it cannot include itself.
$sourceFiles = @(Get-ArchiveSourceFiles -DirectoryPath $projectRoot | Sort-Object FullName)
$archiveDirectory = [System.IO.Path]::GetDirectoryName($archivePath)
[System.IO.Directory]::CreateDirectory($archiveDirectory) | Out-Null
$temporaryPath = Join-Path $archiveDirectory ('.archive-' + [guid]::NewGuid().ToString('N') + '.tmp')
$stream = $null
$zip = $null
try {
    $stream = [System.IO.File]::Open($temporaryPath, [System.IO.FileMode]::CreateNew)
    $zip = [System.IO.Compression.ZipArchive]::new(
        $stream, [System.IO.Compression.ZipArchiveMode]::Create, $false,
        [System.Text.Encoding]::UTF8
    )
    foreach ($file in $sourceFiles) {
        $relativePath = $file.FullName.Substring($projectRoot.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $file.FullName, $relativePath, [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
    $zip.Dispose()
    $zip = $null
    $stream.Dispose()
    $stream = $null
    if ([System.IO.File]::Exists($archivePath)) {
        if (-not $Force) { throw "Archive appeared while writing: $archivePath. Output was not replaced." }
        [System.IO.File]::Replace($temporaryPath, $archivePath, $null)
    } else {
        [System.IO.File]::Move($temporaryPath, $archivePath)
    }
    [pscustomobject]@{
        Archive = $archivePath
        Files = $sourceFiles.Count
        Bytes = ([System.IO.FileInfo]::new($archivePath)).Length
    }
} finally {
    if ($null -ne $zip) { $zip.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    # Only remove the single temporary file this invocation created.
    if ([System.IO.File]::Exists($temporaryPath)) {
        [System.IO.File]::Delete($temporaryPath)
    }
}

