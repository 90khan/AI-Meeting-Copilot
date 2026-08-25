//! macOS-only ownership wrapper for the Swift audio-capture lifecycle surface.
//!
//! Swift owns the native object and runs lifecycle work on the main queue. This
//! wrapper owns exactly one opaque handle and never marks itself `Send` or
//! `Sync`; a future main-queue dispatcher adapter can implement the callback
//! bridge contract after its threading model is proven.

use std::{
    ffi::{c_char, c_void, CStr},
    ptr::NonNull,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};

#[cfg(debug_assertions)]
use std::{sync::atomic::AtomicU64, time::Instant};

use thiserror::Error;

use super::{
    authorization::{
        parse_microphone_authorization, parse_screen_authorization, MicrophoneAuthorizationState,
        ScreenCaptureAuthorizationState,
    },
    bridge::{NativeAudioCaptureBridge, NativeAudioCaptureBridgeError, NativeAudioFrameSender},
    sources::{parse_displays, parse_microphones, CaptureDisplaySource, CaptureMicrophoneSource},
    status::{AudioCaptureState, AudioCaptureStatus},
    types::{
        AudioCaptureConfiguration, NativeAudioFormat, NativeAudioFrame, NativeAudioSamples,
        NativeAudioSource, NativeSampleFormat,
    },
};

const NATIVE_SUCCESS: i32 = 0;
const NATIVE_STATUS_STOPPED: i32 = 0;
const NATIVE_STATUS_CAPTURING: i32 = 1;

/// Native callbacks are normally roughly 10 ms apart. Reporting every 500
/// validated frames gives useful multi-second throughput evidence without
/// writing one diagnostic per callback.
#[cfg(debug_assertions)]
const CALLBACK_THROUGHPUT_REPORT_INTERVAL: u64 = 500;

/// Private FFI seam so lifecycle ownership can be tested without Swift.
pub(crate) trait SwiftAudioCaptureFunctions: Send {
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
    fn start_capture(
        &self,
        handle: *mut c_void,
        configuration: &AudioCaptureConfiguration,
        callback: NativeFrameCallback,
        context: *mut c_void,
    ) -> i32;
    fn stop_capture(&self, handle: *mut c_void) -> i32;
    fn capture_status(&self, handle: *mut c_void) -> i32;
}

type NativeFrameCallback =
    extern "C" fn(*mut c_void, u8, u32, u16, u8, bool, f64, *const c_void, usize);

#[cfg(debug_assertions)]
#[derive(Default)]
struct CallbackSourceMetrics {
    valid_frame_count: AtomicU64,
    input_microseconds: AtomicU64,
    accepted_frame_count: AtomicU64,
    accepted_microseconds: AtomicU64,
    dropped_full_count: AtomicU64,
    dropped_full_microseconds: AtomicU64,
    dropped_closed_count: AtomicU64,
    dropped_closed_microseconds: AtomicU64,
}

#[cfg(debug_assertions)]
impl CallbackSourceMetrics {
    fn record_input(&self, duration_microseconds: u64) {
        self.valid_frame_count.fetch_add(1, Ordering::Relaxed);
        self.input_microseconds
            .fetch_add(duration_microseconds, Ordering::Relaxed);
    }

    fn record_accepted(&self, duration_microseconds: u64) {
        self.accepted_frame_count.fetch_add(1, Ordering::Relaxed);
        self.accepted_microseconds
            .fetch_add(duration_microseconds, Ordering::Relaxed);
    }

    /// Returns true only for the first observed drop of this class.
    fn record_dropped_full(&self, duration_microseconds: u64) -> bool {
        self.dropped_full_microseconds
            .fetch_add(duration_microseconds, Ordering::Relaxed);
        self.dropped_full_count.fetch_add(1, Ordering::Relaxed) == 0
    }

    /// Returns true only for the first observed drop of this class.
    fn record_dropped_closed(&self, duration_microseconds: u64) -> bool {
        self.dropped_closed_microseconds
            .fetch_add(duration_microseconds, Ordering::Relaxed);
        self.dropped_closed_count.fetch_add(1, Ordering::Relaxed) == 0
    }

