#import "CaptureSourcesObjC.h"

#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <CoreMedia/CoreMedia.h>
#include <stdlib.h>

enum { AMCP_STATUS_STOPPED = 0, AMCP_STATUS_CAPTURING = 1, AMCP_STATUS_FAILED = 2 };
enum { AMCP_OK = 0, AMCP_FAILURE = 1, AMCP_PERMISSION = 2, AMCP_SOURCE = 3 };

#if AMCP_DEBUG_ENUMERATION_DIAGNOSTICS
static void AMCPLogDisplayEnumerationClassification(const char *classification) {
  fprintf(stderr, "audio-capture display enumeration=%s\n", classification);
}

static void AMCPLogMicrophoneEnumerationClassification(const char *classification) {
  fprintf(stderr, "audio-capture microphone enumeration=%s\n", classification);
}

static void AMCPLogDisplayBooleanRepresentation(NSNumber *value) {
  const char *representation = CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()
      ? "cfboolean"
      : "number";
  fprintf(stderr, "audio-capture display_boolean_representation=%s\n", representation);
}

static void AMCPLogMicrophoneBooleanRepresentation(NSNumber *value) {
  const char *representation = CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()
      ? "cfboolean"
      : "number";
  fprintf(stderr, "audio-capture microphone_boolean_representation=%s\n", representation);
}

static void AMCPLogObjCDisplayEntry(void) {
  fprintf(stderr, "audio-capture objc display entered\n");
}

static void AMCPLogObjCMicrophoneEntry(void) {
  fprintf(stderr, "audio-capture objc microphone entered\n");
}
#else
static void AMCPLogDisplayEnumerationClassification(const char *classification) {
  (void)classification;
}

static void AMCPLogMicrophoneEnumerationClassification(const char *classification) {
  (void)classification;
}

static void AMCPLogDisplayBooleanRepresentation(NSNumber *value) {
  (void)value;
}

static void AMCPLogMicrophoneBooleanRepresentation(NSNumber *value) {
  (void)value;
}

static void AMCPLogObjCDisplayEntry(void) {
}

static void AMCPLogObjCMicrophoneEntry(void) {
}
#endif

#if AMCP_DEBUG_CAPTURE_DIAGNOSTICS
static void AMCPLogCaptureConfiguration(AMCPCaptureConfiguration configuration) {
  fprintf(stderr,
          "audio-capture native configuration system_audio=%s microphone=%s exclude_current_process_audio=%s\n",
          configuration.include_system_audio ? "true" : "false",
          configuration.include_microphone ? "true" : "false",
          configuration.exclude_current_process_audio ? "true" : "false");
}

static void AMCPLogCaptureBoundary(const char *boundary, const char *outcome) {
  fprintf(stderr, "audio-capture native %s=%s\n", boundary, outcome);
}

static void AMCPLogFirstSystemOutputCallback(uint64_t count) {
  if (count == 1) {
    fprintf(stderr, "audio-capture native system output callback received count=1\n");
  }
}

static void AMCPLogFirstSystemOutputDispatch(uint64_t count) {
  if (count == 1) {
    fprintf(stderr, "audio-capture native system output callback dispatched count=1\n");
  }
}

static void AMCPLogFirstSystemOutputFormat(uint64_t count,
                                           uint8_t sampleFormat,
                                           uint32_t channels,
                                           BOOL interleaved) {
  if (count == 1) {
    const char *format = sampleFormat == 1 ? "float32" : "signed_int16";
    fprintf(stderr,
            "audio-capture native system output format=%s channels=%u interleaved=%s\n",
            format, channels, interleaved ? "true" : "false");
  }
}
#else
static void AMCPLogCaptureConfiguration(AMCPCaptureConfiguration configuration) {
  (void)configuration;
}

static void AMCPLogCaptureBoundary(const char *boundary, const char *outcome) {
  (void)boundary;
  (void)outcome;
}

static void AMCPLogFirstSystemOutputCallback(uint64_t count) {
  (void)count;
}

static void AMCPLogFirstSystemOutputDispatch(uint64_t count) {
  (void)count;
}

