#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

char * _Nullable amcp_copy_display_sources_json(void);
char * _Nullable amcp_copy_microphone_sources_json(void);
void amcp_free_capture_sources_json(char * _Nullable buffer);

NS_ASSUME_NONNULL_END
