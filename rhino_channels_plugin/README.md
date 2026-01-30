Rhino Channels Plugin

This plugin provides the `CaptureRenderChannels` command for writing linear depth/normal PFM files.

Build
1) Set `RhinoCommonPath` in `rhino_channels_plugin/RhinoChannelsPlugin.csproj` to your RhinoCommon.dll.
   - Rhino 7 default: C:\Program Files\Rhino 7\System\RhinoCommon.dll
   - Rhino 8 default: C:\Program Files\Rhino 8\System\RhinoCommon.dll
2) Build the project (Visual Studio or `dotnet build`).
3) Install the resulting .rhp into Rhino.

Usage (from Python)
-Command form in Rhino:
  -CaptureRenderChannels _View="Perspective" _DepthPath="C:\\path\\depth.pfm" _NormalPath="C:\\path\\normal.pfm" _Width=1024 _Height=1024 _Enter

The Python pipeline calls this command from `utils_loc/outputs.py`.
