#!/usr/bin/env bash
set -u
set -o pipefail

if [ "$EUID" -ne 0 ]; then
  echo "This script must be run as root."
  echo "Run it with:"
  echo "  sudo $0 ${1:-}"
  exit 1
fi

passes="${1:-2}"

if ! [[ "$passes" =~ ^[0-9]+$ ]] || [ "$passes" -lt 1 ] || [ "$passes" -gt 10 ]; then
  echo "Usage: sudo $0 [passes]"
  echo "passes must be an integer from 1 to 10, default: 2"
  exit 1
fi

if ! command -v lsblk >/dev/null 2>&1; then
  echo "Error: lsblk is required."
  exit 1
fi

if ! command -v dd >/dev/null 2>&1; then
  echo "Error: dd is required."
  exit 1
fi

devices=$(lsblk -dn -o NAME,TYPE,TRAN | awk '$2=="disk" && $3=="usb" {print "/dev/" $1}')

if [ -z "$devices" ]; then
  echo "No USB disks found."
  exit 0
fi

echo "USB disks found:"
for dev in $devices; do
  lsblk -dn -o NAME,SIZE,MODEL,SERIAL "$dev"
done

echo
echo "The script will read all listed USB disks $passes time(s)."
echo "No data will be written to the listed USB disks."
echo
read -r -p "Type YES to continue: " confirm

if [ "$confirm" != "YES" ]; then
  echo "Aborted."
  exit 1
fi

overall_status=0

for pass in $(seq 1 "$passes"); do
  echo
  echo "===== Pass $pass of $passes ====="

  for dev in $devices; do
    safe_name=$(basename "$dev")
    timestamp=$(date +"%Y%m%d-%H%M%S")
    log="read-test-${safe_name}-pass-${pass}-${timestamp}.log"

    echo
    echo "Reading $dev, pass $pass of $passes"
    echo "Log: $log"

    dd if="$dev" of=/dev/null bs=16M status=progress iflag=fullblock 2>&1 | tee "$log"
    dd_status=${PIPESTATUS[0]}

    if [ "$dd_status" -eq 0 ]; then
      echo "$dev pass $pass OK" | tee -a "$log"
    else
      echo "$dev pass $pass READ ERROR, dd exit code: $dd_status" | tee -a "$log"
      overall_status=1
    fi
  done
done

echo
if [ "$overall_status" -eq 0 ]; then
  echo "All reads finished successfully."
else
  echo "Some reads finished with errors. Check the log files."
fi

exit "$overall_status"
