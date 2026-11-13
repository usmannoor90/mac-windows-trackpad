#ifndef CMULTITOUCH_H
#define CMULTITOUCH_H

#include <CoreFoundation/CoreFoundation.h>
#include <stdbool.h>

// Finger record delivered by MultitouchSupport on every trackpad frame.
// Layout matches the ctypes struct used by the original Python script.
typedef struct {
    float x, y;
} MTPoint;

typedef struct {
    MTPoint position, velocity;
} MTVector;

typedef struct {
    int frame;
    double timestamp;
    int identifier;
    int state;
    int fingerId;
    int handId;
    MTVector normalized;  // 0.0-1.0 position on the glass
    float size;
    int zero1;
    float angle;
    float majorAxis;
    float minorAxis;
    MTVector mm;
    int zero2[2];
    float density;
} MTFinger;

typedef void *MTDeviceRef;
typedef int (*MTContactCallback)(MTDeviceRef device, MTFinger *fingers,
                                 int count, double timestamp, int frame);

// Loads the private framework. Returns false if any symbol is missing.
bool MTLoad(void);

// Wrappers around the framework (valid after MTLoad returned true).
CF_RETURNS_RETAINED CFArrayRef MTCreateDeviceList(void);
void MTRegisterCallback(MTDeviceRef device, MTContactCallback callback);
void MTUnregisterCallback(MTDeviceRef device, MTContactCallback callback);
void MTStart(MTDeviceRef device);
void MTStop(MTDeviceRef device);
int MTFamilyID(MTDeviceRef device);
bool MTIsBuiltIn(MTDeviceRef device);

#endif
