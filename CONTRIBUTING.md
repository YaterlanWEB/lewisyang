# 参与贡献

感谢关注 Lyrics Video Maker Studio。

## 提交问题

请说明 Windows 版本、CPU/GPU 模式、所选模型、问题发生步骤和完整错误信息。不要上传无权分享的歌曲、歌词、图片、个人路径或账号凭据；可以使用几秒钟的自制测试音频和虚构歌词复现。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
python app_qt.py
```

## Pull Request

- 一次提交聚焦一个问题。
- 行为变化需要增加或更新测试。
- 不要提交模型、媒体文件、构建目录和二进制依赖。
- 涉及识别精度时，请说明测试条件和评价方式，不要只提供单个主观样例。
