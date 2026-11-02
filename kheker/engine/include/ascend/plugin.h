#pragma once

// Ascend C ABI v1：动态模块插件边界。
//
// 插件只导出 ascend_plugin_callbacks；宿主通过 C 回调读取公开量、调用方法并
// 捕获／恢复状态。声明、配置与状态均使用稳定类型；不要让 std::string、
// std::any、std::function 或异常穿过此边界。
//
// 首版边界：一个插件库只提供一个平坦模块，不支持需求、子模块、转接与导出；
// 公开类型为 void、bool、int64、double、string，方法最多三个参数。
//
// 字符串生命周期：描述、定义、实现、版本、平台、错误与返回值中的字符串由插件
// 持有；宿主必须在同一实例的下一次回调或插件卸载前复制。create 完成后 state
// 由宿主持有，destroy 负责释放。

#include <stddef.h>
#include <stdint.h>

#define ASCEND_PLUGIN_ABI_VERSION 1
#define ASCEND_PLUGIN_ENTRY_SYMBOL "ascend_plugin_callbacks"
#define ASCEND_PLUGIN_BINARY_FORMAT "shared-library"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum AscendPluginType {
    ASCEND_PLUGIN_TYPE_VOID = 0,
    ASCEND_PLUGIN_TYPE_BOOL = 1,
    ASCEND_PLUGIN_TYPE_INT64 = 2,
    ASCEND_PLUGIN_TYPE_DOUBLE = 3,
    ASCEND_PLUGIN_TYPE_STRING = 4
} AscendPluginType;

typedef enum AscendPluginKind {
    ASCEND_PLUGIN_KIND_VALUE = 0,
    ASCEND_PLUGIN_KIND_METHOD = 1
} AscendPluginKind;

typedef enum AscendPluginStatus {
    ASCEND_PLUGIN_OK = 0,
    ASCEND_PLUGIN_ERROR = 1
} AscendPluginStatus;

typedef struct AscendPluginString {
    const char* data;
    size_t size;
} AscendPluginString;

typedef struct AscendPluginValue {
    AscendPluginType type;
    union {
        int32_t boolean;
        int64_t integer;
        double number;
        AscendPluginString string;
    } data;
} AscendPluginValue;

typedef struct AscendPluginParameter {
    AscendPluginString name;
    AscendPluginType type;
} AscendPluginParameter;

typedef struct AscendPluginDeclaration {
    AscendPluginString symbol;
    AscendPluginKind kind;
    AscendPluginType result_type;
    const AscendPluginParameter* parameters;
    size_t parameter_count;
    // description_domain 为空时 description_key 作为原文；否则按文本键解析。
    AscendPluginString description_domain;
    AscendPluginString description_key;
    AscendPluginString description_fallback;
    AscendPluginString contract;
} AscendPluginDeclaration;

typedef struct AscendPluginError {
    int32_t code;
    AscendPluginString message;
} AscendPluginError;

typedef int32_t (*AscendPluginCreateFn)(AscendPluginString instance,
                                         AscendPluginString config_json,
                                         void** state,
                                         AscendPluginError* error);
typedef void (*AscendPluginDestroyFn)(void* state);
typedef int32_t (*AscendPluginReadFn)(void* state,
                                      AscendPluginString symbol,
                                      AscendPluginValue* result,
                                      AscendPluginError* error);
typedef int32_t (*AscendPluginCallFn)(void* state,
                                      AscendPluginString symbol,
                                      const AscendPluginValue* arguments,
                                      size_t argument_count,
                                      AscendPluginValue* result,
                                      AscendPluginError* error);
typedef int32_t (*AscendPluginCaptureFn)(void* state,
                                         AscendPluginString* state_json,
                                         AscendPluginError* error);
typedef int32_t (*AscendPluginRestoreFn)(void* state,
                                         AscendPluginString state_json,
                                         AscendPluginError* error);

typedef struct AscendPluginCallbacks {
    uint32_t abi_version;
    AscendPluginString platform;
    AscendPluginString definition;
    AscendPluginString implementation;
    AscendPluginString version;
    uint32_t stateless;
    AscendPluginString state_contract;
    AscendPluginString default_config_json;
    const AscendPluginDeclaration* declarations;
    size_t declaration_count;
    AscendPluginCreateFn create;
    AscendPluginDestroyFn destroy;
    AscendPluginReadFn read;
    AscendPluginCallFn call;
    AscendPluginCaptureFn capture;
    AscendPluginRestoreFn restore;
} AscendPluginCallbacks;

#if defined(_WIN32)
#define ASCEND_PLUGIN_EXPORT __declspec(dllexport)
#else
#define ASCEND_PLUGIN_EXPORT __attribute__((visibility("default")))
#endif

// 每个插件库只导出一个描述表；返回的指针在插件生命周期内有效。
ASCEND_PLUGIN_EXPORT const AscendPluginCallbacks* ascend_plugin_callbacks(void);

#ifdef __cplusplus
}  // extern "C"
#endif