static void AMCPLogFirstSystemOutputFormat(uint64_t count,
                                           uint8_t sampleFormat,
                                           uint32_t channels,
                                           BOOL interleaved) {
  (void)count;
  (void)sampleFormat;
  (void)channels;
  (void)interleaved;
}
#endif

@interface AMCPCaptureEngine : NSObject <SCStreamOutput, SCStreamDelegate>
@property(nonatomic, strong) SCStream *stream;
@property(nonatomic) AMCPAudioFrameCallback callback;
@property(nonatomic) void *callbackContext;
@property(nonatomic) BOOL acceptingFrames;
@property(nonatomic) int32_t status;
@property(nonatomic) uint64_t systemOutputCallbackCount;
@property(nonatomic) BOOL systemOutputRejectionReported;
@end

@implementation AMCPCaptureEngine

- (void)reportSystemOutputRejection:(const char *)outcome {
  if (self.systemOutputRejectionReported) return;
  self.systemOutputRejectionReported = YES;
  AMCPLogCaptureBoundary("system_output_rejected", outcome);
}

- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer ofType:(SCStreamOutputType)type {
  if (type == SCStreamOutputTypeAudio) {
    self.systemOutputCallbackCount += 1;
    AMCPLogFirstSystemOutputCallback(self.systemOutputCallbackCount);
  }
  if (!self.acceptingFrames) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"not_accepting"];
    return;
  }
  if (self.callback == NULL) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"callback_unavailable"];
    return;
  }
  if (!CMSampleBufferDataIsReady(sampleBuffer)) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"sample_not_ready"];
    return;
  }
  if (type != SCStreamOutputTypeAudio && type != SCStreamOutputTypeMicrophone) return;
  CMAudioFormatDescriptionRef description = CMSampleBufferGetFormatDescription(sampleBuffer);
  if (description == NULL) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"format_description_unavailable"];
    return;
  }
  const AudioStreamBasicDescription *format = CMAudioFormatDescriptionGetStreamBasicDescription(description);
  if (format == NULL || format->mChannelsPerFrame == 0) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"invalid_format"];
    return;
  }
  uint8_t sampleFormat = 0;
  if ((format->mFormatFlags & kAudioFormatFlagIsFloat) && format->mBitsPerChannel == 32) sampleFormat = 1;
  if ((format->mFormatFlags & kAudioFormatFlagIsSignedInteger) && format->mBitsPerChannel == 16) sampleFormat = 2;
  if (sampleFormat == 0) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"unsupported_pcm_format"];
    return;
  }
  if (type == SCStreamOutputTypeAudio) {
    AMCPLogFirstSystemOutputFormat(self.systemOutputCallbackCount, sampleFormat,
                                   format->mChannelsPerFrame,
                                   (format->mFormatFlags & kAudioFormatFlagIsNonInterleaved) == 0);
  }
  size_t bufferListSize = 0;
  OSStatus listStatus = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
      sampleBuffer, &bufferListSize, NULL, 0, NULL, NULL, 0, NULL);
  if (listStatus != noErr || bufferListSize < sizeof(AudioBufferList)) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"audio_buffer_list_size_unavailable"];
    return;
  }
  AudioBufferList *bufferList = malloc(bufferListSize);
  if (bufferList == NULL) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"audio_buffer_list_allocation_failed"];
    return;
  }
  CMBlockBufferRef blockBuffer = NULL;
  listStatus = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
      sampleBuffer, NULL, bufferList, bufferListSize, NULL, NULL, 0, &blockBuffer);
  if (listStatus != noErr) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"audio_buffer_list_unavailable"];
    free(bufferList);
    return;
  }
  size_t bytes = 0;
  for (UInt32 index = 0; index < bufferList->mNumberBuffers; index++) bytes += bufferList->mBuffers[index].mDataByteSize;
  size_t width = sampleFormat == 1 ? sizeof(float) : sizeof(int16_t);
  if (bytes == 0 || bytes % width != 0) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"invalid_sample_count"];
    if (blockBuffer) CFRelease(blockBuffer);
    free(bufferList);
    return;
  }
  NSMutableData *samples = [NSMutableData dataWithLength:bytes];
  uint8_t *destination = samples.mutableBytes;
  for (UInt32 index = 0; index < bufferList->mNumberBuffers; index++) {
    AudioBuffer buffer = bufferList->mBuffers[index];
    memcpy(destination, buffer.mData, buffer.mDataByteSize);
    destination += buffer.mDataByteSize;
  }
  // The sample buffer PTS is the beginning of this audio buffer on the
  // stream's synchronization timeline. Reading the clock at callback receipt
  // time instead would reflect dispatch scheduling rather than sample timing.
  CMTime presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer);
  double timestamp = CMTimeGetSeconds(presentationTime);
  if (!isfinite(timestamp) || timestamp < 0) {
    if (type == SCStreamOutputTypeAudio) [self reportSystemOutputRejection:"invalid_timestamp"];
    if (blockBuffer) CFRelease(blockBuffer);
    free(bufferList);
    return;
  }
  if (type == SCStreamOutputTypeAudio) AMCPLogFirstSystemOutputDispatch(self.systemOutputCallbackCount);
  self.callback(self.callbackContext, type == SCStreamOutputTypeAudio ? 1 : 2,
                (uint32_t)format->mSampleRate, (uint16_t)format->mChannelsPerFrame, sampleFormat,
                (format->mFormatFlags & kAudioFormatFlagIsNonInterleaved) == 0, timestamp,
                samples.bytes, bytes / width);
  if (blockBuffer) CFRelease(blockBuffer);
  free(bufferList);
}

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error {
  AMCPLogCaptureBoundary("stream_stopped", "unexpected_error");
  self.acceptingFrames = NO;
  self.status = AMCP_STATUS_FAILED;
}
@end

