//! macOS-only ownership wrapper for the Swift audio-capture lifecycle surface.
//!
//! Swift owns the native object and runs lifecycle work on the main queue. This
//! wrapper owns exactly one opaque handle and never marks itself `Send` or
//! `Sync`; a future main-queue dispatcher adapter can implement the callback
//! bridge contract after its threading model is proven.

use std::{
    ffi::{c_char, c_void, CStr},
    ptr::NonNull,
};

use thiserror::Error;

use super::{
    authorization::{
        parse_microphone_authorization, parse_screen_authorization, MicrophoneAuthorizationState,
        ScreenCaptureAuthorizationState,
    },
    sources::{parse_displays, parse_microphones, CaptureDisplaySource, CaptureMicrophoneSource},
    status::{AudioCaptureState, AudioCaptureStatus},
    types::AudioCaptureConfiguration,
};

const NATIVE_SUCCESS: i32 = 0;
const NATIVE_STATUS_STOPPED: i32 = 0;
const NATIVE_STATUS_CAPTURING: i32 = 1;

/// Private FFI seam so lifecycle ownership can be tested without Swift.
trait SwiftAudioCaptureFunctions {
    fn create(&self) -> *mut c_void;
    fn destroy(&self, handle: *mut c_void);
    fn start_placeholder(&self, handle: *mut c_void) -> i32;
    fn stop_placeholder(&self, handle: *mut c_void) -> i32;
    fn status_placeholder(&self, handle: *mut c_void) -> i32;
    fn screen_authorization_state(&self, handle: *mut c_void) -> *mut c_char;
    fn request_screen_authorization(&self, handle: *mut c_void) -> *mut c_char;
    fn microphone_authorization_state(&self, handle: *mut c_void) -> *mut c_char;
    fn request_microphone_authorization(&self, handle: *mut c_void) -> *mut c_char;
    fn list_displays(&self, handle: *mut c_void) -> *mut c_char;
    fn list_microphones(&self, handle: *mut c_void) -> *mut c_char;
    fn free_json_buffer(&self, buffer: *mut c_char);
}

struct SystemSwiftAudioCaptureFunctions;

#[cfg(target_os = "macos")]
impl SwiftAudioCaptureFunctions for SystemSwiftAudioCaptureFunctions {
    fn create(&self) -> *mut c_void {
        // The Swift implementation returns a retained opaque handle.
        unsafe { amcp_audio_capture_bridge_create() }
    }

    fn destroy(&self, handle: *mut c_void) {
        // `handle` is owned by this wrapper and is released exactly once.
        unsafe { amcp_audio_capture_bridge_destroy(handle) }
    }

    fn start_placeholder(&self, handle: *mut c_void) -> i32 {
        // The Swift implementation synchronizes lifecycle work onto its main queue.
        unsafe { amcp_audio_capture_bridge_start_placeholder(handle) }
    }

    fn stop_placeholder(&self, handle: *mut c_void) -> i32 {
        // The Swift implementation synchronizes lifecycle work onto its main queue.
        unsafe { amcp_audio_capture_bridge_stop_placeholder(handle) }
    }

    fn status_placeholder(&self, handle: *mut c_void) -> i32 {
        // The handle remains valid for the duration of this wrapper method.
        unsafe { amcp_audio_capture_bridge_status_placeholder(handle) }
    }

    fn screen_authorization_state(&self, handle: *mut c_void) -> *mut c_char {
        unsafe { amcp_audio_capture_bridge_screen_authorization_state(handle) }
    }

    fn request_screen_authorization(&self, handle: *mut c_void) -> *mut c_char {
        unsafe { amcp_audio_capture_bridge_request_screen_authorization(handle) }
    }

    fn microphone_authorization_state(&self, handle: *mut c_void) -> *mut c_char {
        unsafe { amcp_audio_capture_bridge_microphone_authorization_state(handle) }
    }

    fn request_microphone_authorization(&self, handle: *mut c_void) -> *mut c_char {
        unsafe { amcp_audio_capture_bridge_request_microphone_authorization(handle) }
    }

    fn list_displays(&self, handle: *mut c_void) -> *mut c_char {
        unsafe { amcp_audio_capture_bridge_list_displays(handle) }
    }

    fn list_microphones(&self, handle: *mut c_void) -> *mut c_char {
        unsafe { amcp_audio_capture_bridge_list_microphones(handle) }
    }

