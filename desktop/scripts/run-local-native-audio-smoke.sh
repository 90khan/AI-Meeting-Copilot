#!/bin/zsh

set -eu

if [[ "${AI_MEETING_COPILOT_RUN_LOCAL_NATIVE_AUDIO_TESTS:-}" != "1" ]]; then
  print -u2 "Set AI_MEETING_COPILOT_RUN_LOCAL_NATIVE_AUDIO_TESTS=1 to run the native-audio smoke test."
  exit 1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "The native-audio smoke test requires macOS 15 or later."
  exit 1
fi

major_version="$(sw_vers -productVersion | cut -d. -f1)"
if (( major_version < 15 )); then
  print -u2 "The native-audio smoke test requires macOS 15 or later."
  exit 1
fi

cd "$(dirname "$0")/.."
cargo test --manifest-path src-tauri/Cargo.toml --test native_live_transcription_local -- --ignored
npm run tauri:dev