typedef void (*AMCPEnumerationClassificationLogger)(const char *classification);

static char *AMCPDuplicateJSON(id object, AMCPEnumerationClassificationLogger logClassification) {
  NSError *error = nil;
  NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:&error];
  if (error != nil || data == nil) {
    logClassification("serialization_failed");
    return NULL;
  }
  NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
  if (json == nil || json.UTF8String == NULL) {
    logClassification("serialization_failed");
    return NULL;
  }
  char *copy = strdup(json.UTF8String);
  if (copy == NULL) {
    logClassification("allocation_failed");
  }
  return copy;
}

char *amcp_copy_display_sources_json(void) {
  AMCPLogObjCDisplayEntry();
  if (!CGPreflightScreenCaptureAccess()) {
    AMCPLogDisplayEnumerationClassification("permission_denied");
    return NULL;
  }
  dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
  if (semaphore == NULL) {
    AMCPLogDisplayEnumerationClassification("allocation_failed");
    return NULL;
  }
  __block char *result = NULL;

  [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                             onScreenWindowsOnly:YES
                                               completionHandler:^(SCShareableContent *content, NSError *error) {
    if (error != nil) {
      AMCPLogDisplayEnumerationClassification("shareable_content_error");
    } else if (content == nil) {
      AMCPLogDisplayEnumerationClassification("bridge_failed");
    } else {
      CGDirectDisplayID primaryDisplayID = CGMainDisplayID();
      NSMutableArray<NSDictionary *> *displays = [NSMutableArray array];
      for (SCDisplay *display in content.displays) {
        if (display.width <= 0 || display.height <= 0) {
          continue;
        }
        BOOL isPrimary = (BOOL)(display.displayID == primaryDisplayID);
        [displays addObject:@{
          @"id": @(display.displayID),
          @"width": @(display.width),
          @"height": @(display.height),
          @"is_primary": [NSNumber numberWithBool:isPrimary],
        }];
      }
      if (displays.count == 0) {
        AMCPLogDisplayEnumerationClassification("empty_displays");
      }
      if (displays.count > 0) {
        AMCPLogDisplayBooleanRepresentation(displays.firstObject[@"is_primary"]);
      }
      result = AMCPDuplicateJSON(@{ @"displays": displays }, AMCPLogDisplayEnumerationClassification);
    }
    dispatch_semaphore_signal(semaphore);
  }];
  dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
  return result;
}