    fn free_json_buffer(&self, buffer: *mut c_char) {
        unsafe { amcp_audio_capture_bridge_free_json_buffer(buffer) }
    }
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn amcp_audio_capture_bridge_create() -> *mut c_void;
    fn amcp_audio_capture_bridge_destroy(handle: *mut c_void);
    fn amcp_audio_capture_bridge_start_placeholder(handle: *mut c_void) -> i32;
    fn amcp_audio_capture_bridge_stop_placeholder(handle: *mut c_void) -> i32;
    fn amcp_audio_capture_bridge_status_placeholder(handle: *mut c_void) -> i32;
    fn amcp_audio_capture_bridge_free_json_buffer(buffer: *mut c_char);
    fn amcp_audio_capture_bridge_screen_authorization_state(handle: *mut c_void) -> *mut c_char;
    fn amcp_audio_capture_bridge_request_screen_authorization(handle: *mut c_void) -> *mut c_char;
    fn amcp_audio_capture_bridge_microphone_authorization_state(handle: *mut c_void)
        -> *mut c_char;
    fn amcp_audio_capture_bridge_request_microphone_authorization(
        handle: *mut c_void,
    ) -> *mut c_char;
    fn amcp_audio_capture_bridge_list_displays(handle: *mut c_void) -> *mut c_char;
    fn amcp_audio_capture_bridge_list_microphones(handle: *mut c_void) -> *mut c_char;
}

/// Safe, privacy-preserving errors from the native bridge boundary.
#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum SwiftAudioCaptureBridgeError {
    #[error("The native audio capture bridge is unavailable on this platform.")]
    UnsupportedPlatform,
    #[error("The native audio capture bridge could not be created.")]
    CreationFailed,
    #[error("The native audio capture bridge operation failed.")]
    NativeOperationFailed,
    #[error("The native audio capture bridge returned an invalid status.")]
    InvalidStatus,
    #[error("The native audio capture bridge did not return data.")]
    NativeDataUnavailable,
    #[error("The native audio capture bridge returned invalid data.")]
    MalformedNativePayload,
}

/// Owns a single Swift object handle without exposing it outside this module.
struct SwiftAudioCaptureBridge<F: SwiftAudioCaptureFunctions = SystemSwiftAudioCaptureFunctions> {
    functions: F,
    handle: Option<NonNull<c_void>>,
}

impl SwiftAudioCaptureBridge<SystemSwiftAudioCaptureFunctions> {
    /// Creates the macOS bridge. Other platforms have no Swift runtime bridge.
    #[cfg(target_os = "macos")]
    fn new() -> Result<Self, SwiftAudioCaptureBridgeError> {
        Self::with_functions(SystemSwiftAudioCaptureFunctions)
    }

    /// Provides a clear unsupported path for non-macOS development builds.
    #[cfg(not(target_os = "macos"))]
    fn new() -> Result<Self, SwiftAudioCaptureBridgeError> {
        Err(SwiftAudioCaptureBridgeError::UnsupportedPlatform)
    }
}

impl<F: SwiftAudioCaptureFunctions> SwiftAudioCaptureBridge<F> {
    fn with_functions(functions: F) -> Result<Self, SwiftAudioCaptureBridgeError> {
        let handle =
            NonNull::new(functions.create()).ok_or(SwiftAudioCaptureBridgeError::CreationFailed)?;
        Ok(Self {
            functions,
            handle: Some(handle),
        })
    }

    /// Placeholder start operation; configuration is intentionally unused until capture exists.
    fn start_placeholder(
        &mut self,
        _configuration: &AudioCaptureConfiguration,
    ) -> Result<AudioCaptureStatus, SwiftAudioCaptureBridgeError> {
        if self.functions.start_placeholder(self.handle()?.as_ptr()) != NATIVE_SUCCESS {
            return Err(SwiftAudioCaptureBridgeError::NativeOperationFailed);
        }
        self.status_placeholder()
    }

    /// The Swift operation is idempotent, so repeated calls are safe.
    fn stop_placeholder(&mut self) -> Result<AudioCaptureStatus, SwiftAudioCaptureBridgeError> {
        if self.functions.stop_placeholder(self.handle()?.as_ptr()) != NATIVE_SUCCESS {
            return Err(SwiftAudioCaptureBridgeError::NativeOperationFailed);
        }
        self.status_placeholder()
    }

    fn status_placeholder(&self) -> Result<AudioCaptureStatus, SwiftAudioCaptureBridgeError> {
        let state = match self.functions.status_placeholder(self.handle()?.as_ptr()) {
            NATIVE_STATUS_STOPPED => AudioCaptureState::Stopped,
            NATIVE_STATUS_CAPTURING => AudioCaptureState::Capturing,
            _ => return Err(SwiftAudioCaptureBridgeError::InvalidStatus),
        };
        AudioCaptureStatus::new(state, None)
            .map_err(|_| SwiftAudioCaptureBridgeError::InvalidStatus)
    }

