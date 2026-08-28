#!/bin/bash
#
# Build a ready-to-flash OpenFlight SD card image.
#
# Starts from the current stable Raspberry Pi OS Desktop (64-bit) release,
# grows it, mounts it, runs customize.sh inside it under qemu, then shrinks
# and compresses the result. The output is a .img.xz that Raspberry Pi Imager
# writes to a card; the owner sets their Wi-Fi and account in Imager, puts the
# card in, and switches on.
#
# Why customize a released image rather than build one with pi-gen: pi-gen
# rebuilds the whole OS from packages, which takes hours and drifts from what
# Raspberry Pi actually ship and test. Customizing the released image means
# the base is exactly the one everyone else runs, and a build takes minutes.
#
# Usage:
#     sudo ./scripts/image/build-image.sh
#     sudo ./scripts/image/build-image.sh --out /tmp/openflight.img
#     sudo ./scripts/image/build-image.sh --base ~/downloads/raspios.img.xz
#     sudo ./scripts/image/build-image.sh --no-compress    # faster iteration
#
# Requirements: Linux, root, ~16 GB free disk, and:
#     sudo apt install qemu-user-static binfmt-support xz-utils parted \
#                      e2fsprogs kpartx curl
#
# Runs on any Linux host; the GitHub Actions workflow in
# .github/workflows/sd-image.yml is the same script on a hosted runner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# The official "latest stable" redirect for Raspberry Pi OS Desktop, 64-bit.
# Using the redirect rather than a pinned URL is deliberate: an image built
# today should carry today's security updates, and pinning silently rots.
# --base overrides it when a reproducible or offline build is needed.
BASE_URL="https://downloads.raspberrypi.com/raspios_arm64_latest"

WORK_DIR="${OPENFLIGHT_IMAGE_WORKDIR:-/var/tmp/openflight-image}"
OUTPUT=""
BASE_IMAGE=""
COMPRESS=true
# Room for the venv, ui/dist, and the owner's first sessions. The rootfs is
# shrunk back to its real size at the end, so this costs build-time disk only.
GROW_MB=3072

log()  { echo -e "\033[0;32m[image]\033[0m $*"; }
warn() { echo -e "\033[1;33m[image]\033[0m $*" >&2; }
die()  { echo -e "\033[0;31m[image]\033[0m $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)         OUTPUT="$2"; shift 2 ;;
        --base)        BASE_IMAGE="$2"; shift 2 ;;
        --work-dir)    WORK_DIR="$2"; shift 2 ;;
        --grow-mb)     GROW_MB="$2"; shift 2 ;;
        --no-compress) COMPRESS=false; shift ;;
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "Must run as root (loop devices and chroot)."
[ "$(uname -s)" = "Linux" ] || die "Only supported on Linux."

for tool in curl xz parted losetup kpartx resize2fs e2fsck mkfs.ext4; do
    command -v "$tool" >/dev/null 2>&1 || die "Missing required tool: $tool"
done
[ -x /usr/bin/qemu-aarch64-static ] \
    || die "Missing /usr/bin/qemu-aarch64-static (apt install qemu-user-static)"

mkdir -p "$WORK_DIR"
MOUNT_ROOT="$WORK_DIR/mnt"
BRANDING_DIR="$WORK_DIR/branding"
LOOP_DEV=""
BUILD_IMAGE="$WORK_DIR/openflight.img"

# ──────────────────────────────────────────────────────────────────────
# Cleanup — every exit path, including a failed chroot
# ──────────────────────────────────────────────────────────────────────
cleanup() {
    local status=$?
    set +e
    if mountpoint -q "$MOUNT_ROOT" 2>/dev/null; then
        # Reverse order: the bind mounts sit on top of the two partitions.
        for path in dev/pts dev proc sys run boot/firmware; do
            umount -lf "$MOUNT_ROOT/$path" 2>/dev/null
        done
        umount -lf "$MOUNT_ROOT" 2>/dev/null
    fi
    if [ -n "$LOOP_DEV" ]; then
        kpartx -d "$LOOP_DEV" 2>/dev/null
        losetup -d "$LOOP_DEV" 2>/dev/null
    fi
    return $status
}
trap cleanup EXIT