    fn snapshot(&self) -> CallbackSourceMetricsSnapshot {
        CallbackSourceMetricsSnapshot {
            input_ms: self.input_microseconds.load(Ordering::Relaxed) / 1_000,
            accepted_ms: self.accepted_microseconds.load(Ordering::Relaxed) / 1_000,
            valid_frame_count: self.valid_frame_count.load(Ordering::Relaxed),
            accepted_frame_count: self.accepted_frame_count.load(Ordering::Relaxed),
            dropped_full_count: self.dropped_full_count.load(Ordering::Relaxed),
            dropped_full_ms: self.dropped_full_microseconds.load(Ordering::Relaxed) / 1_000,
            dropped_closed_count: self.dropped_closed_count.load(Ordering::Relaxed),
            dropped_closed_ms: self.dropped_closed_microseconds.load(Ordering::Relaxed) / 1_000,
        }
    }
}

#[cfg(debug_assertions)]
struct CallbackSourceMetricsSnapshot {
    input_ms: u64,
    accepted_ms: u64,
    valid_frame_count: u64,
    accepted_frame_count: u64,
    dropped_full_count: u64,
    dropped_full_ms: u64,
    dropped_closed_count: u64,
    dropped_closed_ms: u64,
}

#[cfg(debug_assertions)]
struct CallbackThroughputMetrics {
    started_at: Instant,
    valid_callback_count: AtomicU64,
    system: CallbackSourceMetrics,
    microphone: CallbackSourceMetrics,
}

#[cfg(debug_assertions)]
impl CallbackThroughputMetrics {
    fn new() -> Self {
        Self {
            started_at: Instant::now(),
            valid_callback_count: AtomicU64::new(0),
            system: CallbackSourceMetrics::default(),
            microphone: CallbackSourceMetrics::default(),
        }
    }

    fn source_metrics(&self, source: NativeAudioSource) -> &CallbackSourceMetrics {
        match source {
            NativeAudioSource::SystemAudio => &self.system,
            NativeAudioSource::Microphone => &self.microphone,
        }
    }

    fn record_input(&self, source: NativeAudioSource, duration_microseconds: u64) -> u64 {
        self.source_metrics(source)
            .record_input(duration_microseconds);
        self.valid_callback_count.fetch_add(1, Ordering::Relaxed) + 1
    }

    fn record_accepted(&self, source: NativeAudioSource, duration_microseconds: u64) {
        self.source_metrics(source)
            .record_accepted(duration_microseconds);
    }

    fn record_dropped_full(&self, source: NativeAudioSource, duration_microseconds: u64) -> bool {
        self.source_metrics(source)
            .record_dropped_full(duration_microseconds)
    }

    fn record_dropped_closed(&self, source: NativeAudioSource, duration_microseconds: u64) -> bool {
        self.source_metrics(source)
            .record_dropped_closed(duration_microseconds)
    }

    fn report_if_due(&self, generation: u64, valid_callback_count: u64) {
        if valid_callback_count % CALLBACK_THROUGHPUT_REPORT_INTERVAL != 0 {
            return;
        }
        self.report(generation, valid_callback_count, "periodic");
    }

    fn report_at_stop(&self, generation: u64) {
        self.report(
            generation,
            self.valid_callback_count.load(Ordering::Relaxed),
            "stopped",
        );
    }

    fn report(&self, generation: u64, valid_callback_count: u64, phase: &'static str) {
        let system = self.system.snapshot();
        let microphone = self.microphone.snapshot();
        eprintln!(
            "audio-capture native callback throughput phase={phase} generation={generation} elapsed_ms={} valid_frame_count={valid_callback_count} system_input_ms={} system_accepted_ms={} system_valid_frame_count={} system_accepted_frame_count={} system_dropped_full_count={} system_dropped_full_ms={} system_dropped_closed_count={} system_dropped_closed_ms={} microphone_input_ms={} microphone_accepted_ms={} microphone_valid_frame_count={} microphone_accepted_frame_count={} microphone_dropped_full_count={} microphone_dropped_full_ms={} microphone_dropped_closed_count={} microphone_dropped_closed_ms={}",
            self.started_at.elapsed().as_millis(),
            system.input_ms,
            system.accepted_ms,
            system.valid_frame_count,
            system.accepted_frame_count,
            system.dropped_full_count,
            system.dropped_full_ms,
            system.dropped_closed_count,
            system.dropped_closed_ms,
            microphone.input_ms,
            microphone.accepted_ms,
            microphone.valid_frame_count,
            microphone.accepted_frame_count,
            microphone.dropped_full_count,
            microphone.dropped_full_ms,
            microphone.dropped_closed_count,
            microphone.dropped_closed_ms,
        );
    }
}

