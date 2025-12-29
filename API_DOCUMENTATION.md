# WebSocket 接口文档

本文档描述了音频播放器与对讲服务器使用的 WebSocket 协议。

**服务器地址**: `ws://[板卡IP]:8888`

---

## 1. 消息类型 (客户端 -> 服务器)

所有控制消息均作为 JSON 字符串发送。

### 1.1 播放文件
触发 `media/` 目录下的音频文件播放。如果文件以 `.pcm` 结尾，则视为原始 PCM 数据（S16LE, 48k, 单声道）。
```json
{
    "type": "play",
    "file": "example.mp3"
}
```

### 1.2 停止所有播放
停止当前文件播放并清空实时音频队列。
```json
{
    "type": "stop"
}
```

### 1.3 获取媒体文件列表
请求 `media/` 目录下的当前文件列表。
```json
{
    "type": "list"
}
```

### 1.4 删除文件
从 `media/` 目录中删除指定文件。
```json
{
    "type": "delete",
    "file": "example.mp3"
}
```

### 1.5 文件上传 (分片上传)
使用 Base64 编码的分片方式将文件上传到服务器。

**阶段 1: 开始上传**
```json
{
    "type": "upload_start",
    "filename": "new_audio.mp3"
}
```

**阶段 2: 发送分片**(建议每片发256KB，太大websocket会出错)
```json
{
    "type": "upload_chunk",
    "filename": "new_audio.mp3",
    "data": "BASE64_ENCODED_BINARY_DATA"
}
```

**阶段 3: 完成上传**
```json
{
    "type": "upload_end",
    "filename": "new_audio.mp3"
}
```

---

### 1.6 播放原始PCM文件
显式播放原始PCM文件（S16LE, 48k, 单声道）。
```json
{
    "type": "play_pcm",
    "file": "raw_audio.pcm"
}
```

### 1.7 设置音量
设置全局播放音量。
```json
{
    "type": "set_volume",
    "volume": 0.8  // 0.0 到 1.0 之间的浮点数
}
```

### 1.8 暂停/恢复播放
```json
{
    "type": "pause"
}
```
或
```json
{
    "type": "resume"
}
```

---

## 2. 服务器响应 (服务器 -> 客户端)

服务器会针对某些命令发送 JSON 字符串响应。

### 2.1 文件列表更新
在响应 `list`、`delete` 或 `upload_end` 命令时发送。现在包含文件元数据。
```json
{
    "type": "list",
    "files": [
        {
            "name": "abc.mp3",
            "size": 3145728,
            "mtime": 1703649600.0,
            "duration": 120.5
        },
        {
            "name": "alert.wav",
            "size": 48000,
            "mtime": 1703653200.0,
            "duration": 1.5
        }
    ]
}
```

### 2.2 播放结束事件
当文件播放流结束时广播。
```json
{
    "type": "ended"
}
```

---

## 3. 实时音频流 (二进制)

支持发送原始二进制数据用于实时对讲。

*   **格式**: 二进制 (Blob/ArrayBuffer)
*   **数据类型**: Float32 (32位浮点数)
*   **采样率**: 48,000 Hz (服务器首选)
*   **通道**: 单声道 (服务器会自动广播至双声道播放)

**行为**: 当服务器收到类型为 `bytes` 的消息时，会直接将其转换为 NumPy 数组并放入混音器队列进行实时播放。