# ──────────────────────────────────────────────────────────────────────
# 1. Base image
# ──────────────────────────────────────────────────────────────────────
if [ -z "$BASE_IMAGE" ]; then
    log "Resolving the current stable Raspberry Pi OS Desktop (64-bit)"
    RESOLVED_URL="$(curl -sSLI -o /dev/null -w '%{url_effective}' "$BASE_URL")"
    [ -n "$RESOLVED_URL" ] || die "Could not resolve $BASE_URL"
    BASE_NAME="$(basename "$RESOLVED_URL")"
    BASE_IMAGE="$WORK_DIR/$BASE_NAME"
    log "Base release: $BASE_NAME"

    if [ ! -f "$BASE_IMAGE" ]; then
        log "Downloading (this is ~1.3 GB)"
        curl -fSL --retry 4 --retry-delay 5 -o "$BASE_IMAGE.part" "$RESOLVED_URL"
        mv "$BASE_IMAGE.part" "$BASE_IMAGE"
    else
        log "Reusing the copy already in $WORK_DIR"
    fi

    # Raspberry Pi publish a .sha256 next to every image. Verifying it is the
    # difference between "we customized the OS" and "we customized whatever
    # the network handed us".
    if curl -fsSL -o "$BASE_IMAGE.sha256" "$RESOLVED_URL.sha256"; then
        log "Verifying the download's checksum"
        (cd "$WORK_DIR" && sha256sum -c "$(basename "$BASE_IMAGE.sha256")") \
            || die "Checksum mismatch on $BASE_NAME — refusing to build."
    else
        die "No published checksum for $BASE_NAME — refusing to build an unverified OS."
    fi
fi

log "Preparing a working copy"
rm -f "$BUILD_IMAGE"
case "$BASE_IMAGE" in
    *.xz)  xz -dc "$BASE_IMAGE" > "$BUILD_IMAGE" ;;
    *.img) cp --reflink=auto "$BASE_IMAGE" "$BUILD_IMAGE" ;;
    *)     die "Unrecognised base image type: $BASE_IMAGE" ;;
esac

# ──────────────────────────────────────────────────────────────────────
# 2. Grow the root filesystem so there is room to install into
# ──────────────────────────────────────────────────────────────────────
log "Growing the image by ${GROW_MB} MB"
truncate -s "+${GROW_MB}M" "$BUILD_IMAGE"
# The root partition is the last one, so it can simply be extended to 100%.
parted -s "$BUILD_IMAGE" resizepart 2 100%

LOOP_DEV="$(losetup --find --show "$BUILD_IMAGE")"
kpartx -as "$LOOP_DEV"
LOOP_NAME="$(basename "$LOOP_DEV")"
BOOT_PART="/dev/mapper/${LOOP_NAME}p1"
ROOT_PART="/dev/mapper/${LOOP_NAME}p2"
# kpartx returns before udev has created the nodes.
for _ in $(seq 1 20); do
    [ -b "$ROOT_PART" ] && break
    sleep 0.5
done
[ -b "$ROOT_PART" ] || die "Root partition device never appeared: $ROOT_PART"

e2fsck -fy "$ROOT_PART" >/dev/null 2>&1 || true
resize2fs "$ROOT_PART"

# ──────────────────────────────────────────────────────────────────────
# 3. Mount and stage
# ──────────────────────────────────────────────────────────────────────
log "Mounting the image"
mkdir -p "$MOUNT_ROOT"
mount "$ROOT_PART" "$MOUNT_ROOT"
mkdir -p "$MOUNT_ROOT/boot/firmware"
mount "$BOOT_PART" "$MOUNT_ROOT/boot/firmware"
mount --bind /dev "$MOUNT_ROOT/dev"
mount --bind /dev/pts "$MOUNT_ROOT/dev/pts"
mount -t proc proc "$MOUNT_ROOT/proc"
mount -t sysfs sys "$MOUNT_ROOT/sys"
mount -t tmpfs tmpfs "$MOUNT_ROOT/run"

log "Generating branding assets"
rm -rf "$BRANDING_DIR"
mkdir -p "$BRANDING_DIR"
# Generated on the host so the target image never needs Pillow. uv is used
# here because the repo pins the extra that supplies it.
(cd "$PROJECT_DIR" && uv run --extra image --quiet python \
    scripts/image/make_branding.py --out "$BRANDING_DIR" >/dev/null)
mkdir -p "$MOUNT_ROOT/tmp/openflight-branding"
cp -r "$BRANDING_DIR/." "$MOUNT_ROOT/tmp/openflight-branding/"

log "Staging the OpenFlight source at /opt/openflight"
rm -rf "${MOUNT_ROOT:?}/opt/openflight"
mkdir -p "$MOUNT_ROOT/opt/openflight"
# git archive of HEAD, so the image contains exactly one committed revision
# and none of the build host's local mess (.venv, node_modules, session logs).
(cd "$PROJECT_DIR" && git archive --format=tar HEAD) \
    | tar -x -C "$MOUNT_ROOT/opt/openflight"
# Record what was built, so a support request can start from a commit.
(cd "$PROJECT_DIR" && git rev-parse HEAD) > "$MOUNT_ROOT/opt/openflight/.image-revision"

