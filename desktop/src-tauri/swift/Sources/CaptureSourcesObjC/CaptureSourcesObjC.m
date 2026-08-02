#import "CaptureSourcesObjC.h"

#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>

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
