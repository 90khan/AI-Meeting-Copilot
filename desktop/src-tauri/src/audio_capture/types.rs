//! Platform-neutral native audio types emitted by the future Swift bridge.

use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeAudioSource {
    SystemAudio,
    Microphone,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeSampleFormat {
    Float32,
    SignedInt16,
}

/// Immutable description of the format supplied by a native audio callback.
#[derive(Clone, PartialEq, Eq)]
pub struct NativeAudioFormat {
    sample_rate_hz: u32,
    channels: u16,
    sample_format: NativeSampleFormat,
    interleaved: bool,
}

impl NativeAudioFormat {
    pub fn new(
        sample_rate_hz: u32,
        channels: u16,
        sample_format: NativeSampleFormat,
        interleaved: bool,
    ) -> Result<Self, NativeAudioValidationError> {
        if sample_rate_hz == 0 || channels == 0 {
            return Err(NativeAudioValidationError::InvalidFormat);
        }
        Ok(Self {
            sample_rate_hz,
            channels,
            sample_format,
            interleaved,
        })
    }

    pub const fn sample_rate_hz(&self) -> u32 {
        self.sample_rate_hz
    }

    pub const fn channels(&self) -> u16 {
        self.channels
    }

    pub const fn sample_format(&self) -> NativeSampleFormat {
        self.sample_format
    }

    pub const fn interleaved(&self) -> bool {
        self.interleaved
    }
}

impl std::fmt::Debug for NativeAudioFormat {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("NativeAudioFormat")
            .field("sample_rate_hz", &self.sample_rate_hz)
            .field("channels", &self.channels)
            .field("sample_format", &self.sample_format)
            .field("interleaved", &self.interleaved)
            .finish()
    }
}

pub enum NativeAudioSamples {
    Float32(Vec<f32>),
    SignedInt16(Vec<i16>),
}

impl NativeAudioSamples {
    pub fn len(&self) -> usize {
        match self {
            Self::Float32(samples) => samples.len(),
            Self::SignedInt16(samples) => samples.len(),
        }
    }

    fn format(&self) -> NativeSampleFormat {
        match self {
            Self::Float32(_) => NativeSampleFormat::Float32,
            Self::SignedInt16(_) => NativeSampleFormat::SignedInt16,
        }
    }
}

/// Immutable, copied audio data with no Apple-framework or pointer types.
pub struct NativeAudioFrame {
    source: NativeAudioSource,
    format: NativeAudioFormat,
    capture_time_seconds: f64,
    samples: NativeAudioSamples,
}

impl NativeAudioFrame {
    pub fn new(
        source: NativeAudioSource,
        format: NativeAudioFormat,
        capture_time_seconds: f64,
        samples: NativeAudioSamples,
    ) -> Result<Self, NativeAudioValidationError> {
        if !capture_time_seconds.is_finite() || capture_time_seconds < 0.0 {
            return Err(NativeAudioValidationError::InvalidTimestamp);
        }
        if samples.len() == 0
            || samples.len() % usize::from(format.channels()) != 0
            || samples.format() != format.sample_format()
        {
            return Err(NativeAudioValidationError::InvalidSamples);
        }
        Ok(Self {
            source,
            format,
            capture_time_seconds,
            samples,
        })
    }

    pub const fn source(&self) -> NativeAudioSource {
        self.source
    }

    pub const fn format(&self) -> &NativeAudioFormat {
        &self.format
    }

    pub const fn capture_time_seconds(&self) -> f64 {
        self.capture_time_seconds
    }

    pub const fn samples(&self) -> &NativeAudioSamples {
        &self.samples
    }
}

impl std::fmt::Debug for NativeAudioSamples {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("NativeAudioSamples")
            .field("sample_count", &self.len())
            .finish()
    }
}

impl std::fmt::Debug for NativeAudioFrame {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("NativeAudioFrame")
            .field("source", &self.source)
            .field("format", &self.format)
            .field("capture_time_seconds", &self.capture_time_seconds)
            .field("samples", &self.samples)
            .finish()
    }
}

/// Configuration for selecting native sources, before signal processing begins.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioCaptureConfiguration {
    display_id: u32,
    microphone_device_id: Option<String>,
    include_system_audio: bool,
    include_microphone: bool,
    exclude_current_process_audio: bool,
}

