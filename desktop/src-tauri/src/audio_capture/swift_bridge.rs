//! macOS-only ownership wrapper for the Swift audio-capture lifecycle surface.
//!
//! Swift owns the native object and runs lifecycle work on the main queue. This
//! wrapper owns exactly one opaque handle and never marks itself `Send` or
//! `Sync`; a future main-queue dispatcher adapter can implement the callback
//! bridge contract after its threading model is proven.

use std::{ffi::c_void, ptr::NonNull};

use thiserror::Error;

use super::{
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
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn amcp_audio_capture_bridge_create() -> *mut c_void;
    fn amcp_audio_capture_bridge_destroy(handle: *mut c_void);
    fn amcp_audio_capture_bridge_start_placeholder(handle: *mut c_void) -> i32;
    fn amcp_audio_capture_bridge_stop_placeholder(handle: *mut c_void) -> i32;
    fn amcp_audio_capture_bridge_status_placeholder(handle: *mut c_void) -> i32;
}

/// Safe, privacy-preserving errors from the native bridge boundary.
#[derive(Debug, Error, PartialEq, Eq)]
enum SwiftAudioCaptureBridgeError {
    #[error("The native audio capture bridge is unavailable on this platform.")]
    UnsupportedPlatform,
    #[error("The native audio capture bridge could not be created.")]
    CreationFailed,
    #[error("The native audio capture bridge operation failed.")]
    NativeOperationFailed,
    #[error("The native audio capture bridge returned an invalid status.")]
    InvalidStatus,
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
        ffi::c_void,
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
        stops: usize,
        status: i32,
    }

    impl FakeFunctions {
        fn new(status: i32) -> Self {
            Self {
                state: Arc::new(Mutex::new(FakeState {
                    creates: 0,
                    destroys: 0,
                    stops: 0,
                    status,
                })),
            }
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
}
