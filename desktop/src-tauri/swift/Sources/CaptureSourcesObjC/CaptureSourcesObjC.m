#import "CaptureSourcesObjC.h"

#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <CoreMedia/CoreMedia.h>

enum { AMCP_STATUS_STOPPED = 0, AMCP_STATUS_CAPTURING = 1, AMCP_STATUS_FAILED = 2 };
enum { AMCP_OK = 0, AMCP_FAILURE = 1, AMCP_PERMISSION = 2, AMCP_SOURCE = 3 };

@interface AMCPCaptureEngine : NSObject <SCStreamOutput, SCStreamDelegate>
@property(nonatomic, strong) SCStream *stream;
@property(nonatomic) AMCPAudioFrameCallback callback;
@property(nonatomic) void *callbackContext;
@property(nonatomic) BOOL acceptingFrames;
@property(nonatomic) int32_t status;
@end

@implementation AMCPCaptureEngine

- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer ofType:(SCStreamOutputType)type {
  if (!self.acceptingFrames || self.callback == NULL || !CMSampleBufferDataIsReady(sampleBuffer)) return;
  if (type != SCStreamOutputTypeAudio && type != SCStreamOutputTypeMicrophone) return;
  CMAudioFormatDescriptionRef description = CMSampleBufferGetFormatDescription(sampleBuffer);
  const AudioStreamBasicDescription *format = CMAudioFormatDescriptionGetStreamBasicDescription(description);
  if (format == NULL || format->mChannelsPerFrame == 0) return;
  uint8_t sampleFormat = 0;
  if ((format->mFormatFlags & kAudioFormatFlagIsFloat) && format->mBitsPerChannel == 32) sampleFormat = 1;
  if ((format->mFormatFlags & kAudioFormatFlagIsSignedInteger) && format->mBitsPerChannel == 16) sampleFormat = 2;
  if (sampleFormat == 0) return;
  AudioBufferList bufferList;
  CMBlockBufferRef blockBuffer = NULL;
  if (CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(sampleBuffer, NULL, &bufferList, sizeof(bufferList), NULL, NULL, 0, &blockBuffer) != noErr) return;
  size_t bytes = 0;
  for (UInt32 index = 0; index < bufferList.mNumberBuffers; index++) bytes += bufferList.mBuffers[index].mDataByteSize;
  size_t width = sampleFormat == 1 ? sizeof(float) : sizeof(int16_t);
  if (bytes == 0 || bytes % width != 0) { if (blockBuffer) CFRelease(blockBuffer); return; }
  NSMutableData *samples = [NSMutableData dataWithLength:bytes];
  uint8_t *destination = samples.mutableBytes;
  for (UInt32 index = 0; index < bufferList.mNumberBuffers; index++) {
    AudioBuffer buffer = bufferList.mBuffers[index];
    memcpy(destination, buffer.mData, buffer.mDataByteSize);
    destination += buffer.mDataByteSize;
  }
  CMClockRef clock = stream.synchronizationClock;
  double timestamp = clock ? CMTimeGetSeconds(CMClockGetTime(clock)) : CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer));
  if (!isfinite(timestamp) || timestamp < 0) { if (blockBuffer) CFRelease(blockBuffer); return; }
  self.callback(self.callbackContext, type == SCStreamOutputTypeAudio ? 1 : 2,
                (uint32_t)format->mSampleRate, (uint16_t)format->mChannelsPerFrame, sampleFormat,
                (format->mFormatFlags & kAudioFormatFlagIsNonInterleaved) == 0, timestamp,
                samples.bytes, bytes / width);
  if (blockBuffer) CFRelease(blockBuffer);
}

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error {
  self.acceptingFrames = NO;
  self.status = AMCP_STATUS_FAILED;
}
@end

static char *AMCPDuplicateJSON(id object) {
  NSError *error = nil;
  NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:&error];
  if (error != nil || data == nil) {
    return NULL;
  }
  NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
  return json == nil ? NULL : strdup(json.UTF8String);
}

char *amcp_copy_display_sources_json(void) {
  dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
  __block char *result = NULL;

  [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                             onScreenWindowsOnly:YES
                                               completionHandler:^(SCShareableContent *content, NSError *error) {
    if (error == nil && content != nil) {
      CGDirectDisplayID primaryDisplayID = CGMainDisplayID();
      NSMutableArray<NSDictionary *> *displays = [NSMutableArray array];
      for (SCDisplay *display in content.displays) {
        if (display.width <= 0 || display.height <= 0) {
          continue;
        }
        [displays addObject:@{
          @"id": @(display.displayID),
          @"width": @(display.width),
          @"height": @(display.height),
          @"is_primary": @(display.displayID == primaryDisplayID),
        }];
      }
      result = AMCPDuplicateJSON(@{ @"displays": displays });
    }
    dispatch_semaphore_signal(semaphore);
  }];
  dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
  return result;
}

char *amcp_copy_microphone_sources_json(void) {
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
    [microphones addObject:@{
      @"id": identifier,
      @"is_default": @(defaultID != nil && [identifier isEqualToString:defaultID]),
    }];
  }
  return AMCPDuplicateJSON(@{ @"microphones": microphones });
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
  if (!CGPreflightScreenCaptureAccess()) return AMCP_PERMISSION;
  if (configuration.include_microphone && [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] != AVAuthorizationStatusAuthorized) return AMCP_PERMISSION;
  AMCPCaptureEngine *captureEngine = (__bridge AMCPCaptureEngine *)engine;
  if (captureEngine.stream != nil) return AMCP_FAILURE;
  dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
  __block SCShareableContent *content = nil;
  [SCShareableContent getShareableContentExcludingDesktopWindows:NO onScreenWindowsOnly:YES completionHandler:^(SCShareableContent *value, NSError *error) { content = error == nil ? value : nil; dispatch_semaphore_signal(semaphore); }];
  dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
  SCDisplay *selected = nil;
  for (SCDisplay *display in content.displays) if (display.displayID == configuration.display_id) { selected = display; break; }
  if (selected == nil) return AMCP_SOURCE;
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
  NSError *error = nil;
  dispatch_queue_t systemQueue = dispatch_queue_create("com.aimeetingcopilot.capture.system", DISPATCH_QUEUE_SERIAL);
  dispatch_queue_t microphoneQueue = dispatch_queue_create("com.aimeetingcopilot.capture.microphone", DISPATCH_QUEUE_SERIAL);
  if ((configuration.include_system_audio && ![captureEngine.stream addStreamOutput:captureEngine type:SCStreamOutputTypeAudio sampleHandlerQueue:systemQueue error:&error]) ||
      (configuration.include_microphone && ![captureEngine.stream addStreamOutput:captureEngine type:SCStreamOutputTypeMicrophone sampleHandlerQueue:microphoneQueue error:&error])) { captureEngine.stream = nil; return AMCP_FAILURE; }
  captureEngine.callback = callback;
  captureEngine.callbackContext = context;
  captureEngine.acceptingFrames = YES;
  dispatch_semaphore_t started = dispatch_semaphore_create(0);
  __block NSError *startError = nil;
  [captureEngine.stream startCaptureWithCompletionHandler:^(NSError *value) { startError = value; dispatch_semaphore_signal(started); }];
  dispatch_semaphore_wait(started, DISPATCH_TIME_FOREVER);
  if (startError != nil) { captureEngine.acceptingFrames = NO; captureEngine.callback = NULL; captureEngine.callbackContext = NULL; captureEngine.stream = nil; return AMCP_FAILURE; }
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
