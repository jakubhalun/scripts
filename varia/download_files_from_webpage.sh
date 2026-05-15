#!/bin/bash

OUTPUT_DIR="."
RETRIES=10
DELAY=30
PAGE_URL=""

usage() {
    echo "Usage: $0 -u <PageUrl> [-o <OutputDir>] [-r <Retries>] [-d <DelaySeconds>]"
    echo "Example: $0 -u 'https://example.com/files' -o './downloads' -r 5 -d 15"
    exit 1
}

while getopts "u:o:r:d:h" opt; do
    case "$opt" in
        u) PAGE_URL="$OPTARG" ;;
        o) OUTPUT_DIR="$OPTARG" ;;
        r) RETRIES="$OPTARG" ;;
        d) DELAY="$OPTARG" ;;
        h|*) usage ;;
    esac
done

if [ -z "$PAGE_URL" ]; then
    echo "Error: Parameter -u (PageUrl) is required." >&2
    usage
fi

if [ ! -d "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR" >/dev/null 2>&1 || { echo "Error: Cannot create directory $OUTPUT_DIR" >&2; exit 1; }
fi

echo "Fetching page content: $PAGE_URL"

PAGE_CONTENT=$(wget -qO- "$PAGE_URL")
if [ $? -ne 0 ]; then
    echo "Error: Failed to download page: $PAGE_URL" >&2
    exit 1
fi

RAW_LINKS=$(echo "$PAGE_CONTENT" | grep -ioP 'href="\K[^"]+' | sort -u)

urldecode() {
    local url_encoded="${1//+/ }"
    printf '%b' "${url_encoded//%/\\x}"
}

DOMAIN=$(echo "$PAGE_URL" | grep -oP '^https?://[^/]+')
if [[ "$PAGE_URL" == */ ]]; then
    BASE_PATH="$PAGE_URL"
else
    BASE_PATH=$(echo "$PAGE_URL" | sed 's|/[^/]*$|/|')
fi

for HREF in $RAW_LINKS; do
    if [[ "$HREF" == "#"* ]] || [[ -z "$HREF" ]]; then
        continue
    fi

    if [[ "$HREF" == "http://"* ]] || [[ "$HREF" == "https://"* ]]; then
        FILE_URL="$HREF"
    elif [[ "$HREF" == /* ]]; then
        FILE_URL="${DOMAIN}${HREF}"
    else
        FILE_URL="${BASE_PATH}${HREF}"
    fi

    URL_PATH=$(echo "$FILE_URL" | cut -d? -f1)
    
    if [[ "$URL_PATH" =~ \.(html?|php)$ ]]; then
        continue
    fi

    if [[ "$URL_PATH" == */ ]]; then
        continue
    fi

    FILENAME=$(basename "$URL_PATH")
    if [ -z "$FILENAME" ]; then
        continue
    fi

    DECODED_FILENAME=$(urldecode "$FILENAME")
    SAFE_FILENAME=$(echo "$DECODED_FILENAME" | sed -e 's/[<>:"/\|?*]/_/g')
    DESTINATION="${OUTPUT_DIR}/${SAFE_FILENAME}"

    echo "----------------------------------------"
    echo "Downloading: $FILE_URL"
    echo "Saving as:   $DESTINATION"

    ATTEMPT=1
    SUCCESS=false
    
    while [ $ATTEMPT -le "$RETRIES" ]; do
        wget -q --show-progress -O "$DESTINATION" "$FILE_URL"
        
        if [ $? -eq 0 ]; then
            SUCCESS=true
            break
        else
            echo "Warning: Attempt $ATTEMPT/$RETRIES failed for $FILE_URL" >&2
            if [ $ATTEMPT -lt "$RETRIES" ]; then
                echo "Waiting $DELAY seconds before next attempt..."
                sleep "$DELAY"
            fi
            ((ATTEMPT++))
        fi
    done

    if [ "$SUCCESS" = false ]; then
        echo "Error: Failed to download file after $RETRIES attempts. Skipping: $FILE_URL" >&2
    fi
done

echo "----------------------------------------"
echo "Download finished."