struct CallbackState {
    accepting: AtomicBool,
    #[cfg(debug_assertions)]
    generation: u64,
    #[cfg(debug_assertions)]
    throughput_metrics: CallbackThroughputMetrics,
    sender: Mutex<Option<NativeAudioFrameSender>>,
}

pub(crate) struct SystemSwiftAudioCaptureFunctions;

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

    fn start_capture(
        &self,
        handle: *mut c_void,
        configuration: &AudioCaptureConfiguration,
        callback: NativeFrameCallback,
        context: *mut c_void,
    ) -> i32 {
        let microphone_id = configuration
            .microphone_device_id()
            .map(|id| std::ffi::CString::new(id).expect("validated device ID"));
        unsafe {
            amcp_audio_capture_bridge_start_capture(
                handle,
                configuration.display_id(),
                microphone_id
                    .as_ref()
                    .map_or(std::ptr::null(), |value| value.as_ptr()),
                configuration.include_system_audio(),
                configuration.include_microphone(),
                configuration.exclude_current_process_audio(),
                callback,
                context,
            )
        }
    }
    fn stop_capture(&self, handle: *mut c_void) -> i32 {
        unsafe { amcp_audio_capture_bridge_stop_capture(handle) }
    }
    fn capture_status(&self, handle: *mut c_void) -> i32 {
        unsafe { amcp_audio_capture_bridge_capture_status(handle) }
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
    fn amcp_audio_capture_bridge_start_capture(
        handle: *mut c_void,
        display_id: u32,
        microphone_id: *const c_char,
        include_system_audio: bool,
        include_microphone: bool,
        exclude_current_process_audio: bool,
        callback: NativeFrameCallback,
        context: *mut c_void,
    ) -> i32;
    fn amcp_audio_capture_bridge_stop_capture(handle: *mut c_void) -> i32;
    fn amcp_audio_capture_bridge_capture_status(handle: *mut c_void) -> i32;
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
    PayloadUtf8Failed,
    #[error("The native audio capture bridge returned invalid data.")]
    JsonParseFailed,
    #[error("The native audio capture bridge returned invalid data.")]
    TopLevelShapeMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    DtoValidationFailed,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayFieldSetMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayIdTypeMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayIdOutOfRange,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayWidthTypeMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayWidthZero,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayWidthOutOfRange,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayHeightTypeMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayHeightZero,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayHeightOutOfRange,
    #[error("The native audio capture bridge returned invalid data.")]
    DisplayIsPrimaryTypeMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    MicrophoneFieldSetMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    MicrophoneIdTypeMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    MicrophoneIdBlank,
    #[error("The native audio capture bridge returned invalid data.")]
    MicrophoneIsDefaultTypeMismatch,
    #[error("The native audio capture bridge returned invalid data.")]
    MalformedNativePayload,
}

impl SwiftAudioCaptureBridgeError {
    #[cfg(debug_assertions)]
    pub(crate) fn enumeration_diagnostic(&self) -> String {
        match self {
            Self::PayloadUtf8Failed => "payload_utf8_failed".to_owned(),
            Self::JsonParseFailed => "json_parse_failed".to_owned(),
            Self::TopLevelShapeMismatch => "top_level_shape_mismatch".to_owned(),
            Self::DtoValidationFailed => "dto_validation_failed".to_owned(),
            Self::DisplayFieldSetMismatch | Self::MicrophoneFieldSetMismatch => {
                "field_set_mismatch".to_owned()
            }
            Self::DisplayIdTypeMismatch | Self::MicrophoneIdTypeMismatch => {
                "id_type_mismatch".to_owned()
            }
            Self::DisplayIdOutOfRange => "id_out_of_range".to_owned(),
            Self::DisplayWidthTypeMismatch => "width_type_mismatch".to_owned(),
            Self::DisplayWidthZero => "width_zero".to_owned(),
            Self::DisplayWidthOutOfRange => "width_out_of_range".to_owned(),
            Self::DisplayHeightTypeMismatch => "height_type_mismatch".to_owned(),
            Self::DisplayHeightZero => "height_zero".to_owned(),
            Self::DisplayHeightOutOfRange => "height_out_of_range".to_owned(),
            Self::DisplayIsPrimaryTypeMismatch => "is_primary_type_mismatch".to_owned(),
            Self::MicrophoneIdBlank => "id_blank".to_owned(),
            Self::MicrophoneIsDefaultTypeMismatch => "is_default_type_mismatch".to_owned(),
            Self::NativeDataUnavailable => "native_data_unavailable".to_owned(),
            _ => "bridge_failed".to_owned(),
        }
    }
}