    /// Queries permission state without starting capture or opening settings.
    async fn screen_authorization_state(
        &self,
    ) -> Result<ScreenCaptureAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.screen_authorization_state(handle))
            .and_then(|payload| parse_screen_authorization(&payload))
    }

    /// Explicitly requests screen-capture authorization at most once in Swift.
    async fn request_screen_authorization(
        &self,
    ) -> Result<ScreenCaptureAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.request_screen_authorization(handle))
            .and_then(|payload| parse_screen_authorization(&payload))
    }

    /// Queries microphone authorization without starting an audio engine.
    async fn microphone_authorization_state(
        &self,
    ) -> Result<MicrophoneAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.microphone_authorization_state(handle))
            .and_then(|payload| parse_microphone_authorization(&payload))
    }

    /// Explicitly requests microphone authorization at most once by the system API.
    async fn request_microphone_authorization(
        &self,
    ) -> Result<MicrophoneAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.request_microphone_authorization(handle))
            .and_then(|payload| parse_microphone_authorization(&payload))
    }

    /// Fetches a fresh, privacy-filtered list of ScreenCaptureKit displays.
    async fn list_displays(
        &self,
    ) -> Result<Vec<CaptureDisplaySource>, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.list_displays(handle))
            .and_then(|payload| parse_displays(&payload))
    }

    /// Fetches a fresh list of microphone device identifiers without opening an engine.
    async fn list_microphones(
        &self,
    ) -> Result<Vec<CaptureMicrophoneSource>, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.list_microphones(handle))
            .and_then(|payload| parse_microphones(&payload))
    }

    fn read_json(
        &self,
        get_buffer: impl FnOnce(&F, *mut c_void) -> *mut c_char,
    ) -> Result<Vec<u8>, SwiftAudioCaptureBridgeError> {
        let buffer = NonNull::new(get_buffer(&self.functions, self.handle()?.as_ptr()))
            .ok_or(SwiftAudioCaptureBridgeError::NativeDataUnavailable)?;
        // The pointer is valid and NUL-terminated by the Swift `strdup` boundary.
        let payload = unsafe { CStr::from_ptr(buffer.as_ptr()).to_bytes().to_vec() };
        // Copy before release, then free the owned Swift buffer exactly once.
        self.functions.free_json_buffer(buffer.as_ptr());
        Ok(payload)
    }

    fn handle(&self) -> Result<NonNull<c_void>, SwiftAudioCaptureBridgeError> {
        self.handle
            .ok_or(SwiftAudioCaptureBridgeError::NativeOperationFailed)
    }
}

impl<F> Drop for SwiftAudioCaptureBridge<F>
where
    F: SwiftAudioCaptureFunctions,
{
    fn drop(&mut self) {
        if let Some(handle) = self.handle.take() {
            self.functions.destroy(handle.as_ptr());
        }
    }
}