impl AudioCaptureConfiguration {
    pub fn new(
        display_id: u32,
        microphone_device_id: Option<String>,
        include_system_audio: bool,
        include_microphone: bool,
        exclude_current_process_audio: bool,
    ) -> Result<Self, NativeAudioValidationError> {
        if !include_system_audio && !include_microphone {
            return Err(NativeAudioValidationError::NoAudioSource);
        }
        let microphone_device_id = match microphone_device_id {
            Some(device_id) if device_id.trim().is_empty() => {
                return Err(NativeAudioValidationError::InvalidMicrophoneDevice)
            }
            Some(device_id) => Some(device_id.trim().to_owned()),
            None => None,
        };
        Ok(Self {
            display_id,
            microphone_device_id,
            include_system_audio,
            include_microphone,
            exclude_current_process_audio,
        })
    }

    pub const fn display_id(&self) -> u32 {
        self.display_id
    }

    pub fn microphone_device_id(&self) -> Option<&str> {
        self.microphone_device_id.as_deref()
    }

    pub const fn include_system_audio(&self) -> bool {
        self.include_system_audio
    }

    pub const fn include_microphone(&self) -> bool {
        self.include_microphone
    }

    pub const fn exclude_current_process_audio(&self) -> bool {
        self.exclude_current_process_audio
    }
}

impl Default for AudioCaptureConfiguration {
    fn default() -> Self {
        Self {
            display_id: 0,
            microphone_device_id: None,
            include_system_audio: true,
            include_microphone: true,
            exclude_current_process_audio: true,
        }
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum NativeAudioValidationError {
    #[error("The native audio format is invalid.")]
    InvalidFormat,
    #[error("The native audio timestamp is invalid.")]
    InvalidTimestamp,
    #[error("The native audio samples are invalid.")]
    InvalidSamples,
    #[error("At least one audio source is required.")]
    NoAudioSource,
    #[error("The microphone device selection is invalid.")]
    InvalidMicrophoneDevice,
}

#[cfg(test)]
mod tests {
    use super::{
        AudioCaptureConfiguration, NativeAudioFormat, NativeAudioFrame, NativeAudioSamples,
        NativeAudioSource, NativeAudioValidationError, NativeSampleFormat,
    };

    fn format(channels: u16) -> NativeAudioFormat {
        NativeAudioFormat::new(48_000, channels, NativeSampleFormat::Float32, false)
            .expect("format is valid")
    }

    #[test]
    fn serializes_native_enum_values() {
        assert_eq!(
            serde_json::to_string(&NativeAudioSource::SystemAudio).expect("serializes"),
            r#""system_audio""#
        );
        assert_eq!(
            serde_json::to_string(&NativeSampleFormat::SignedInt16).expect("serializes"),
            r#""signed_int16""#
        );
    }

    #[test]
    fn rejects_invalid_formats_and_frames() {
        assert_eq!(
            NativeAudioFormat::new(0, 1, NativeSampleFormat::Float32, false),
            Err(NativeAudioValidationError::InvalidFormat)
        );
        assert!(matches!(
            NativeAudioFrame::new(
                NativeAudioSource::Microphone,
                format(2),
                f64::NAN,
                NativeAudioSamples::Float32(vec![0.0, 0.0]),
            ),
            Err(NativeAudioValidationError::InvalidTimestamp)
        ));
        assert!(matches!(
            NativeAudioFrame::new(
                NativeAudioSource::Microphone,
                format(2),
                0.0,
                NativeAudioSamples::Float32(vec![]),
            ),
            Err(NativeAudioValidationError::InvalidSamples)
        ));
        assert!(matches!(
            NativeAudioFrame::new(
                NativeAudioSource::Microphone,
                format(2),
                0.0,
                NativeAudioSamples::Float32(vec![0.0, 0.0, 0.0]),
            ),
            Err(NativeAudioValidationError::InvalidSamples)
        ));
    }

    #[test]
    fn validates_default_and_explicit_capture_configuration() {
        let configuration = AudioCaptureConfiguration::default();
        assert!(configuration.include_system_audio());
        assert!(configuration.include_microphone());
        assert!(configuration.exclude_current_process_audio());

        assert_eq!(
            AudioCaptureConfiguration::new(0, None, false, false, true),
            Err(NativeAudioValidationError::NoAudioSource)
        );
        assert_eq!(
            AudioCaptureConfiguration::new(0, Some("  ".to_owned()), true, true, true),
            Err(NativeAudioValidationError::InvalidMicrophoneDevice)
        );
    }
}
