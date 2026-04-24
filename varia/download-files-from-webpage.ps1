param(
    [Parameter(Mandatory = $true)]
    [string]$PageUrl,

    [string]$OutputDir = "."
)

Add-Type -AssemblyName System.Web

# Create output directory if needed
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

try {
    $pageResponse = Invoke-WebRequest -Uri $PageUrl -UseBasicParsing
} catch {
    Write-Error "Failed to download page: $PageUrl"
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

        Invoke-WebRequest -Uri $fileUri.AbsoluteUri -OutFile $destination
    } catch {
        Write-Warning "Failed to download: $href"
    }
}
