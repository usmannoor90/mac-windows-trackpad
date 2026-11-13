#include "CMultitouch.h"
#include <dlfcn.h>

typedef CFArrayRef (*CreateList_t)(void);
typedef void (*Register_t)(MTDeviceRef, MTContactCallback);
typedef void (*Start_t)(MTDeviceRef, int);
typedef void (*Stop_t)(MTDeviceRef);
typedef void (*FamilyID_t)(MTDeviceRef, int *);
typedef bool (*IsBuiltIn_t)(MTDeviceRef);

static CreateList_t pCreateList;
static Register_t pRegister;
static Register_t pUnregister;
static Start_t pStart;
static Stop_t pStop;
static FamilyID_t pFamilyID;
static IsBuiltIn_t pIsBuiltIn;

bool MTLoad(void) {
    static void *handle;
    if (handle) return true;
    handle = dlopen(
        "/System/Library/PrivateFrameworks/MultitouchSupport.framework/MultitouchSupport",
        RTLD_NOW);
    if (!handle) return false;
    pCreateList = (CreateList_t)dlsym(handle, "MTDeviceCreateList");
    pRegister = (Register_t)dlsym(handle, "MTRegisterContactFrameCallback");
    pUnregister = (Register_t)dlsym(handle, "MTUnregisterContactFrameCallback");
    pStart = (Start_t)dlsym(handle, "MTDeviceStart");
    pStop = (Stop_t)dlsym(handle, "MTDeviceStop");
    pFamilyID = (FamilyID_t)dlsym(handle, "MTDeviceGetFamilyID");
    pIsBuiltIn = (IsBuiltIn_t)dlsym(handle, "MTDeviceIsBuiltIn");
    return pCreateList && pRegister && pUnregister && pStart && pStop &&
           pFamilyID && pIsBuiltIn;
}

CFArrayRef MTCreateDeviceList(void) { return pCreateList ? pCreateList() : NULL; }
void MTRegisterCallback(MTDeviceRef d, MTContactCallback cb) { pRegister(d, cb); }
void MTUnregisterCallback(MTDeviceRef d, MTContactCallback cb) { pUnregister(d, cb); }
void MTStart(MTDeviceRef d) { pStart(d, 0); }
void MTStop(MTDeviceRef d) { pStop(d); }

int MTFamilyID(MTDeviceRef d) {
    int family = 0;
    pFamilyID(d, &family);
    return family;
}

bool MTIsBuiltIn(MTDeviceRef d) { return pIsBuiltIn(d); }