/// Owns a single Swift object handle without exposing it outside this module.
pub(crate) struct SwiftAudioCaptureBridge<
    F: SwiftAudioCaptureFunctions = SystemSwiftAudioCaptureFunctions,
> {
    functions: F,
    handle: Option<NonNull<c_void>>,
    callback_state: Option<Arc<CallbackState>>,
    callback_context: Option<*const CallbackState>,
}

// The opaque Swift object is never dereferenced by Rust. Every lifecycle FFI
// entry point synchronously transfers execution to Swift's main queue, while
// the command state serializes Rust-side ownership with a mutex. Callback
// state is Arc-owned and is explicitly unregistered before bridge destruction.
// These facts make moving the Rust handle between Tauri worker threads safe.
unsafe impl<F: SwiftAudioCaptureFunctions> Send for SwiftAudioCaptureBridge<F> {}

impl SwiftAudioCaptureBridge<SystemSwiftAudioCaptureFunctions> {
    /// Creates the macOS bridge. Other platforms have no Swift runtime bridge.
    #[cfg(target_os = "macos")]
    pub(crate) fn new() -> Result<Self, SwiftAudioCaptureBridgeError> {
        Self::with_functions(SystemSwiftAudioCaptureFunctions)
    }

    /// Provides a clear unsupported path for non-macOS development builds.
    #[cfg(not(target_os = "macos"))]
    pub(crate) fn new() -> Result<Self, SwiftAudioCaptureBridgeError> {
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
            callback_state: None,
            callback_context: None,
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
    pub(crate) fn screen_authorization_state(
        &self,
    ) -> Result<ScreenCaptureAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.screen_authorization_state(handle))
            .and_then(|payload| parse_screen_authorization(&payload))
    }

    /// Explicitly requests screen-capture authorization at most once in Swift.
    pub(crate) fn request_screen_authorization(
        &self,
    ) -> Result<ScreenCaptureAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.request_screen_authorization(handle))
            .and_then(|payload| parse_screen_authorization(&payload))
    }

    /// Queries microphone authorization without starting an audio engine.
    pub(crate) fn microphone_authorization_state(
        &self,
    ) -> Result<MicrophoneAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.microphone_authorization_state(handle))
            .and_then(|payload| parse_microphone_authorization(&payload))
    }

    /// Explicitly requests microphone authorization at most once by the system API.
    pub(crate) fn request_microphone_authorization(
        &self,
    ) -> Result<MicrophoneAuthorizationState, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.request_microphone_authorization(handle))
            .and_then(|payload| parse_microphone_authorization(&payload))
    }

    /// Fetches a fresh, privacy-filtered list of ScreenCaptureKit displays.
    pub(crate) fn list_displays(
        &self,
    ) -> Result<Vec<CaptureDisplaySource>, SwiftAudioCaptureBridgeError> {
        self.read_json(|functions, handle| functions.list_displays(handle))
            .and_then(|payload| parse_displays(&payload))
    }

    /// Fetches a fresh list of microphone device identifiers without opening an engine.
    pub(crate) fn list_microphones(
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
        std::str::from_utf8(&payload)
            .map_err(|_| SwiftAudioCaptureBridgeError::PayloadUtf8Failed)?;
        Ok(payload)
    }

    fn handle(&self) -> Result<NonNull<c_void>, SwiftAudioCaptureBridgeError> {
        self.handle
            .ok_or(SwiftAudioCaptureBridgeError::NativeOperationFailed)
    }
}

