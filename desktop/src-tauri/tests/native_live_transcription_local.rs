//! Explicitly opt-in preflight for the manual native live-transcription smoke test.
//!
//! ScreenCaptureKit permissions and selecting a real desktop audio source are
//! interactive macOS operations. The full test is therefore performed through
//! the documented desktop workflow rather than simulated in CI.

#[test]
#[ignore = "manual macOS native-audio smoke test; never run in CI"]
fn native_live_transcription_smoke_requires_explicit_opt_in() {
    assert!(cfg!(target_os = "macos"), "This smoke test requires macOS.");
    assert_eq!(
        std::env::var("AI_MEETING_COPILOT_RUN_LOCAL_NATIVE_AUDIO_TESTS").ok().as_deref(),
        Some("1"),
        "Set AI_MEETING_COPILOT_RUN_LOCAL_NATIVE_AUDIO_TESTS=1 to enable the manual native-audio smoke test."
    );
}