# Build the UI on the host. The chroot's apt nodejs is whatever Debian ships
# (often too old for Vite 8); the UI is architecture-independent JS.
log "Building the UI on the host"
if ! command -v npm >/dev/null 2>&1; then
    die "npm is required on the build host (Node 20+). Install it or pass a tree that already has ui/dist."
fi
(
    cd "$PROJECT_DIR/ui"
    npm ci --no-audit --no-fund
    npm run build
)
rm -rf "$MOUNT_ROOT/opt/openflight/ui/dist"
cp -a "$PROJECT_DIR/ui/dist" "$MOUNT_ROOT/opt/openflight/ui/dist"

cp /usr/bin/qemu-aarch64-static "$MOUNT_ROOT/usr/bin/"

# ──────────────────────────────────────────────────────────────────────
# 4. Customize inside the image
# ──────────────────────────────────────────────────────────────────────
log "Running customize.sh inside the image (this takes a few minutes)"
chroot "$MOUNT_ROOT" /bin/bash /opt/openflight/scripts/image/customize.sh

log "Cleaning up build leftovers"
rm -f "$MOUNT_ROOT/usr/bin/qemu-aarch64-static"
rm -rf "$MOUNT_ROOT/tmp/openflight-branding"
rm -rf "$MOUNT_ROOT/root/.cache" "$MOUNT_ROOT/root/.npm"
# Machine-specific identity must not ship in an image every owner writes:
# a shared machine-id gives every unit the same DHCP lease and journal ID.
: > "$MOUNT_ROOT/etc/machine-id"
rm -f "$MOUNT_ROOT/var/lib/dbus/machine-id"
rm -f "$MOUNT_ROOT"/etc/ssh/ssh_host_*
find "$MOUNT_ROOT/var/log" -type f -exec truncate -s 0 {} + 2>/dev/null || true

# ──────────────────────────────────────────────────────────────────────
# 5. Unmount, shrink, compress
# ──────────────────────────────────────────────────────────────────────
log "Unmounting"
for path in dev/pts dev proc sys run boot/firmware; do
    umount -lf "$MOUNT_ROOT/$path"
done
umount "$MOUNT_ROOT"

log "Shrinking the root filesystem to its used size"
e2fsck -fy "$ROOT_PART" >/dev/null 2>&1 || true
# resize2fs -M leaves no slack; the Pi expands the partition on first boot
# anyway, so the shipped image only needs to hold what is actually in it.
resize2fs -M "$ROOT_PART" >/dev/null
BLOCK_COUNT="$(dumpe2fs -h "$ROOT_PART" 2>/dev/null | awk -F: '/Block count/ {print $2}' | tr -d ' ')"
BLOCK_SIZE="$(dumpe2fs -h "$ROOT_PART" 2>/dev/null | awk -F: '/Block size/ {print $2}' | tr -d ' ')"
e2fsck -fy "$ROOT_PART" >/dev/null 2>&1 || true

kpartx -d "$LOOP_DEV"
losetup -d "$LOOP_DEV"
LOOP_DEV=""

if [ -n "$BLOCK_COUNT" ] && [ -n "$BLOCK_SIZE" ]; then
    PART_START="$(parted -sm "$BUILD_IMAGE" unit B print | awk -F: '/^2:/ {print $2}' | tr -d 'B')"
    NEW_END=$(( PART_START + BLOCK_COUNT * BLOCK_SIZE ))
    parted -s "$BUILD_IMAGE" unit B resizepart 2 "$NEW_END"
    truncate -s "$(( NEW_END + 1 ))" "$BUILD_IMAGE"
    log "Shrunk to $(( NEW_END / 1024 / 1024 )) MB"
else
    warn "Could not read the filesystem geometry; shipping the image unshrunk"
fi

STAMP="$(date -u +%Y-%m-%d)"
REVISION="$(cd "$PROJECT_DIR" && git rev-parse --short HEAD)"
DEFAULT_OUTPUT="$WORK_DIR/openflight-${STAMP}-${REVISION}.img"
FINAL="${OUTPUT:-$DEFAULT_OUTPUT}"
mv "$BUILD_IMAGE" "$FINAL"

if [ "$COMPRESS" = true ]; then
    log "Compressing (this is the slowest step)"
    # -T0 uses every core; Imager decompresses .xz natively.
    xz -T0 -f "$FINAL"
    FINAL="$FINAL.xz"
fi

sha256sum "$FINAL" > "$FINAL.sha256"

log "Built: $FINAL"
log "       $(du -h "$FINAL" | cut -f1)"
log ""
log "Write it with Raspberry Pi Imager (Use custom -> this file). Set the"
log "Wi-Fi network and user account in Imager's settings screen before writing."