extern "C" fn receive_native_frame(
    context: *mut c_void,
    source: u8,
    sample_rate_hz: u32,
    channels: u16,
    sample_format: u8,
    interleaved: bool,
    timestamp: f64,
    samples: *const c_void,
    sample_count: usize,
) {
    if context.is_null() || samples.is_null() {
        return;
    }
    let state = unsafe { &*(context as *const CallbackState) };
    if !state.accepting.load(Ordering::Acquire) {
        return;
    }
    let (source, format, samples) = match (source, sample_format) {
        (1, 1) => (
            NativeAudioSource::SystemAudio,
            NativeSampleFormat::Float32,
            NativeAudioSamples::Float32(
                unsafe { std::slice::from_raw_parts(samples.cast::<f32>(), sample_count) }.to_vec(),
            ),
        ),
        (1, 2) => (
            NativeAudioSource::SystemAudio,
            NativeSampleFormat::SignedInt16,
            NativeAudioSamples::SignedInt16(
                unsafe { std::slice::from_raw_parts(samples.cast::<i16>(), sample_count) }.to_vec(),
            ),
        ),
        (2, 1) => (
            NativeAudioSource::Microphone,
            NativeSampleFormat::Float32,
            NativeAudioSamples::Float32(
                unsafe { std::slice::from_raw_parts(samples.cast::<f32>(), sample_count) }.to_vec(),
            ),
        ),
        (2, 2) => (
            NativeAudioSource::Microphone,
            NativeSampleFormat::SignedInt16,
            NativeAudioSamples::SignedInt16(
                unsafe { std::slice::from_raw_parts(samples.cast::<i16>(), sample_count) }.to_vec(),
            ),
        ),
        _ => return,
    };
    let Ok(format) = NativeAudioFormat::new(sample_rate_hz, channels, format, interleaved) else {
        return;
    };
    let Ok(frame) = NativeAudioFrame::new(source, format, timestamp, samples) else {
        return;
    };
    #[cfg(debug_assertions)]
    let frame_duration_microseconds = frame.duration_microseconds();
    #[cfg(debug_assertions)]
    let valid_callback_count = state
        .throughput_metrics
        .record_input(source, frame_duration_microseconds);
    #[cfg(debug_assertions)]
    if valid_callback_count == 1 {
        eprintln!(
            "audio-capture callback received count=1 generation={}",
            state.generation
        );
    }
    if let Some(sender) = state.sender.lock().ok().and_then(|guard| guard.clone()) {
        #[cfg(debug_assertions)]
        let generation = sender.generation();
        match sender.try_send(frame) {
            Ok(()) => {
                #[cfg(debug_assertions)]
                state
                    .throughput_metrics
                    .record_accepted(source, frame_duration_microseconds);
            }
            Err(super::bridge::NativeAudioFrameSendError::Full) => {
                #[cfg(debug_assertions)]
                if state
                    .throughput_metrics
                    .record_dropped_full(source, frame_duration_microseconds)
                {
                    eprintln!(
                        "audio-capture callback frame dropped reason=channel_full generation={generation}"
                    );
                }
            }
            Err(super::bridge::NativeAudioFrameSendError::Closed) => {
                #[cfg(debug_assertions)]
                if state
                    .throughput_metrics
                    .record_dropped_closed(source, frame_duration_microseconds)
                {
                    eprintln!(
                        "audio-capture callback frame dropped reason=channel_closed generation={generation}"
                    );
                }
            }
        }
    }
    #[cfg(debug_assertions)]
    state
        .throughput_metrics
        .report_if_due(state.generation, valid_callback_count);
}