char *amcp_copy_microphone_sources_json(void) {
  AMCPLogObjCMicrophoneEntry();
  AVCaptureDeviceDiscoverySession *session = [AVCaptureDeviceDiscoverySession
      discoverySessionWithDeviceTypes:@[ AVCaptureDeviceTypeMicrophone ]
                            mediaType:AVMediaTypeAudio
                             position:AVCaptureDevicePositionUnspecified];
  NSString *defaultID = [AVCaptureDevice defaultDeviceWithMediaType:AVMediaTypeAudio].uniqueID;
  NSMutableArray<NSDictionary *> *microphones = [NSMutableArray array];
  for (AVCaptureDevice *device in session.devices) {
    NSString *identifier = [device.uniqueID stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (identifier.length == 0) {
      continue;
    }
    BOOL isDefault = (BOOL)(defaultID != nil && [identifier isEqualToString:defaultID]);
    [microphones addObject:@{
      @"id": identifier,
      @"is_default": [NSNumber numberWithBool:isDefault],
    }];
  }
  if (microphones.count > 0) {
    AMCPLogMicrophoneBooleanRepresentation(microphones.firstObject[@"is_default"]);
  }
  return AMCPDuplicateJSON(@{ @"microphones": microphones }, AMCPLogMicrophoneEnumerationClassification);
}

void amcp_free_capture_sources_json(char *buffer) {
  free(buffer);
}

void *amcp_capture_engine_create(void) {
  AMCPCaptureEngine *engine = [AMCPCaptureEngine new];
  engine.status = AMCP_STATUS_STOPPED;
  return (__bridge_retained void *)engine;
}

void amcp_capture_engine_destroy(void *engine) {
  if (engine == NULL) return;
  AMCPCaptureEngine *captureEngine = (__bridge_transfer AMCPCaptureEngine *)engine;
  [captureEngine.stream stopCaptureWithCompletionHandler:nil];
  captureEngine.callback = NULL;
  captureEngine.callbackContext = NULL;
}

int32_t amcp_capture_engine_start(void *engine, AMCPCaptureConfiguration configuration, AMCPAudioFrameCallback callback, void *context) {
  if (engine == NULL || callback == NULL || (!configuration.include_system_audio && !configuration.include_microphone)) return AMCP_FAILURE;
  AMCPLogCaptureConfiguration(configuration);
  if (!CGPreflightScreenCaptureAccess()) return AMCP_PERMISSION;
  if (configuration.include_microphone && [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] != AVAuthorizationStatusAuthorized) return AMCP_PERMISSION;
  AMCPCaptureEngine *captureEngine = (__bridge AMCPCaptureEngine *)engine;
  if (captureEngine.stream != nil) return AMCP_FAILURE;
  dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
  __block SCShareableContent *content = nil;
  [SCShareableContent getShareableContentExcludingDesktopWindows:NO onScreenWindowsOnly:YES completionHandler:^(SCShareableContent *value, NSError *error) { content = error == nil ? value : nil; dispatch_semaphore_signal(semaphore); }];
  dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
  if (content == nil) {
    AMCPLogCaptureBoundary("shareable_content", "unavailable");
    return AMCP_SOURCE;
  }
  SCDisplay *selected = nil;
  for (SCDisplay *display in content.displays) if (display.displayID == configuration.display_id) { selected = display; break; }
  if (selected == nil) {
    AMCPLogCaptureBoundary("display_selection", "missing");
    return AMCP_SOURCE;
  }
  AMCPLogCaptureBoundary("display_selection", "resolved");
  NSString *microphoneID = configuration.microphone_device_id == NULL ? nil : [NSString stringWithUTF8String:configuration.microphone_device_id];
  if (microphoneID != nil) {
    BOOL found = NO;
    for (AVCaptureDevice *device in [AVCaptureDevice devicesWithMediaType:AVMediaTypeAudio]) if ([device.uniqueID isEqualToString:microphoneID]) { found = YES; break; }
    if (!found) return AMCP_SOURCE;
  }
  SCStreamConfiguration *streamConfiguration = [SCStreamConfiguration new];
  streamConfiguration.capturesAudio = configuration.include_system_audio;
  streamConfiguration.captureMicrophone = configuration.include_microphone;
  streamConfiguration.microphoneCaptureDeviceID = microphoneID;
  streamConfiguration.excludesCurrentProcessAudio = configuration.exclude_current_process_audio;
  SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:selected excludingWindows:@[]];
  captureEngine.stream = [[SCStream alloc] initWithFilter:filter configuration:streamConfiguration delegate:captureEngine];
  AMCPLogCaptureBoundary("stream_configuration_captures_audio", streamConfiguration.capturesAudio ? "true" : "false");
  NSError *error = nil;
  dispatch_queue_t systemQueue = dispatch_queue_create("com.aimeetingcopilot.capture.system", DISPATCH_QUEUE_SERIAL);
  dispatch_queue_t microphoneQueue = dispatch_queue_create("com.aimeetingcopilot.capture.microphone", DISPATCH_QUEUE_SERIAL);
  if (configuration.include_system_audio && ![captureEngine.stream addStreamOutput:captureEngine type:SCStreamOutputTypeAudio sampleHandlerQueue:systemQueue error:&error]) {
    AMCPLogCaptureBoundary("system_output_registration", "failed");
    captureEngine.stream = nil;
    return AMCP_FAILURE;
  }
  if (configuration.include_system_audio) AMCPLogCaptureBoundary("system_output_registration", "succeeded");
  if (configuration.include_microphone && ![captureEngine.stream addStreamOutput:captureEngine type:SCStreamOutputTypeMicrophone sampleHandlerQueue:microphoneQueue error:&error]) {
    AMCPLogCaptureBoundary("microphone_output_registration", "failed");
    captureEngine.stream = nil;
    return AMCP_FAILURE;
  }
  if (configuration.include_microphone) AMCPLogCaptureBoundary("microphone_output_registration", "succeeded");
  captureEngine.callback = callback;
  captureEngine.callbackContext = context;
  captureEngine.acceptingFrames = YES;
  captureEngine.systemOutputCallbackCount = 0;
  captureEngine.systemOutputRejectionReported = NO;
  dispatch_semaphore_t started = dispatch_semaphore_create(0);
  __block NSError *startError = nil;
  [captureEngine.stream startCaptureWithCompletionHandler:^(NSError *value) { startError = value; dispatch_semaphore_signal(started); }];
  dispatch_semaphore_wait(started, DISPATCH_TIME_FOREVER);
  if (startError != nil) {
    AMCPLogCaptureBoundary("stream_start", "failed");
    captureEngine.acceptingFrames = NO;
    captureEngine.callback = NULL;
    captureEngine.callbackContext = NULL;
    captureEngine.stream = nil;
    return AMCP_FAILURE;
  }
  AMCPLogCaptureBoundary("stream_start", "succeeded");
  captureEngine.status = AMCP_STATUS_CAPTURING;
  return AMCP_OK;
}

int32_t amcp_capture_engine_stop(void *engine) {
  if (engine == NULL) return AMCP_FAILURE;
  AMCPCaptureEngine *captureEngine = (__bridge AMCPCaptureEngine *)engine;
  if (captureEngine.stream == nil) { captureEngine.status = AMCP_STATUS_STOPPED; return AMCP_OK; }
  captureEngine.acceptingFrames = NO;
  dispatch_semaphore_t stopped = dispatch_semaphore_create(0);
  [captureEngine.stream stopCaptureWithCompletionHandler:^(__unused NSError *error) { dispatch_semaphore_signal(stopped); }];
  dispatch_semaphore_wait(stopped, DISPATCH_TIME_FOREVER);
  captureEngine.callback = NULL;
  captureEngine.callbackContext = NULL;
  captureEngine.stream = nil;
  captureEngine.status = AMCP_STATUS_STOPPED;
  return AMCP_OK;
}

int32_t amcp_capture_engine_status(void *engine) { return engine == NULL ? AMCP_STATUS_FAILED : ((__bridge AMCPCaptureEngine *)engine).status; }
