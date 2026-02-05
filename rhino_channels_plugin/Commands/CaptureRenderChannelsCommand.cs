using System;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text;
using Rhino;
using Rhino.Commands;
using Rhino.Display;
using Rhino.Geometry;
using Rhino.Input;

namespace RhinoChannelsPlugin.Commands
{
    public sealed class CaptureRenderChannelsCommand : Command
    {
        public override string EnglishName => "CaptureRenderChannels";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            RhinoApp.WriteLine("CaptureRenderChannels: command started.");

            var depthPath = string.Empty;
            var normalPath = string.Empty;
            var viewName = string.Empty;
            var widthToken = string.Empty;
            var heightToken = string.Empty;
            var rendererToken = string.Empty;

            var result = RhinoGet.GetString("DepthPath", false, ref depthPath);
            if (result != Result.Success)
                return result;

            result = RhinoGet.GetString("NormalPath", false, ref normalPath);
            if (result != Result.Success)
                return result;

            result = RhinoGet.GetString("ViewName (Enter for active view)", true, ref viewName);
            if (result == Result.Cancel)
                return result;

            result = RhinoGet.GetString("Width (0 = viewport width)", true, ref widthToken);
            if (result == Result.Cancel)
                return result;

            result = RhinoGet.GetString("Height (0 = viewport height)", true, ref heightToken);
            if (result == Result.Cancel)
                return result;

            // Kept for backward compatibility with existing command calls.
            result = RhinoGet.GetString("RendererId (unused for viewport capture)", true, ref rendererToken);
            if (result == Result.Cancel)
                return result;

            try
            {
                var width = ParseOptionalPositiveInt(widthToken, "Width");
                var height = ParseOptionalPositiveInt(heightToken, "Height");

                var view = string.IsNullOrWhiteSpace(viewName)
                    ? doc.Views.ActiveView
                    : doc.Views.Find(viewName, false);
                if (view == null)
                    throw new InvalidOperationException("No matching view found.");

                if (!string.IsNullOrWhiteSpace(rendererToken))
                    RhinoApp.WriteLine("CaptureRenderChannels: RendererId is ignored in viewport capture mode.");

                view.Redraw();

                var srcSize = view.ActiveViewport.Size;
                if (srcSize.Width <= 0 || srcSize.Height <= 0)
                    throw new InvalidOperationException("Viewport size is invalid.");

                var outWidth = width > 0 ? width : srcSize.Width;
                var outHeight = height > 0 ? height : srcSize.Height;
                if (outWidth <= 0 || outHeight <= 0)
                    throw new InvalidOperationException("Invalid output dimensions.");

                RhinoApp.WriteLine(
                    $"CaptureRenderChannels: using view '{view.ActiveViewport.Name}', source={srcSize.Width}x{srcSize.Height}, output={outWidth}x{outHeight}.");

                using var zcap = new ZBufferCapture(view.ActiveViewport);
                zcap.ShowIsocurves(false);
                zcap.ShowMeshWires(false);
                zcap.ShowCurves(false);
                zcap.ShowPoints(false);
                zcap.ShowText(false);
                zcap.ShowAnnotations(false);
                zcap.ShowLights(false);

                var worldPoints = new Point3d[srcSize.Width * srcSize.Height];
                var validMask = new bool[srcSize.Width * srcSize.Height];
                CaptureWorldPoints(zcap, srcSize.Width, srcSize.Height, worldPoints, validMask);

                var depth = BuildDepthBuffer(
                    worldPoints,
                    validMask,
                    srcSize.Width,
                    srcSize.Height,
                    outWidth,
                    outHeight,
                    view.ActiveViewport.CameraLocation);

                var sourceNormals = BuildSourceNormalBuffer(
                    worldPoints,
                    validMask,
                    srcSize.Width,
                    srcSize.Height,
                    view.ActiveViewport.CameraDirection);

                var normal = ResampleVectorBuffer(
                    sourceNormals,
                    srcSize.Width,
                    srcSize.Height,
                    outWidth,
                    outHeight,
                    3);

                LogMinMax("Depth", depth);
                LogMinMax("Normal", normal);

                WritePfm(depthPath, outWidth, outHeight, 1, depth);
                WritePfm(normalPath, outWidth, outHeight, 3, normal);

                RhinoApp.WriteLine($"CaptureRenderChannels: wrote '{depthPath}' and '{normalPath}'.");
                return Result.Success;
            }
            catch (Exception ex)
            {
                RhinoApp.WriteLine($"CaptureRenderChannels: ERROR {ex.GetType().Name}: {ex.Message}");
                return Result.Failure;
            }
        }