impl<F: SwiftAudioCaptureFunctions> NativeAudioCaptureBridge for SwiftAudioCaptureBridge<F> {
    fn start(
        &mut self,
        configuration: &AudioCaptureConfiguration,
        frame_sender: NativeAudioFrameSender,
    ) -> Result<(), NativeAudioCaptureBridgeError> {
        if self.callback_state.is_some() {
            return Err(NativeAudioCaptureBridgeError::StartFailed);
        }
        let state = Arc::new(CallbackState {
            accepting: AtomicBool::new(true),
            #[cfg(debug_assertions)]
            generation: frame_sender.generation(),
            #[cfg(debug_assertions)]
            throughput_metrics: CallbackThroughputMetrics::new(),
            sender: Mutex::new(Some(frame_sender)),
        });
        let callback_context = Arc::into_raw(state.clone());
        let context = callback_context.cast_mut().cast::<c_void>();
        if self.functions.start_capture(
            self.handle()
                .map_err(|_| NativeAudioCaptureBridgeError::StartFailed)?
                .as_ptr(),
            configuration,
            receive_native_frame,
            context,
        ) != NATIVE_SUCCESS
        {
            unsafe {
                drop(Arc::from_raw(callback_context));
            }
            return Err(NativeAudioCaptureBridgeError::StartFailed);
        }
        self.callback_state = Some(state);
        self.callback_context = Some(callback_context);
        Ok(())
    }
    fn stop(&mut self) -> Result<(), NativeAudioCaptureBridgeError> {
        if self.callback_state.is_none() {
            return Ok(());
        }
        if self.functions.stop_capture(
            self.handle()
                .map_err(|_| NativeAudioCaptureBridgeError::StopFailed)?
                .as_ptr(),
        ) != NATIVE_SUCCESS
        {
            return Err(NativeAudioCaptureBridgeError::StopFailed);
        }
        if let Some(state) = self.callback_state.take() {
            #[cfg(debug_assertions)]
            state.throughput_metrics.report_at_stop(state.generation);
            state.accepting.store(false, Ordering::Release);
            *state
                .sender
                .lock()
                .map_err(|_| NativeAudioCaptureBridgeError::StopFailed)? = None;
        }
        if let Some(context) = self.callback_context.take() {
            unsafe {
                drop(Arc::from_raw(context));
            }
        }
        Ok(())
    }
    fn status(&self) -> AudioCaptureStatus {
        let state = match self
            .handle()
            .map(|handle| self.functions.capture_status(handle.as_ptr()))
        {
            Ok(1) => AudioCaptureState::Capturing,
            Ok(2) => AudioCaptureState::Failed,
            _ => AudioCaptureState::Stopped,
        };
        AudioCaptureStatus::new(state, None).expect("static status is valid")
    }
}

