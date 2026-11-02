#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <AppKit/AppKit.h>

// 本地 OCR：macOS Vision，输出每个文本框的 text / conf / 像素坐标。
// 只负责"看见"，不做表格重建；重建与判断在 Python 侧完成。
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) { fprintf(stderr, "usage: vision_ocr <image> [lang]\n"); return 2; }
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSString *lang = argc >= 3 ? [NSString stringWithUTF8String:argv[2]] : @"zh-Hans";
        NSMutableArray<NSString*> *custom = [NSMutableArray array];
        if (argc >= 4) {
            NSString *wpath = [NSString stringWithUTF8String:argv[3]];
            NSString *wtxt = [NSString stringWithContentsOfFile:wpath encoding:NSUTF8StringEncoding error:nil];
            for (NSString *w in [wtxt componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
                NSString *t = [w stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
                if (t.length > 0) [custom addObject:t];
            }
        }
        NSImage *img = [[NSImage alloc] initWithContentsOfFile:path];
        if (!img) { fprintf(stderr, "cannot load image: %s\n", argv[1]); return 1; }
        CGImageRef cg = [img CGImageForProposedRect:NULL context:nil hints:nil];
        if (!cg) { fprintf(stderr, "cannot get CGImage\n"); return 1; }
        size_t W = CGImageGetWidth(cg), H = CGImageGetHeight(cg);
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:cg options:@{}];
        VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] init];
        req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        req.recognitionLanguages = @[lang, @"en-US"];
        req.usesLanguageCorrection = YES;
        req.minimumTextHeight = 0.004;
        if (custom.count > 0) req.customWords = custom;
        NSError *err = nil;
        [handler performRequests:@[req] error:&err];
        if (err) { fprintf(stderr, "ocr failed: %s\n", err.description.UTF8String); return 1; }
        NSMutableArray *out = [NSMutableArray array];
        for (VNRecognizedTextObservation *obs in req.results) {
            VNRecognizedText *c = [[obs topCandidates:1] firstObject];
            if (!c) continue;
            CGRect b = obs.boundingBox;
            double x = b.origin.x * W;
            double y = (1.0 - b.origin.y - b.size.height) * H;
            double w = b.size.width * W;
            double h = b.size.height * H;
            [out addObject:@{
                @"text": c.string ?: @"",
                @"conf": @(c.confidence),
                @"x": @(x), @"y": @(y), @"w": @(w), @"h": @(h),
                @"cx": @(x + w/2.0), @"cy": @(y + h/2.0)
            }];
        }
        NSData *data = [NSJSONSerialization dataWithJSONObject:out
                                                       options:NSJSONWritingPrettyPrinted
                                                         error:nil];
        fwrite(data.bytes, 1, data.length, stdout);
        printf("\n");
    }
    return 0;
}
