using System;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Compression;
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
        private static readonly bool VerboseLogging = false;

        private static class ReusableBuffers
        {
            public static Point3d[] WorldPoints = Array.Empty<Point3d>();
            public static bool[] ValidMask = Array.Empty<bool>();
            public static float[] SourceNormals = Array.Empty<float>();
            public static float[] Depth = Array.Empty<float>();
            public static float[] Normal = Array.Empty<float>();

            public static void EnsureSizes(int srcPixelCount, int outPixelCount)
            {
                if (WorldPoints.Length != srcPixelCount)
                    WorldPoints = new Point3d[srcPixelCount];
                if (ValidMask.Length != srcPixelCount)
                    ValidMask = new bool[srcPixelCount];
                if (SourceNormals.Length != srcPixelCount * 3)
                    SourceNormals = new float[srcPixelCount * 3];
                if (Depth.Length != outPixelCount)
                    Depth = new float[outPixelCount];
                if (Normal.Length != outPixelCount * 3)
                    Normal = new float[outPixelCount * 3];
            }
        }

        public override string EnglishName => "CaptureRenderChannels";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
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
                    LogVerbose("CaptureRenderChannels: RendererId is ignored in viewport capture mode.");

                view.Redraw();

                var srcSize = view.ActiveViewport.Size;
                if (srcSize.Width <= 0 || srcSize.Height <= 0)
                    throw new InvalidOperationException("Viewport size is invalid.");

                var outWidth = width > 0 ? width : srcSize.Width;
                var outHeight = height > 0 ? height : srcSize.Height;
                if (outWidth <= 0 || outHeight <= 0)
                    throw new InvalidOperationException("Invalid output dimensions.");

                LogVerbose(
                    $"CaptureRenderChannels: using view '{view.ActiveViewport.Name}', source={srcSize.Width}x{srcSize.Height}, output={outWidth}x{outHeight}.");

                var srcPixelCount = srcSize.Width * srcSize.Height;
                var outPixelCount = outWidth * outHeight;
                ReusableBuffers.EnsureSizes(srcPixelCount, outPixelCount);

                using var zcap = new ZBufferCapture(view.ActiveViewport);
                zcap.ShowIsocurves(false);
                zcap.ShowMeshWires(false);
                zcap.ShowCurves(false);
                zcap.ShowPoints(false);
                zcap.ShowText(false);
                zcap.ShowAnnotations(false);
                zcap.ShowLights(false);

                var worldPoints = ReusableBuffers.WorldPoints;
                var validMask = ReusableBuffers.ValidMask;
                var sourceNormals = ReusableBuffers.SourceNormals;
                var depth = ReusableBuffers.Depth;
                var normal = ReusableBuffers.Normal;

                CaptureWorldPoints(zcap, srcSize.Width, srcSize.Height, worldPoints, validMask);

                FillDepthBuffer(
                    worldPoints,
                    validMask,
                    srcSize.Width,
                    srcSize.Height,
                    outWidth,
                    outHeight,
                    view.ActiveViewport.CameraLocation,
                    view.ActiveViewport.CameraDirection,
                    depth);

                FillSourceNormalBuffer(
                    worldPoints,
                    validMask,
                    srcSize.Width,
                    srcSize.Height,
                    view.ActiveViewport.CameraDirection,
                    sourceNormals);

                ResampleVectorBuffer(
                    sourceNormals,
                    srcSize.Width,
                    srcSize.Height,
                    outWidth,
                    outHeight,
                    3,
                    normal);

                LogMinMax("Depth", depth);
                LogMinMax("Normal", normal);

                WriteNpz(depthPath, outWidth, outHeight, 1, depth);
                WriteNpz(normalPath, outWidth, outHeight, 3, normal);

                LogVerbose($"CaptureRenderChannels: wrote '{depthPath}' and '{normalPath}'.");
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

        private static void FillDepthBuffer(
            Point3d[] worldPoints,
            bool[] validMask,
            int srcWidth,
            int srcHeight,
            int outWidth,
            int outHeight,
            Point3d cameraLocation,
            Vector3d cameraDirection,
            float[] data)
        {
            if (!cameraDirection.Unitize())
                cameraDirection = new Vector3d(0, 0, -1);

            for (var y = 0; y < outHeight; y++)
            {
                var sy = MapCoord(y, outHeight, srcHeight);
                for (var x = 0; x < outWidth; x++)
                {
                    var sx = MapCoord(x, outWidth, srcWidth);
                    var sidx = (sy * srcWidth) + sx;
                    if (validMask[sidx])
                    {
                        var ray = worldPoints[sidx] - cameraLocation;
                        var depth = ray * cameraDirection;
                        data[(y * outWidth) + x] = depth > 0.0 ? (float)depth : 0f;
                    }
                    else
                    {
                        data[(y * outWidth) + x] = 0f;
                    }
                }
            }
        }

        private static void FillSourceNormalBuffer(
            Point3d[] worldPoints,
            bool[] validMask,
            int width,
            int height,
            Vector3d cameraDirection,
            float[] data)
        {
            Array.Clear(data, 0, width * height * 3);
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

        private static void ResampleVectorBuffer(
            float[] source,
            int srcWidth,
            int srcHeight,
            int outWidth,
            int outHeight,
            int components,
            float[] output)
        {
            if (srcWidth == outWidth && srcHeight == outHeight)
            {
                Array.Copy(source, output, outWidth * outHeight * components);
                return;
            }

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

        // Write the buffer as a compressed .npz (numpy) instead of raw 32-bit PFM, so the
        // dataset is small at the source (no transient 500 GB / no post-process). The .npz
        // contains one deflate-compressed .npy entry named "depth" or "normal". Depth stays
        // float32 (lossless; avoids fp16 overflow->inf for far-background depth > 65504 cm),
        // normal is float16 (unit vectors, ~2e-4 error). Data order is C-order top-to-bottom
        // (same as the old PFM byte order), so np.load(path)["depth"|"normal"] matches the
        // image/label rasters with no flip. Downstream: np.load(path)[key].
        private static void WriteNpz(string path, int width, int height, int channels, float[] data)
        {
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);

            bool asHalf = channels == 3;                       // normal -> fp16, depth -> fp32
            string key = channels == 3 ? "normal" : "depth";
            string shape = channels == 3
                ? string.Format(CultureInfo.InvariantCulture, "({0}, {1}, {2})", height, width, channels)
                : string.Format(CultureInfo.InvariantCulture, "({0}, {1})", height, width);
            string dict = string.Format(
                CultureInfo.InvariantCulture,
                "{{'descr': '{0}', 'fortran_order': False, 'shape': {1}, }}",
                asHalf ? "<f2" : "<f4",
                shape);
            // npy v1.0: 10-byte preamble + header string; pad header so total is a multiple of 64.
            int pad = (64 - ((10 + dict.Length + 1) % 64)) % 64;
            byte[] headerBytes = Encoding.ASCII.GetBytes(dict + new string(' ', pad) + "\n");

            using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read);
            using var zip = new ZipArchive(fs, ZipArchiveMode.Create);
            var entry = zip.CreateEntry(key + ".npy", CompressionLevel.Optimal);
            using var es = entry.Open();
            using var bw = new BinaryWriter(es);
            bw.Write((byte)0x93);
            bw.Write(Encoding.ASCII.GetBytes("NUMPY"));
            bw.Write((byte)1);
            bw.Write((byte)0);
            bw.Write((ushort)headerBytes.Length);
            bw.Write(headerBytes);
            if (asHalf)
            {
                for (var i = 0; i < data.Length; i++)
                    bw.Write(BitConverter.HalfToInt16Bits((Half)data[i]));   // 2 bytes LE per value
            }
            else
            {
                for (var i = 0; i < data.Length; i++)
                    bw.Write(data[i]);                                       // 4 bytes LE per value
            }
        }

        private static void LogMinMax(string label, float[] data)
        {
            if (!VerboseLogging)
                return;
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

        private static void LogVerbose(string message)
        {
            if (VerboseLogging)
                RhinoApp.WriteLine(message);
        }
    }
}
