#!/usr/bin/env bash
# One mapping for a full checkout and an already bundled standalone receiver.
package_valid_file() {
  [[ "$1" =~ ^([a-zA-Z0-9_]+\.py|README\.md|requirements\.txt|receiver_assets\.txt|cluster-hdmi\.service|scripts/[a-zA-Z0-9_-]+\.sh|assets/fonts/[a-zA-Z0-9_-]+\.ttf|assets/icons/[a-zA-Z0-9_-]+\.png)$ ]]
}
package_safe_parents() {
  local directory=$1 name=$2 parent=$2
  [[ ! -L "$directory/$name" ]] || return 1
  while [[ "$parent" == */* ]]; do
    parent=${parent%/*}
    [[ ! -L "$directory/$parent" ]] || return 1
    [[ ! -e "$directory/$parent" || -d "$directory/$parent" ]] || return 1
  done
}
package_add_file() {
  local name=$1 root=$2 source=${3:-$1}
  package_valid_file "$name" || fail "Unexpected package filename: $name"
  package_safe_parents "$root" "$source" || fail "Unsafe package path: $name"
  [[ -f "$root/$source" ]] || fail "Package is missing $name. Use a full checkout or a complete standalone bundle."
  [[ " ${files[*]} " != *" $name "* ]] || fail "Duplicate package path: $name"
  files+=("$name")
  file_sources+=("$root/$source")
}
package_collect_files() {
  local source relative required shared asset_root
  files=()
  file_sources=()
  [[ ! -L "$PACKAGE_DIR/scripts" && ! -L "$PACKAGE_DIR/assets" ]] || fail 'Package directories must not be symlinks.'
  for relative in README.md requirements.txt receiver_assets.txt cluster-hdmi.service; do
    package_add_file "$relative" "$PACKAGE_DIR"
  done
  for source in "$PACKAGE_DIR"/*.py "$PACKAGE_DIR"/scripts/*.sh; do
    [[ -e "$source" || -L "$source" ]] || continue
    package_add_file "${source#"$PACKAGE_DIR/"}" "$PACKAGE_DIR"
  done
  for relative in cluster_renderer.py cluster_render_support.py; do
    if [[ " ${files[*]} " != *" $relative "* ]]; then
      shared=$(cd -- "$PACKAGE_DIR/../.." && pwd -P)
      package_add_file "$relative" "$shared"
    fi
  done
  if [[ -d "$PACKAGE_DIR/assets" ]]; then
    asset_root=$PACKAGE_DIR
  else
    shared=$(cd -- "$PACKAGE_DIR/../.." && pwd -P)
    asset_root=$(cd -- "$shared/../.." && pwd -P)
    # Repo assets live under selfdrive/assets; bundle paths start with assets/.
  fi
  while IFS= read -r relative || [[ -n "$relative" ]]; do
    relative=${relative%$'\r'}
    [[ "$relative" =~ ^assets/(fonts/[a-zA-Z0-9_-]+\.ttf|icons/[a-zA-Z0-9_-]+\.png)$ ]] || fail 'Invalid receiver asset path.'
    package_add_file "$relative" "$asset_root"
  done < "$PACKAGE_DIR/receiver_assets.txt"
  for required in cluster_receiver.py cluster_protocol.py hdmi_display.py frame_stream.py display_controls.py texture_presenter.py touch_status.py tcp_diagnostics.py \
    receiver_build.py h264_receiver.py h264_protocol.py h264_scene.py cluster_renderer.py cluster_render_support.py \
    assets/fonts/Inter-Bold.ttf assets/fonts/Inter-Regular.ttf scripts/common.sh scripts/package_sources.sh scripts/prepare_h264.sh \
    scripts/update.sh scripts/ssh_port.sh scripts/ensure_wifi.sh scripts/wifi_boot_setup.sh scripts/wifi_power_save.sh scripts/fan_setup.sh; do
    [[ " ${files[*]} " == *" $required "* ]] || fail "Package is missing $required."
  done
}
