using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Rhino;
using Rhino.Commands;
using Rhino.Display;
using Rhino.Geometry;
using Rhino.Input;

namespace RhinoChannelsPlugin.Commands
{
    [SupportedOSPlatform("windows")]
    public sealed class CaptureBaseColorMaskCommand : Command
    {
        public override string EnglishName => "CaptureBaseColorMask";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            RhinoApp.WriteLine("CaptureBaseColorMask: command started.");

            var maskPath = string.Empty;
            var viewName = string.Empty;
            var widthToken = string.Empty;
            var heightToken = string.Empty;

            var result = RhinoGet.GetString("MaskPath", false, ref maskPath);
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

            try
            {
                var width = ParseOptionalPositiveInt(widthToken, "Width");
                var height = ParseOptionalPositiveInt(heightToken, "Height");

                var view = string.IsNullOrWhiteSpace(viewName)
                    ? doc.Views.ActiveView
                    : doc.Views.Find(viewName, false);
                if (view == null)
                    throw new InvalidOperationException("No matching view found.");

                view.Redraw();

                var srcSize = view.ActiveViewport.Size;
                if (srcSize.Width <= 0 || srcSize.Height <= 0)
                    throw new InvalidOperationException("Viewport size is invalid.");

                var outWidth = width > 0 ? width : srcSize.Width;
                var outHeight = height > 0 ? height : srcSize.Height;
                if (outWidth <= 0 || outHeight <= 0)
                    throw new InvalidOperationException("Invalid output dimensions.");

                RhinoApp.WriteLine(
                    $"CaptureBaseColorMask: using view '{view.ActiveViewport.Name}', source={srcSize.Width}x{srcSize.Height}, output={outWidth}x{outHeight}.");

                var previousMode = view.ActiveViewport.DisplayMode;
                var maskMode = FindMaskDisplayMode();
                if (maskMode != null)
                    view.ActiveViewport.DisplayMode = maskMode;

                var changedAa = TrySetOpenGlAntialiasLevel(0, out var prevAaLevel);
                try
                {
                    view.Redraw();

                    using var captured = CaptureMaskBitmap(view, outWidth, outHeight);
                    var validMask = CaptureValidMask(view.ActiveViewport, srcSize.Width, srcSize.Height);
                    var layerPalette = CollectVisibleLayerColors(doc);
                    using var quantized = QuantizeToLayerColors(
                        captured,
                        validMask,
                        srcSize.Width,
                        srcSize.Height,
                        outWidth,
                        outHeight,
                        layerPalette,
                        Color.White);

                    WritePng(maskPath, quantized);
                }
                finally
                {
                    if (changedAa)
                        TrySetOpenGlAntialiasLevel(prevAaLevel, out _);
                    view.ActiveViewport.DisplayMode = previousMode;
                    view.Redraw();
                }

                RhinoApp.WriteLine($"CaptureBaseColorMask: wrote '{maskPath}'.");
                return Result.Success;
            }
            catch (Exception ex)
            {
                RhinoApp.WriteLine($"CaptureBaseColorMask: ERROR {ex.GetType().Name}: {ex.Message}");
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

        private static Bitmap CaptureMaskBitmap(RhinoView view, int outWidth, int outHeight)
        {
            var capture = new ViewCapture
            {
                Width = outWidth,
                Height = outHeight,
                ScaleScreenItems = false,
                DrawAxes = false,
                DrawGrid = false,
                DrawGridAxes = false,
                TransparentBackground = false,
            };

            var bitmap = capture.CaptureToBitmap(view);
            if (bitmap == null)
                throw new InvalidOperationException("ViewCapture.CaptureToBitmap returned no bitmap for mask output.");

            if (bitmap.Width == outWidth && bitmap.Height == outHeight)
                return bitmap;

            using (bitmap)
            {
                return ResizeBitmap(bitmap, outWidth, outHeight);
            }
        }

        private static Bitmap ResizeBitmap(Bitmap source, int width, int height)
        {
            var resized = new Bitmap(width, height, PixelFormat.Format32bppArgb);
            using var g = Graphics.FromImage(resized);
            g.InterpolationMode = InterpolationMode.NearestNeighbor;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.SmoothingMode = SmoothingMode.None;
            g.DrawImage(source, new Rectangle(0, 0, width, height));
            return resized;
        }

        private static bool[] CaptureValidMask(RhinoViewport viewport, int width, int height)
        {
            using var zcap = new ZBufferCapture(viewport);
            zcap.ShowIsocurves(false);
            zcap.ShowMeshWires(false);
            zcap.ShowCurves(false);
            zcap.ShowPoints(false);
            zcap.ShowText(false);
            zcap.ShowAnnotations(false);
            zcap.ShowLights(false);

            var valid = new bool[width * height];
            for (var y = 0; y < height; y++)
            {
                for (var x = 0; x < width; x++)
                {
                    var idx = (y * width) + x;
                    valid[idx] = zcap.WorldPointAt(x, y).IsValid;
                }
            }
            return valid;
        }

        private static Bitmap QuantizeToLayerColors(
            Bitmap source,
            bool[] validMask,
            int srcWidth,
            int srcHeight,
            int outWidth,
            int outHeight,
            IReadOnlyList<Color> layerPalette,
            Color background)
        {
            using var sourceArgb = ConvertToArgb32(source);
            var output = new Bitmap(outWidth, outHeight, PixelFormat.Format32bppArgb);

            var rect = new Rectangle(0, 0, outWidth, outHeight);
            var srcData = sourceArgb.LockBits(rect, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
            var dstData = output.LockBits(rect, ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb);

            try
            {
                var srcStride = Math.Abs(srcData.Stride);
                var dstStride = Math.Abs(dstData.Stride);
                var srcBytes = new byte[srcStride * outHeight];
                var dstBytes = new byte[dstStride * outHeight];
                Marshal.Copy(srcData.Scan0, srcBytes, 0, srcBytes.Length);

                for (var y = 0; y < outHeight; y++)
                {
                    var sy = MapCoord(y, outHeight, srcHeight);
                    var srcRow = srcData.Stride >= 0 ? y * srcStride : (outHeight - 1 - y) * srcStride;
                    var dstRow = dstData.Stride >= 0 ? y * dstStride : (outHeight - 1 - y) * dstStride;
                    for (var x = 0; x < outWidth; x++)
                    {
                        var sx = MapCoord(x, outWidth, srcWidth);
                        var validIdx = (sy * srcWidth) + sx;
                        var dstIdx = dstRow + (x * 4);
                        Color outColor;

                        if (!validMask[validIdx])
                        {
                            outColor = background;
                        }
                        else
                        {
                            var srcIdx = srcRow + (x * 4);
                            var sample = Color.FromArgb(
                                srcBytes[srcIdx + 3],
                                srcBytes[srcIdx + 2],
                                srcBytes[srcIdx + 1],
                                srcBytes[srcIdx]);
                            outColor = FindNearestColor(sample, layerPalette);
                        }

                        dstBytes[dstIdx] = outColor.B;
                        dstBytes[dstIdx + 1] = outColor.G;
                        dstBytes[dstIdx + 2] = outColor.R;
                        dstBytes[dstIdx + 3] = 255;
                    }
                }

                Marshal.Copy(dstBytes, 0, dstData.Scan0, dstBytes.Length);
            }
            finally
            {
                sourceArgb.UnlockBits(srcData);
                output.UnlockBits(dstData);
            }

            return output;
        }

        private static Bitmap ConvertToArgb32(Bitmap bitmap)
        {
            if (bitmap.PixelFormat == PixelFormat.Format32bppArgb &&
                bitmap.Width > 0 &&
                bitmap.Height > 0)
            {
                return (Bitmap)bitmap.Clone();
            }

            var converted = new Bitmap(bitmap.Width, bitmap.Height, PixelFormat.Format32bppArgb);
            using var g = Graphics.FromImage(converted);
            g.DrawImage(bitmap, new Rectangle(0, 0, bitmap.Width, bitmap.Height));
            return converted;
        }

        private static Color FindNearestColor(Color sample, IReadOnlyList<Color> palette)
        {
            if (palette.Count == 0)
                return sample;

            var best = palette[0];
            var bestDist = ColorDistanceSquared(sample, best);
            for (var i = 1; i < palette.Count; i++)
            {
                var candidate = palette[i];
                var dist = ColorDistanceSquared(sample, candidate);
                if (dist < bestDist)
                {
                    bestDist = dist;
                    best = candidate;
                }
            }

            return best;
        }

        private static int ColorDistanceSquared(Color a, Color b)
        {
            var dr = a.R - b.R;
            var dg = a.G - b.G;
            var db = a.B - b.B;
            return (dr * dr) + (dg * dg) + (db * db);
        }

        private static List<Color> CollectVisibleLayerColors(RhinoDoc doc)
        {
            var colors = new List<Color>();
            var seen = new HashSet<int>();

            foreach (var layer in doc.Layers)
            {
                if (layer == null || layer.IsDeleted || !layer.IsVisible)
                    continue;

                var color = layer.Color;
                var argb = color.ToArgb();
                if (seen.Add(argb))
                    colors.Add(color);
            }

            return colors;
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

        private static void WritePng(string path, Bitmap bitmap)
        {
            var dir = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(dir))
                dir = ".";
            Directory.CreateDirectory(dir);
            bitmap.Save(path, ImageFormat.Png);
        }

        private static DisplayModeDescription? FindMaskDisplayMode()
        {
            return DisplayModeDescription.FindByName("Flat Shade")
                   ?? DisplayModeDescription.FindByName("Base Color");
        }

        private static bool TrySetOpenGlAntialiasLevel(int level, out int previousLevel)
        {
            previousLevel = 0;

            try
            {
                var oglType = typeof(Rhino.ApplicationSettings.OpenGLSettings);
                var prop = oglType.GetProperty("AntialiasLevel", BindingFlags.Public | BindingFlags.Static);
                if (prop == null || !prop.CanRead || !prop.CanWrite)
                    return false;

                var currentRaw = prop.GetValue(null, null);
                if (currentRaw == null)
                    return false;

                previousLevel = Convert.ToInt32(currentRaw, CultureInfo.InvariantCulture);
                if (previousLevel == level)
                    return false;

                var targetType = Nullable.GetUnderlyingType(prop.PropertyType) ?? prop.PropertyType;
                object coerced;
                if (targetType.IsEnum)
                {
                    coerced = Enum.ToObject(targetType, level);
                }
                else
                {
                    coerced = Convert.ChangeType(level, targetType, CultureInfo.InvariantCulture) ?? level;
                }

                prop.SetValue(null, coerced, null);
                return true;
            }
            catch
            {
                return false;
            }
        }
    }
}
