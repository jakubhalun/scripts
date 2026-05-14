param(
    [Parameter(Mandatory = $true)]
    [string]$PageUrl,

    [string]$OutputDir = ".",

    [int]$MaxRetries = 3
)

Add-Type -AssemblyName System.Web

function Invoke-WebRequestWithRetry {
    param(
        [hashtable]$Params,
        [int]$MaxRetries
    )

    $attempt = 0
    while ($true) {
        try {
            return Invoke-WebRequest @Params
        } catch {
            if ($attempt -ge $MaxRetries) {
                throw
            }
            $waitSec = [int](10 * [Math]::Pow(2, $attempt))
            Write-Warning "Request failed (attempt $($attempt + 1)/$($MaxRetries + 1)): $_"
            Write-Warning "Retrying in ${waitSec}s..."
            Start-Sleep -Seconds $waitSec
            $attempt++
        }
    }
}

function Invoke-FileDownloadWithRetry {
    param(
        [string]$Uri,
        [string]$Destination,
        [int]$MaxRetries
    )

    $tempFile = "$Destination.tmp"
    $attempt = 0

    while ($true) {
        if (Test-Path -LiteralPath $tempFile) {
            Remove-Item -LiteralPath $tempFile -Force
        }

        $downloadOk = $false
        try {
            $response = Invoke-WebRequest -Uri $Uri -OutFile $tempFile -PassThru
            $contentLength = $null
            if ($response.Headers.ContainsKey('Content-Length')) {
                $contentLength = [long]$response.Headers['Content-Length']
            }

            if ($null -ne $contentLength) {
                $actualSize = (Get-Item -LiteralPath $tempFile).Length
                if ($actualSize -ne $contentLength) {
                    throw "Size mismatch: expected $contentLength bytes, got $actualSize bytes"
                }
            }

            $downloadOk = $true
        } catch {
            if (Test-Path -LiteralPath $tempFile) {
                Remove-Item -LiteralPath $tempFile -Force
            }

            if ($attempt -ge $MaxRetries) {
                throw
            }

            $waitSec = [int](10 * [Math]::Pow(2, $attempt))
            Write-Warning "Download failed (attempt $($attempt + 1)/$($MaxRetries + 1)): $_"
            Write-Warning "Retrying in ${waitSec}s..."
            Start-Sleep -Seconds $waitSec
            $attempt++
            continue
        }

        if ($downloadOk) {
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Force
            }
            Move-Item -LiteralPath $tempFile -Destination $Destination
            return
        }
    }
}

# Create output directory if needed
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

try {
    $pageResponse = Invoke-WebRequestWithRetry -Params @{ Uri = $PageUrl; UseBasicParsing = $true } -MaxRetries $MaxRetries
} catch {
    Write-Error "Failed to download page: $PageUrl`n$_"
    exit 1
}

$baseUri = [System.Uri]$PageUrl

# Prefer parsed links if available, fall back to regex
$rawLinks = @()

if ($pageResponse.Links) {
    $rawLinks = $pageResponse.Links | ForEach-Object { $_.href } | Where-Object { $_ }
} else {
    $rawLinks = [regex]::Matches($pageResponse.Content, 'href\s*=\s*"([^"]+)"', 'IgnoreCase') |
        ForEach-Object { $_.Groups[1].Value }
}

# Remove duplicates
$rawLinks = $rawLinks | Select-Object -Unique

foreach ($href in $rawLinks) {
    try {
        $fileUri = [System.Uri]::new($baseUri, $href)

        # Skip pages/scripts
        $path = $fileUri.AbsolutePath
        if ($path -match '\.(html?|php)$') {
            continue
        }

        # Skip links ending with /
        if ($path.EndsWith('/')) {
            continue
        }

        # Get decoded filename
        $encodedFileName = [System.IO.Path]::GetFileName($path)
        if ([string]::IsNullOrWhiteSpace($encodedFileName)) {
            continue
        }

        $decodedFileName = [System.Uri]::UnescapeDataString($encodedFileName)

        # Replace characters invalid on Windows
        $invalidChars = [System.IO.Path]::GetInvalidFileNameChars()
        foreach ($char in $invalidChars) {
            $decodedFileName = $decodedFileName.Replace($char, '_')
        }

        $destination = Join-Path $OutputDir $decodedFileName

        Write-Host "Downloading: $fileUri"
        Write-Host "Saving as:  $destination"

        Invoke-FileDownloadWithRetry -Uri $fileUri.AbsoluteUri -Destination $destination -MaxRetries $MaxRetries
    } catch {
        Write-Warning "Failed to download: $href`n$_"
    }
}