impl<F> Drop for SwiftAudioCaptureBridge<F>
where
    F: SwiftAudioCaptureFunctions,
{
    fn drop(&mut self) {
        let _ = self.stop();
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
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc, Mutex,
        },
    };
    use tokio::sync::mpsc;

    #[cfg(debug_assertions)]
    use super::CallbackThroughputMetrics;
    use super::{
        receive_native_frame, AudioCaptureConfiguration, AudioCaptureState, CallbackState,
        NativeAudioCaptureBridge, NativeAudioFrameSender, NativeFrameCallback,
        SwiftAudioCaptureBridge, SwiftAudioCaptureBridgeError, SwiftAudioCaptureFunctions,
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
        last_capture_configuration: Option<AudioCaptureConfiguration>,
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
                    last_capture_configuration: None,
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

        fn start_capture(
            &self,
            _handle: *mut c_void,
            configuration: &AudioCaptureConfiguration,
            _callback: NativeFrameCallback,
            _context: *mut c_void,
        ) -> i32 {
            let mut state = self.state.lock().expect("state is available");
            state.status = 1;
            state.last_capture_configuration = Some(configuration.clone());
            0
        }

        fn stop_capture(&self, _handle: *mut c_void) -> i32 {
            self.state.lock().expect("state is available").status = 0;
            0
        }

        fn capture_status(&self, _handle: *mut c_void) -> i32 {
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

    #[tokio::test]
    async fn parses_authorization_and_frees_the_native_buffer_once() {
        let functions = FakeFunctions::new(0);
        let state = functions.state.clone();
        let bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");

        assert_eq!(
            bridge
                .screen_authorization_state()
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

        assert!(bridge.list_displays().expect("parses displays").is_empty());
        assert!(bridge
            .list_microphones()
            .expect("parses microphones")
            .is_empty());
    }

    #[tokio::test]
    async fn malformed_native_data_is_released_before_returning_a_safe_error() {
        let functions = FakeFunctions::new(0).with_screen_payload(Some("not JSON"));
        let state = functions.state.clone();
        let bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");

        assert_eq!(
            bridge.screen_authorization_state(),
            Err(SwiftAudioCaptureBridgeError::MalformedNativePayload)
        );
        assert_eq!(
            state.lock().expect("state is available").freed_json_buffers,
            1
        );
    }

    #[test]
    fn callback_copies_float_and_int16_frames_without_blocking() {
        let (sender, mut receiver) = mpsc::channel(1);
        let state = Arc::new(CallbackState {
            accepting: AtomicBool::new(true),
            #[cfg(debug_assertions)]
            generation: 0,
            #[cfg(debug_assertions)]
            throughput_metrics: CallbackThroughputMetrics::new(),
            sender: Mutex::new(Some(NativeAudioFrameSender::new(sender))),
        });
        let context = Arc::into_raw(state.clone()).cast_mut().cast::<c_void>();
        let float_samples = [0.25_f32, -0.25_f32];
        receive_native_frame(
            context,
            1,
            48_000,
            2,
            1,
            true,
            1.0,
            float_samples.as_ptr().cast(),
            float_samples.len(),
        );
        // The callback remains non-blocking when the bounded native handoff
        // is full; debug metrics retain only the structural drop count.
        receive_native_frame(
            context,
            1,
            48_000,
            2,
            1,
            true,
            1.1,
            float_samples.as_ptr().cast(),
            float_samples.len(),
        );
        let frame = receiver.try_recv().expect("frame is copied");
        assert_eq!(
            frame.source(),
            crate::audio_capture::types::NativeAudioSource::SystemAudio
        );
        assert_eq!(frame.format().channels(), 2);
        assert!(
            matches!(frame.samples(), crate::audio_capture::types::NativeAudioSamples::Float32(values) if values == &float_samples)
        );
        #[cfg(debug_assertions)]
        {
            let system_metrics = state.throughput_metrics.system.snapshot();
            assert_eq!(system_metrics.valid_frame_count, 2);
            assert_eq!(system_metrics.accepted_frame_count, 1);
            assert_eq!(system_metrics.dropped_full_count, 1);
            assert_eq!(system_metrics.dropped_closed_count, 0);
        }
        state.accepting.store(false, Ordering::Release);
        let int_samples = [1_i16];
        receive_native_frame(
            context,
            2,
            48_000,
            1,
            2,
            true,
            2.0,
            int_samples.as_ptr().cast(),
            1,
        );
        assert!(receiver.try_recv().is_err());
        unsafe {
            drop(Arc::from_raw(context.cast::<CallbackState>()));
        }
    }

    #[test]
    fn callback_preserves_noninterleaved_system_audio_planes() {
        let (sender, mut receiver) = mpsc::channel(1);
        let state = Arc::new(CallbackState {
            accepting: AtomicBool::new(true),
            #[cfg(debug_assertions)]
            generation: 0,
            #[cfg(debug_assertions)]
            throughput_metrics: CallbackThroughputMetrics::new(),
            sender: Mutex::new(Some(NativeAudioFrameSender::new(sender))),
        });
        let context = Arc::into_raw(Arc::clone(&state))
            .cast_mut()
            .cast::<c_void>();
        // ScreenCaptureKit can expose one Float32 plane per channel. The
        // native boundary concatenates those planes and labels the frame as
        // non-interleaved so Rust's mixer can retain their channel layout.
        let channel_planes = [0.25_f32, -0.25_f32, 0.75_f32, -0.75_f32];

        receive_native_frame(
            context,
            1,
            48_000,
            2,
            1,
            false,
            1.0,
            channel_planes.as_ptr().cast(),
            channel_planes.len(),
        );

        let frame = receiver.try_recv().expect("system frame is copied");
        assert_eq!(
            frame.source(),
            crate::audio_capture::types::NativeAudioSource::SystemAudio
        );
        assert!(!frame.format().interleaved());
        assert!(
            matches!(frame.samples(), crate::audio_capture::types::NativeAudioSamples::Float32(values) if values == &channel_planes)
        );

        state.accepting.store(false, Ordering::Release);
        unsafe {
            drop(Arc::from_raw(context.cast::<CallbackState>()));
        }
    }

    #[test]
    fn forwards_system_only_configuration_to_the_native_start_boundary() {
        let functions = FakeFunctions::new(0);
        let state = Arc::clone(&functions.state);
        let mut bridge = SwiftAudioCaptureBridge::with_functions(functions).expect("creates");
        let (sender, _receiver) = mpsc::channel(1);
        let configuration = AudioCaptureConfiguration::new(7, None, true, false, true)
            .expect("system-only configuration");

        NativeAudioCaptureBridge::start(
            &mut bridge,
            &configuration,
            NativeAudioFrameSender::new(sender),
        )
        .expect("starts native capture");

        assert_eq!(
            state
                .lock()
                .expect("state is available")
                .last_capture_configuration,
            Some(configuration)
        );
    }
}
