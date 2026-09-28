# Windows 绿色版打包

源码仓库不包含模型权重、CUDA 运行库或 FFmpeg 二进制，以避免 Git 仓库膨胀和第三方许可混淆。

## 推荐目录

```text
LyricsVideoMakerStudio/
├─ LyricsVideoMakerStudio.exe
├─ _internal/
├─ ffmpeg/
│  ├─ ffmpeg.exe
│  └─ ffprobe.exe
├─ models/
│  ├─ small/
│  └─ large-v3-turbo/
├─ cuda/                  # 可选，仅 NVIDIA GPU 版本
└─ outputs/
```

## PyInstaller

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name LyricsVideoMakerStudio app_qt.py
```

完成后，把 FFmpeg 和已下载的 CTranslate2 模型放到上述相对目录。GPU 版还需要与 `ctranslate2` 版本兼容的 NVIDIA 驱动和 CUDA 运行库。

发布第三方二进制前，请分别核对 FFmpeg、Whisper 模型、CUDA 与相关 Python 包的许可条款。