        private static int ParseOptionalPositiveInt(string token, string label)
        {
            if (string.IsNullOrWhiteSpace(token))
                return 0;
            if (!int.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
                throw new InvalidOperationException($"{label} '{token}' is not an integer.");
            if (value < 0)
                throw new InvalidOperationException($"{label} must be >= 0.");
            return value;
        }

        private static void CaptureWorldPoints(
            ZBufferCapture capture,
            int width,
            int height,
            Point3d[] worldPoints,
            bool[] validMask)
        {
            for (var y = 0; y < height; y++)
            {
                for (var x = 0; x < width; x++)
                {
                    var idx = (y * width) + x;
                    var p = capture.WorldPointAt(x, y);
                    worldPoints[idx] = p;
                    validMask[idx] = p.IsValid;
                }
            }
        }

        private static float[] BuildDepthBuffer(
            Point3d[] worldPoints,
            bool[] validMask,
            int srcWidth,
            int srcHeight,
            int outWidth,
            int outHeight,
            Point3d cameraLocation)
        {
            var data = new float[outWidth * outHeight];
            for (var y = 0; y < outHeight; y++)
            {
                var sy = MapCoord(y, outHeight, srcHeight);
                for (var x = 0; x < outWidth; x++)
                {
                    var sx = MapCoord(x, outWidth, srcWidth);
                    var sidx = (sy * srcWidth) + sx;
                    if (validMask[sidx])
                    {
                        data[(y * outWidth) + x] = (float)cameraLocation.DistanceTo(worldPoints[sidx]);
                    }
                    else
                    {
                        data[(y * outWidth) + x] = 0f;
                    }
                }
            }
            return data;
        }

        private static float[] BuildSourceNormalBuffer(
            Point3d[] worldPoints,
            bool[] validMask,
            int width,
            int height,
            Vector3d cameraDirection)
        {
            var data = new float[width * height * 3];
            if (!cameraDirection.Unitize())
                cameraDirection = new Vector3d(0, 0, -1);

            for (var y = 0; y < height; y++)
            {
                for (var x = 0; x < width; x++)
                {
                    var idx = (y * width) + x;
                    if (!validMask[idx])
                        continue;

                    var p = worldPoints[idx];
                    if (!TryComputeTangent(worldPoints, validMask, width, height, x, y, true, p, out var tx))
                        continue;
                    if (!TryComputeTangent(worldPoints, validMask, width, height, x, y, false, p, out var ty))
                        continue;

                    var n = Vector3d.CrossProduct(tx, ty);
                    if (!n.Unitize())
                        continue;

                    // Keep normals roughly oriented towards the camera for stable sign.
                    if (n * cameraDirection > 0.0)
                        n = -n;

                    var baseIndex = idx * 3;
                    data[baseIndex] = (float)n.X;
                    data[baseIndex + 1] = (float)n.Y;
                    data[baseIndex + 2] = (float)n.Z;
                }
            }

            return data;
        }

        private static bool TryComputeTangent(
            Point3d[] points,
            bool[] valid,
            int width,
            int height,
            int x,
            int y,
            bool alongX,
            Point3d center,
            out Vector3d tangent)
        {
            tangent = Vector3d.Unset;

            var x0 = alongX ? x - 1 : x;
            var y0 = alongX ? y : y - 1;
            var x1 = alongX ? x + 1 : x;
            var y1 = alongX ? y : y + 1;

            var has0 = TryGetPoint(points, valid, width, height, x0, y0, out var p0);
            var has1 = TryGetPoint(points, valid, width, height, x1, y1, out var p1);

            if (has0 && has1)
            {
                tangent = p1 - p0;
                return tangent.IsValid && tangent.SquareLength > 0.0;
            }

            if (has1)
            {
                tangent = p1 - center;
                return tangent.IsValid && tangent.SquareLength > 0.0;
            }

            if (has0)
            {
                tangent = center - p0;
                return tangent.IsValid && tangent.SquareLength > 0.0;
            }

            return false;
        }

        private static bool TryGetPoint(
            Point3d[] points,
            bool[] valid,
            int width,
            int height,
            int x,
            int y,
            out Point3d point)
        {
            point = Point3d.Unset;
            if (x < 0 || x >= width || y < 0 || y >= height)
                return false;
            var idx = (y * width) + x;
            if (!valid[idx])
                return false;
            point = points[idx];
            return true;
        }

        private static float[] ResampleVectorBuffer(
            float[] source,
            int srcWidth,
            int srcHeight,
            int outWidth,
            int outHeight,
            int components)
        {
            if (srcWidth == outWidth && srcHeight == outHeight)
                return source;

            var output = new float[outWidth * outHeight * components];
            for (var y = 0; y < outHeight; y++)
            {
                var sy = MapCoord(y, outHeight, srcHeight);
                for (var x = 0; x < outWidth; x++)
                {
                    var sx = MapCoord(x, outWidth, srcWidth);
                    var sBase = ((sy * srcWidth) + sx) * components;
                    var oBase = ((y * outWidth) + x) * components;
                    for (var c = 0; c < components; c++)
                        output[oBase + c] = source[sBase + c];
                }
            }
            return output;
        }

        private static int MapCoord(int coord, int outSize, int srcSize)
        {
            if (srcSize <= 1)
                return 0;
            var t = (coord + 0.5) / outSize;
            var mapped = (int)Math.Floor(t * srcSize);
            if (mapped < 0)
                return 0;
            if (mapped >= srcSize)
                return srcSize - 1;
            return mapped;
        }

        private static void WritePfm(string path, int width, int height, int channels, float[] data)
        {
            var dir = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(dir))
                dir = ".";
            Directory.CreateDirectory(dir);

            var header = channels == 3
                ? string.Format(CultureInfo.InvariantCulture, "PF\n{0} {1}\n-1.0\n", width, height)
                : string.Format(CultureInfo.InvariantCulture, "Pf\n{0} {1}\n-1.0\n", width, height);

            using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read);
            using var bw = new BinaryWriter(fs, Encoding.ASCII, false);
            bw.Write(Encoding.ASCII.GetBytes(header));
            for (var i = 0; i < data.Length; i++)
                bw.Write(data[i]);
        }

        private static void LogMinMax(string label, float[] data)
        {
            if (data.Length == 0)
            {
                RhinoApp.WriteLine($"CaptureRenderChannels: {label} channel empty.");
                return;
            }

            float min = float.PositiveInfinity;
            float max = float.NegativeInfinity;
            var nanCount = 0;
            for (var i = 0; i < data.Length; i++)
            {
                var v = data[i];
                if (float.IsNaN(v))
                {
                    nanCount++;
                    continue;
                }
                if (v < min) min = v;
                if (v > max) max = v;
            }
            RhinoApp.WriteLine($"CaptureRenderChannels: {label} min={min}, max={max}, NaN={nanCount}");
        }
    }
}
