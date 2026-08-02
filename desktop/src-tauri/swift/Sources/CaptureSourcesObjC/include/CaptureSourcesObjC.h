#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

char * _Nullable amcp_copy_display_sources_json(void);
char * _Nullable amcp_copy_microphone_sources_json(void);
void amcp_free_capture_sources_json(char * _Nullable buffer);

typedef void (*AMCPAudioFrameCallback)(void * _Nullable context,
                                       uint8_t source,
                                       uint32_t sample_rate_hz,
                                       uint16_t channels,
                                       uint8_t sample_format,
                                       bool interleaved,
                                       double capture_time_seconds,
                                       const void * _Nonnull samples,
                                       size_t sample_count);

typedef struct {
  uint32_t display_id;
  const char * _Nullable microphone_device_id;
  bool include_system_audio;
  bool include_microphone;
  bool exclude_current_process_audio;
} AMCPCaptureConfiguration;

void * _Nullable amcp_capture_engine_create(void);
void amcp_capture_engine_destroy(void * _Nullable engine);
int32_t amcp_capture_engine_start(void * _Nullable engine,
                                  AMCPCaptureConfiguration configuration,
                                  AMCPAudioFrameCallback _Nullable callback,
                                  void * _Nullable context);
int32_t amcp_capture_engine_stop(void * _Nullable engine);
int32_t amcp_capture_engine_status(void * _Nullable engine);

NS_ASSUME_NONNULL_END
