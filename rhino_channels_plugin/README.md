Rhino Channels Plugin

This plugin provides the `CaptureRenderChannels` command for writing linear depth/normal PFM files.

Build
1) Set `RhinoCommonPath` in `rhino_channels_plugin/RhinoChannelsPlugin.csproj` to your RhinoCommon.dll.
   - Rhino 7 default: C:\Program Files\Rhino 7\System\RhinoCommon.dll
   - Rhino 8 default: C:\Program Files\Rhino 8\System\RhinoCommon.dll
2) Build the project (Visual Studio or `dotnet build`).
3) Install the plugin into Rhino (both `.dll` and `.rhp` work).
   - The build outputs `RhinoChannelsPlugin.dll` in `rhino_channels_plugin\bin\{Debug|Release}\net7.0-windows\`.
   - You can drag the `.dll` into Rhino or use Tools → Options → Plug-ins → Install.
   - (Optional) Rename/copy the `.dll` to `.rhp` if you prefer the Rhino convention.

Usage (from Python)
-Command form in Rhino:
  -CaptureRenderChannels "C:\\path\\depth.pfm" "C:\\path\\normal.pfm" "Perspective" 1920 1080 _Enter

Arguments are prompted in this order:
1) `DepthPath` (required)
2) `NormalPath` (required)
3) `ViewName` (optional; press Enter for active view)
4) `Width` (optional; `0` uses viewport width)
5) `Height` (optional; `0` uses viewport height)
6) `RendererId` (accepted for compatibility but ignored in current viewport-capture implementation)

Notes
- This command captures from the current viewport using `ZBufferCapture` and world-point-derived normals.
- It does not invoke the offline render engine, so it should return quickly and avoid long `Processing geometry table` waits.

The Python pipeline calls this command from `utils_loc/outputs.py`.