impl<F: SwiftAudioCaptureFunctions> std::fmt::Debug for SwiftAudioCaptureBridge<F> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SwiftAudioCaptureBridge")
            .field("native_handle", &"[REDACTED]")
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use std::{
        ffi::{c_char, c_void, CString},
        ptr::NonNull,
        sync::{Arc, Mutex},
    };

    use super::{
        AudioCaptureConfiguration, AudioCaptureState, SwiftAudioCaptureBridge,
        SwiftAudioCaptureBridgeError, SwiftAudioCaptureFunctions,
    };

    #[derive(Clone)]
    struct FakeFunctions {
        state: Arc<Mutex<FakeState>>,
    }

    struct FakeState {
        creates: usize,
        destroys: usize,
        freed_json_buffers: usize,
        stops: usize,
        status: i32,
        screen_payload: Option<String>,
    }

    impl FakeFunctions {
        fn new(status: i32) -> Self {
            Self {
                state: Arc::new(Mutex::new(FakeState {
                    creates: 0,
                    destroys: 0,
                    freed_json_buffers: 0,
                    stops: 0,
                    status,
                    screen_payload: Some(r#"{"state":"authorized"}"#.to_owned()),
                })),
            }
        }

        fn with_screen_payload(self, payload: Option<&str>) -> Self {
            self.state
                .lock()
                .expect("state is available")
                .screen_payload = payload.map(str::to_owned);
            self
        }
    }

    impl SwiftAudioCaptureFunctions for FakeFunctions {
        fn create(&self) -> *mut c_void {
            self.state.lock().expect("state is available").creates += 1;
            NonNull::<c_void>::dangling().as_ptr()
        }

        fn destroy(&self, _handle: *mut c_void) {
            self.state.lock().expect("state is available").destroys += 1;
        }

        fn start_placeholder(&self, _handle: *mut c_void) -> i32 {
            self.state.lock().expect("state is available").status = 1;
            0
        }

        fn stop_placeholder(&self, _handle: *mut c_void) -> i32 {
            let mut state = self.state.lock().expect("state is available");
            state.stops += 1;
            state.status = 0;
            0
        }

        fn status_placeholder(&self, _handle: *mut c_void) -> i32 {
            self.state.lock().expect("state is available").status
        }

        fn screen_authorization_state(&self, _handle: *mut c_void) -> *mut c_char {
            self.state
                .lock()
                .expect("state is available")
                .screen_payload
                .as_deref()
                .map(|payload| CString::new(payload).expect("valid JSON").into_raw())
                .unwrap_or(std::ptr::null_mut())
        }

        fn request_screen_authorization(&self, handle: *mut c_void) -> *mut c_char {
            self.screen_authorization_state(handle)
        }

        fn microphone_authorization_state(&self, _handle: *mut c_void) -> *mut c_char {
            CString::new(r#"{"state":"denied"}"#)
                .expect("valid JSON")
                .into_raw()
        }

        fn request_microphone_authorization(&self, handle: *mut c_void) -> *mut c_char {
            self.microphone_authorization_state(handle)
        }

        fn list_displays(&self, _handle: *mut c_void) -> *mut c_char {
            CString::new(r#"{"displays":[]}"#)
                .expect("valid JSON")
                .into_raw()
        }

        fn list_microphones(&self, _handle: *mut c_void) -> *mut c_char {
            CString::new(r#"{"microphones":[]}"#)
                .expect("valid JSON")
                .into_raw()
        }

        fn free_json_buffer(&self, buffer: *mut c_char) {
            self.state
                .lock()
                .expect("state is available")
                .freed_json_buffers += 1;
            // The fake allocates each return value through `CString::into_raw`.
            unsafe { drop(CString::from_raw(buffer)) };
        }
    }

    #[test]
    fn destroys_the_native_handle_exactly_once() {
        let functions = FakeFunctions::new(0);
        let state = functions.state.clone();
        {
            let _bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");
        }

        let state = state.lock().expect("state is available");
        assert_eq!(state.creates, 1);
        assert_eq!(state.destroys, 1);
    }

    #[test]
    fn repeated_stop_is_safe() {
        let functions = FakeFunctions::new(1);
        let state = functions.state.clone();
        let mut bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");

        assert_eq!(
            bridge.stop_placeholder().expect("stops").state(),
            AudioCaptureState::Stopped
        );
        assert_eq!(
            bridge.stop_placeholder().expect("stops again").state(),
            AudioCaptureState::Stopped
        );
        assert_eq!(state.lock().expect("state is available").stops, 2);
    }

    #[test]
    fn maps_known_native_statuses() {
        let functions = FakeFunctions::new(1);
        let bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");

        assert_eq!(
            bridge.status_placeholder().expect("maps status").state(),
            AudioCaptureState::Capturing
        );
    }

    #[test]
    fn rejects_unknown_native_statuses_without_sensitive_details() {
        let functions = FakeFunctions::new(99);
        let bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");
        let error = bridge.status_placeholder().expect_err("invalid status");

        assert_eq!(error, SwiftAudioCaptureBridgeError::InvalidStatus);
        assert!(!error.to_string().contains("99"));
        assert!(!format!("{bridge:?}").contains("0x"));
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn native_bridge_is_explicitly_unsupported_off_macos() {
        assert_eq!(
            SwiftAudioCaptureBridge::new(),
            Err(SwiftAudioCaptureBridgeError::UnsupportedPlatform)
        );
    }

    #[test]
    fn placeholder_start_uses_the_native_lifecycle_seam() {
        let functions = FakeFunctions::new(0);
        let mut bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");

        assert_eq!(
            bridge
                .start_placeholder(&AudioCaptureConfiguration::default())
                .expect("starts")
                .state(),
            AudioCaptureState::Capturing
        );
    }

    #[tokio::test]
    async fn parses_authorization_and_frees_the_native_buffer_once() {
        let functions = FakeFunctions::new(0);
        let state = functions.state.clone();
        let bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");

        assert_eq!(
            bridge
                .screen_authorization_state()
                .await
                .expect("parses authorization"),
            crate::audio_capture::authorization::ScreenCaptureAuthorizationState::Authorized
        );
        assert_eq!(
            state.lock().expect("state is available").freed_json_buffers,
            1
        );
    }

    #[tokio::test]
    async fn parses_empty_source_lists_without_exposing_native_metadata() {
        let bridge =
            SwiftAudioCaptureBridge::with_functions(FakeFunctions::new(0)).expect("creates");

        assert!(bridge
            .list_displays()
            .await
            .expect("parses displays")
            .is_empty());
        assert!(bridge
            .list_microphones()
            .await
            .expect("parses microphones")
            .is_empty());
    }

    #[tokio::test]
    async fn malformed_native_data_is_released_before_returning_a_safe_error() {
        let functions = FakeFunctions::new(0).with_screen_payload(Some("not JSON"));
        let state = functions.state.clone();
        let bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");

        assert_eq!(
            bridge.screen_authorization_state().await,
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
        assert_eq!(
            state.lock().expect("state is available").freed_json_buffers,
            1
        );
    }
}
